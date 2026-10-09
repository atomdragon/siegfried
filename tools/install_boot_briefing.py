#!/usr/bin/env python3
"""Explicit XDG Autostart management. Defaults to dry-run; never starts services."""
import argparse
import os
from pathlib import Path
import stat
import shutil
import sys
import uuid

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / 'src'))
from siegfried.integrations.briefing_files import directory_fd
from siegfried.integrations.briefing_kde import trusted_executable

ENTRY = 'org.siegfried.BootBriefing.desktop'
OWNER_TAG = 'X-Siegfried-Gate=F5.3'


def exec_quote(value):
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        raise ValueError('unsafe_exec_argument')
    value = value.replace('\\', '\\\\\\\\').replace('%', '%%')
    for char in ('"', '`', '$'):
        value = value.replace(char, '\\\\' + char)
    return '"' + value + '"'


def desktop_content(repository=REPO_ROOT, *, enabled=True, weather=False, show_task=False, planned=False):
    hook = Path(repository) / 'scripts' / 'boot_hook.py'
    if not trusted_executable(sys.executable) or (not planned and not trusted_executable(hook)):
        raise ValueError('unsafe_boot_executable')
    argv = [sys.executable, str(hook)]
    if weather:
        argv.append('--weather')
    if show_task:
        argv.append('--show-task')
    return ('[Desktop Entry]\nType=Application\nVersion=1.0\nName=Siegfried\n'
            'Comment=Briefing determinista de inicio\nIcon=dialog-information\n'
            f'Exec={" ".join(exec_quote(arg) for arg in argv)}\nTryExec={sys.executable}\n'
            f'OnlyShowIn=KDE;\nTerminal=false\nStartupNotify=false\nHidden={str(not enabled).lower()}\n'
            f'{OWNER_TAG}\n')


def package_sources(repository):
    root = Path(repository)
    files = [root / 'scripts/boot_hook.py', root / 'bin/siegfried']
    files.extend(sorted((root / 'src/siegfried').rglob('*.py')))
    result = {}
    for source in files:
        if source.is_symlink() or any(p.is_symlink() for p in source.parents if p != root.parent):
            raise ValueError('unsafe_package_source')
        parent = directory_fd(source.parent)
        fd = None
        try:
            fd = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid not in (0, os.getuid()) or st.st_mode & 0o002 or st.st_size > 1048576:
                raise ValueError('unsafe_package_source')
            data = os.read(fd, 1048577)
            if len(data) > 1048576:
                raise ValueError('oversized_package_source')
            result[str(source.relative_to(root))] = data
        finally:
            if fd is not None:
                os.close(fd)
            os.close(parent)
    return result


