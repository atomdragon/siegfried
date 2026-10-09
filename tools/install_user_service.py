#!/usr/bin/env python3
"""Private daemon/CLI deployment; never starts or enables a real service.

Reuses the briefing snapshot machinery with an independent owned package, so
uninstalling a briefing cannot remove the daemon. Foreign files are preserved.
"""
import argparse
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import uuid

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from tools.install_boot_briefing import (
    _validate_home, directory_fd, prepare_package, remove_package,
)

OWNER_TAG = '# X-Siegfried-Owner=F5.5'


def wrapper_content(name):
    # The wrapper resolves only the installed private package relative to HOME.
    # No PYTHONPATH, development checkout or shell interpolation is needed.
    return ("#!/usr/bin/env python3\n" + OWNER_TAG + "\n"
            "import os, sys\nfrom pathlib import Path\n"
            "package = Path(__file__).absolute().parents[2] / '.local/share/siegfried-daemon'\n"
            f"os.execv(sys.executable, [sys.executable, '-B', str(package / 'bin/{name}'), *sys.argv[1:]])\n").encode()


def deployment_files(home):
    unit = (REPO_ROOT / 'systemd/siegfried.service').read_bytes()
    if b'/media/' in unit or b'%h/.local/share/siegfried-daemon/bin/siegfried-daemon' not in unit:
        raise ValueError('unsafe_service_template')
    return {
        home / '.siegfried/bin/siegfried': (wrapper_content('siegfried'), 0o700),
        home / '.siegfried/bin/siegfried-daemon': (wrapper_content('siegfried-daemon'), 0o700),
        home / '.config/systemd/user/siegfried.service': (unit, 0o600),
    }


def inspect_file(path, expected, mode):
    parent = directory_fd(path.parent)
    fd = None
    try:
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        except FileNotFoundError:
            return False
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or
                st.st_nlink != 1 or stat.S_IMODE(st.st_mode) != mode or
                st.st_size != len(expected) or os.read(fd, len(expected) + 1) != expected):
            raise ValueError('foreign_or_modified_deployment_file')
        return True
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def preflight(home, files):
    _validate_home(home)
    for relative in ('.siegfried', '.siegfried/bin', '.config/systemd', '.config/systemd/user'):
        path = home / relative
        if not path.exists() and not path.is_symlink():
            continue
        fd = directory_fd(path)
        try:
            st = os.fstat(fd)
            if st.st_uid != os.getuid() or st.st_mode & 0o022:
                raise ValueError('unsafe_install_directory')
            if relative.startswith('.siegfried') and stat.S_IMODE(st.st_mode) != 0o700:
                raise ValueError('insecure_runtime_directory')
        finally:
            os.close(fd)
    existing = {}
    for path, (content, mode) in files.items():
        if path.parent.exists():
            existing[path] = inspect_file(path, content, mode)
        else:
            existing[path] = False
    package = home / '.local/share/siegfried-daemon'
    if package.exists() or package.is_symlink():
        # Existing snapshot: content validation is idempotent and writes nothing.
        prepare_package(home, REPO_ROOT, False, daemon=True)
        remove_package(home, REPO_ROOT, daemon=True, check_only=True)
    return existing


def ensure_directory(home, relative):
    fd = directory_fd(home)
    try:
        for part in Path(relative).parts:
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            st = os.fstat(nxt)
            if st.st_uid != os.getuid() or st.st_mode & 0o022:
                os.close(nxt)
                raise ValueError('unsafe_install_directory')
            os.close(fd)
            fd = nxt
    finally:
        os.close(fd)


def publish(path, content, mode):
    parent = directory_fd(path.parent)
    temp = '.' + path.name + '.' + uuid.uuid4().hex
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            # Atomic publication that cannot overwrite a concurrently added file.
            os.link(temp, path.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
        except FileExistsError:
            inspect_file(path, content, mode)
        finally:
            os.unlink(temp, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)


def verify_unit(home, content):
    # systemd's %h uses the real account, even with a temporary HOME env.
    # Verify identical syntax with only the fixture's HOME specifier substituted.
    escaped = str(home).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    with tempfile.TemporaryDirectory(prefix='siegfried-unit-verify-') as tmp:
        unit = Path(tmp) / 'siegfried.service'
        unit.write_bytes(content.replace(b'%h', escaped.encode()))
        result = subprocess.run(['systemd-analyze', '--user', 'verify', str(unit)],
                                capture_output=True, timeout=10)
        if result.returncode:
            raise ValueError('systemd_unit_verification_failed')


def install_user_service(target_home=None, dry_run=False, verify=True, *, operation='install'):
    home = Path(target_home or Path.home())
    if operation not in ('install', 'uninstall'):
        raise ValueError('invalid_operation')
    files = deployment_files(home)
    existing = preflight(home, files)
    if dry_run:
        prepare_package(home, REPO_ROOT, True, daemon=True)
        print('DRY-RUN: snapshot privado, dos wrappers propios y unidad; sin activación.')
        return 0
    if operation == 'uninstall':
        # Full preflight precedes every deletion; no runtime data is in files.
        remove_package(home, REPO_ROOT, daemon=True, check_only=True)
        for path, present in existing.items():
            if present:
                parent = directory_fd(path.parent)
                try:
                    inspect_file(path, *files[path])
                    os.unlink(path.name, dir_fd=parent)
                    os.fsync(parent)
                finally:
                    os.close(parent)
        remove_package(home, REPO_ROOT, daemon=True)
        print('Desinstalación propia completada; configuración, historial y Vault preservados.')
        return 0
    package = prepare_package(home, REPO_ROOT, False, daemon=True)
    if verify:
        verify_unit(home, files[home / '.config/systemd/user/siegfried.service'][0])
    for path, (content, mode) in files.items():
        ensure_directory(home, path.parent.relative_to(home))
        if not existing[path]:
            publish(path, content, mode)
    print(f'Instalación privada verificada: {package}; servicio sin activar.')
    return 0


def main():
    parser = argparse.ArgumentParser(description='Despliegue privado explícito de daemon y REPL')
    parser.add_argument('--home', type=Path, default=Path.home())
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--no-verify', action='store_true')
    parser.add_argument('--operation', choices=('install', 'uninstall'), default='install')
    args = parser.parse_args()
    try:
        return install_user_service(args.home, dry_run=not args.apply or args.dry_run,
                                    verify=not args.no_verify, operation=args.operation)
    except (OSError, ValueError, subprocess.SubprocessError):
        print('Despliegue rechazado: ruta, permisos, integridad o verificación inseguros.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
