#!/usr/bin/env python3
"""F5.5 exact-cache cleanup proposal. Read-only unless --apply is explicit.

Production --apply requires separate user authorization. This helper never
changes sources, XDG, services or runtime data. Refuses a changed inventory.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

FIELDS = ('st_dev', 'st_ino', 'st_uid', 'st_gid', 'st_mode', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')


def directory(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('unsafe_path')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            st = os.stat(part, dir_fd=fd, follow_symlinks=False)
            noatime = os.O_NOATIME if st.st_uid == os.getuid() else 0
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | noatime, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def metadata(st):
    return {field: getattr(st, field) for field in FIELDS}


def regular(parent, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK, dir_fd=parent)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_nlink != 1 or st.st_mode & 0o022 or st.st_size > 1048576:
            raise ValueError('unsafe_file')
        chunks = []
        while chunk := os.read(fd, 65536):
            chunks.append(chunk)
        if metadata(st) != metadata(os.fstat(fd)):
            raise ValueError('concurrent_file_change')
        raw = b''.join(chunks)
        return {**metadata(st), 'sha256': hashlib.sha256(raw).hexdigest()}, raw
    finally:
        os.close(fd)


def walk(rootfd):
    files, dirs, contents = {}, {}, {}
    def visit(fd, prefix):
        for name in sorted(os.listdir(fd)):
            relative = prefix + name
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if st.st_uid != os.getuid() or st.st_dev != os.fstat(rootfd).st_dev:
                raise ValueError('foreign_owner_or_mount')
            if stat.S_ISDIR(st.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME, dir_fd=fd)
                try:
                    if metadata(st) != metadata(os.fstat(child)):
                        raise ValueError('directory_changed')
                    dirs[relative] = metadata(st)
                    visit(child, relative + '/')
                finally:
                    os.close(child)
            elif stat.S_ISREG(st.st_mode):
                files[relative], contents[relative] = regular(fd, name)
            else:
                raise ValueError('unknown_type_or_symlink')
    visit(rootfd, '')
    return files, dirs, contents


def hooks():
    found = []
    for path in Path('/proc').iterdir():
        if path.name.isdigit():
            try:
                if path.stat().st_uid != os.getuid():
                    continue
                args = (path/'cmdline').read_bytes().split(b'\0')
                if any(x == b'boot_hook.py' or x.endswith(b'/boot_hook.py') for x in args):
                    found.append(int(path.name))
            except FileNotFoundError:
                continue
            except PermissionError:
                raise ValueError('process_visibility_incomplete')
    if found:
        raise ValueError('active_boot_hook')


def validate(root, manifest):
    if str(root) != manifest['root'] or not root.is_absolute():
        raise ValueError('root_differs')
    hooks()
    fd = directory(root)
    try:
        st = os.fstat(fd)
        if (st.st_dev, st.st_ino, st.st_uid, st.st_gid, stat.S_IMODE(st.st_mode)) != tuple(manifest['root_identity']):
            raise ValueError('snapshot_identity_changed')
        files, dirs, contents = walk(fd)
        sources = manifest['allowed_source_hashes']
        cache_files, cache_dirs = manifest['cache_files'], manifest['cache_directories']
        if set(files) != set(sources) | set(cache_files):
            raise ValueError('unknown_or_missing_file')
        if set(dirs) != set(manifest['source_directories']) | set(cache_dirs):
            raise ValueError('unknown_or_missing_directory')
        for rel, allowed in sources.items():
            if files[rel]['st_gid'] != manifest['root_identity'][3]:
                raise ValueError('source_group_changed')
            if files[rel]['sha256'] not in allowed:
                raise ValueError('source_changed')
            mode = 0o700 if rel in ('bin/siegfried', 'scripts/boot_hook.py') else 0o600
            if stat.S_IMODE(files[rel]['st_mode']) != mode:
                raise ValueError('source_permissions_changed')
        for rel, expected in manifest['source_directories'].items():
            current = dirs[rel]
            for field in ('st_dev','st_ino','st_uid','st_gid','st_mode'):
                if current[field] != expected[field]:
                    raise ValueError('source_directory_changed')
        for rel, expected in cache_files.items():
            if files[rel] != expected or '/__pycache__/' not in rel or not rel.endswith('.pyc'):
                raise ValueError('cache_changed')
        for rel, expected in cache_dirs.items():
            if dirs[rel] != expected or Path(rel).name != '__pycache__':
                raise ValueError('cache_directory_changed')
        return fd, files, dirs, contents
    except BaseException:
        os.close(fd)
        raise


def parent_fd(rootfd, relative):
    parts = Path(relative).parts
    if any(part in ('.','..') for part in parts) or Path(relative).is_absolute():
        raise ValueError('unsafe_relative')
    fd = os.dup(rootfd)
    try:
        for part in parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise


def apply(rootfd, manifest, contents, backup):
    # Backup publication is exclusive, in a private directory outside the package/runtime.
    backup = Path(backup)
    root = Path(manifest['root'])
    if not backup.is_absolute() or backup.is_relative_to(root) or backup.is_relative_to(Path.home()/'.siegfried'):
        raise ValueError('unsafe_backup_location')
    parent = directory(backup.parent)
    try:
        pst = os.fstat(parent)
        if pst.st_uid != os.getuid() or stat.S_IMODE(pst.st_mode) != 0o700:
            raise ValueError('insecure_backup_directory')
        raw = json.dumps({'manifest':manifest, 'bytecode_base64':{p:base64.b64encode(contents[p]).decode() for p in manifest['cache_files']}},indent=2).encode()
        out = os.open(backup.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            view = memoryview(raw)
            while view:
                view = view[os.write(out, view):]
            os.fsync(out)
        finally:
            os.close(out)
        os.fsync(parent)
    finally:
        os.close(parent)
    # Check everything again after backup and before the first removal.
    confirm, _, _, _ = validate(root, manifest)
    os.close(confirm)
    for rel, expected in manifest['cache_files'].items():
        hooks()
        parent, name = parent_fd(rootfd, rel)
        try:
            actual, _ = regular(parent, name)
            if actual != expected or metadata(os.stat(name, dir_fd=parent, follow_symlinks=False)) != {k:expected[k] for k in FIELDS}:
                raise ValueError('cache_changed_before_unlink')
            os.unlink(name, dir_fd=parent)
            os.fsync(parent)
        finally:
            os.close(parent)
    for rel in sorted(manifest['cache_directories'], key=lambda x: len(Path(x).parts), reverse=True):
        hooks()
        parent, name = parent_fd(rootfd, rel)
        try:
            st = os.stat(name, dir_fd=parent, follow_symlinks=False)
            expected = manifest['cache_directories'][rel]
            if not stat.S_ISDIR(st.st_mode) or any(getattr(st,k) != expected[k] for k in ('st_dev','st_ino','st_uid','st_gid','st_mode')):
                raise ValueError('cache_directory_replaced')
            os.rmdir(name, dir_fd=parent)  # Fails if any new/unknown entry exists.
            os.fsync(parent)
        finally:
            os.close(parent)
    remaining, dirs, _ = walk(rootfd)
    if set(remaining) != set(manifest['allowed_source_hashes']) or set(dirs) != set(manifest['source_directories']):
        raise ValueError('postcleanup_inventory_differs')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--root',type=Path,default=Path.home()/'.local/share/siegfried-boot')
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--backup',type=Path)
    args = parser.parse_args()
    if args.apply and not args.backup:
        parser.error('--apply needs --backup; separate production authorization is required')
    parent = directory(args.manifest.absolute().parent)
    try:
        _, raw = regular(parent,args.manifest.name)
        manifest = json.loads(raw)
    finally:
        os.close(parent)
    fd, _, _, contents = validate(args.root,manifest)
    try:
        if args.apply:
            apply(fd,manifest,contents,args.backup)
        print(json.dumps({'status':'PASS','applied':args.apply,'cache_files':len(manifest['cache_files']),'cache_directories':len(manifest['cache_directories']),'sources_or_runtime_touched':False}))
    finally:
        os.close(fd)


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status':'REJECTED','reason':type(exc).__name__,'detail':str(exc) if isinstance(exc,ValueError) else 'filesystem_or_validation_error'}))
        sys.exit(1)