def prepare_package(home, repository, dry_run):
    """Private code snapshot, not a new domain data source or another daemon."""
    target = home / '.local/share/siegfried-boot'
    sources = package_sources(repository)
    if dry_run:
        return target
    # mkdir/open each parent without following symlinks.
    parent = directory_fd(home)
    try:
        for component in ('.local', 'share'):
            try:
                os.mkdir(component, 0o700, dir_fd=parent)
            except FileExistsError:
                pass
            nxt = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            st = os.fstat(nxt)
            if st.st_uid != os.getuid() or st.st_mode & 0o022:
                os.close(nxt)
                raise ValueError('unsafe_package_directory')
            os.close(parent)
            parent = nxt
        if target.exists() or target.is_symlink():
            fd = directory_fd(target, private=True)
            os.close(fd)
            for relative, expected in {**sources, '.siegfried-owned':b'F5.3\n'}.items():
                path = target / relative
                if (path.is_symlink() or not path.is_file() or path.stat().st_size != len(expected)
                        or path.stat().st_uid != os.getuid() or path.stat().st_nlink != 1 or
                        path.stat().st_mode & 0o022 or path.read_bytes() != expected):
                    raise ValueError('package_content_differs')
                fd = directory_fd(path.parent)
                os.close(fd)
            return target
        temp = '.siegfried-boot-' + uuid.uuid4().hex
        os.mkdir(temp, 0o700, dir_fd=parent)
        staging = target.parent / temp
        try:
            for relative, data in {**sources, '.siegfried-owned':b'F5.3\n'}.items():
                dest = staging / relative
                current = staging
                for part in Path(relative).parts[:-1]:
                    current = current / part
                    current.mkdir(mode=0o700, exist_ok=True)
                fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o700 if relative in ('scripts/boot_hook.py','bin/siegfried') else 0o600)
                try:
                    os.write(fd, data)
                    os.fsync(fd)
                finally:
                    os.close(fd)
            os.rename(temp, target.name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return target
    finally:
        os.close(parent)


def _validate_home(home):
    home = Path(home)
    if not home.is_absolute() or '..' in home.parts:
        raise ValueError('unsafe_home')
    fd = directory_fd(home)
    try:
        st = os.fstat(fd)
        if st.st_uid != os.getuid() or st.st_mode & 0o022:
            raise ValueError('insecure_home')
    finally:
        os.close(fd)
    # Validate existing descendants without creating anything in dry-run.
    for path in (home / '.config', home / '.config/autostart',
                 home / '.local', home / '.local/share'):
        if path.exists() or path.is_symlink():
            fd = directory_fd(path)
            try:
                st = os.fstat(fd)
                if st.st_uid != os.getuid() or st.st_mode & 0o022:
                    raise ValueError('insecure_autostart_directory')
            finally:
                os.close(fd)
    return home


def remove_package(home, repository):
    target = home / '.local/share/siegfried-boot'
    if not target.exists() and not target.is_symlink():
        return
    fd = directory_fd(target, private=True)
    os.close(fd)
    marker = target / '.siegfried-owned'
    if marker.is_symlink() or marker.read_bytes() != b'F5.3\n':
        raise ValueError('foreign_boot_package')
    allowed = set(package_sources(repository)) | {'.siegfried-owned'}
    for path in target.rglob('*'):
        st = path.lstat()
        if path.is_symlink() or st.st_uid != os.getuid() or st.st_mode & 0o022:
            raise ValueError('unsafe_package_contents')
        if path.is_file() and str(path.relative_to(target)) not in allowed:
            if path.parent.name != '__pycache__' or path.suffix != '.pyc':
                raise ValueError('foreign_package_contents')
    shutil.rmtree(target)  # Only this private, marked code package; never runtime data.


def manage_autostart(home, *, operation='install', dry_run=True, repository=REPO_ROOT,
                     weather=False, show_task=False):
    if operation not in ('install', 'enable', 'disable', 'uninstall'):
        raise ValueError('invalid_operation')
    home = _validate_home(home)
    package = home / '.local/share/siegfried-boot'
    content = desktop_content(package, weather=weather, show_task=show_task, planned=True)
    destination = home / '.config' / 'autostart' / ENTRY
    if dry_run:
        prepare_package(home, repository, True)
        return {'dry_run':True, 'destination':str(destination), 'operation':operation, 'content':content}
    if not destination.exists() and not destination.is_symlink():
        if operation == 'uninstall':
            # Publication can fail after the private snapshot was prepared.
            # Reuse the ownership/content checks even when no entry exists.
            package_exists = package.exists() or package.is_symlink()
            remove_package(home, repository)
            return {'removed':False, 'package_removed':package_exists}
        if operation in ('enable', 'disable'):
            raise ValueError('autostart_not_installed')
    root = directory_fd(home)
    config = directory = None
    try:
        for name in ('.config',):
            try:
                os.mkdir(name, 0o700, dir_fd=root)
            except FileExistsError:
                pass
        config = os.open('.config', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        try:
            os.mkdir('autostart', 0o700, dir_fd=config)
        except FileExistsError:
            pass
        directory = os.open('autostart', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=config)
        existing = None
        try:
            fd = os.open(ENTRY, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            pass
        else:
            try:
                st = os.fstat(fd)
                if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or
                        st.st_nlink != 1 or st.st_mode & 0o077 or st.st_size > 4096):
                    raise ValueError('unsafe_autostart_entry')
                existing = os.read(fd, 4097).decode('utf-8')
                if OWNER_TAG not in existing.splitlines():
                    raise ValueError('foreign_autostart_entry')
            finally:
                os.close(fd)
        if operation == 'uninstall':
            remove_package(home, repository)
            if existing is not None:
                os.unlink(ENTRY, dir_fd=directory)
                os.fsync(directory)
            return {'removed':existing is not None}
        if operation in ('enable', 'disable'):
            if existing is None:
                raise ValueError('autostart_not_installed')
            content = existing.replace('Hidden=true\n', 'Hidden=false\n') if operation == 'enable' else existing.replace('Hidden=false\n', 'Hidden=true\n')
        elif existing is not None and existing != content:
            raise ValueError('autostart_options_differ')
        if operation != 'uninstall':
            prepare_package(home, repository, False)
        if existing == content:
            return {'unchanged':True}
        temp = '.' + ENTRY + '.' + uuid.uuid4().hex
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                os.write(fd, content.encode('utf-8'))
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(temp, ENTRY, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                os.unlink(temp, dir_fd=directory)
            except FileNotFoundError:
                pass
        return {'installed':True, 'destination':str(destination)}
    finally:
        for fd in (directory, config, root):
            if fd is not None:
                os.close(fd)


def main():
    parser = argparse.ArgumentParser(description='Preparación explícita de XDG Autostart KDE')
    parser.add_argument('--home', type=Path, default=Path.home())
    parser.add_argument('--operation', choices=('install','enable','disable','uninstall'), default='install')
    parser.add_argument('--apply', action='store_true', help='Escribir sólo la entrada de autoinicio de Siegfried')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--weather', action='store_true')
    parser.add_argument('--show-task', action='store_true')
    args = parser.parse_args()
    try:
        result = manage_autostart(args.home, operation=args.operation, dry_run=not args.apply or args.dry_run,
                                 weather=args.weather, show_task=args.show_task)
        if result.get('dry_run'):
            print(result['content'], end='')
            print('DRY-RUN: no se modificó Autostart.')
        else:
            print('Operación de Autostart completada.')
        return 0
    except (OSError, ValueError):
        print('Autostart rechazado: ruta, permisos o entrada no autorizados.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
