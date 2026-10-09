#!/usr/bin/python3
"""Run tests with temporary HOME/XDG and inherited Linux filesystem protection.

Fail closed without Landlock ABI >= 3. No production modules are imported until
protection is installed. Landlock guards file I/O even for cached absolute paths
and subprocesses; the Python audit hook also rejects metadata mutations. This
is a regression harness for trusted tests, not a sandbox for hostile programs.
"""
import ctypes
import contextlib
import json
import os
from pathlib import Path
import platform
import pwd
import runpy
import subprocess
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[1]


def restrict_filesystem(blocked):
    if platform.machine() not in ('x86_64', 'aarch64'):
        raise RuntimeError('unsupported Landlock syscall architecture')
    libc = ctypes.CDLL(None, use_errno=True)
    abi = libc.syscall(444, 0, 0, 1)
    if abi < 3:
        raise RuntimeError('Landlock ABI >= 3 required; refusing unprotected tests')
    rights = (1 << (16 if abi >= 5 else 15)) - 1
    ruleset = ctypes.c_uint64(rights)
    fd = libc.syscall(444, ctypes.byref(ruleset), 8, 0)
    if fd < 0:
        raise OSError(ctypes.get_errno(), 'Landlock create_ruleset')

    class Beneath(ctypes.Structure):
        _layout_ = 'ms'
        _pack_ = 1
        _fields_ = [('access', ctypes.c_uint64), ('parent', ctypes.c_int32)]

    def grant(path):
        canonical = os.path.realpath(path)
        if any(canonical == b or canonical.startswith(b + '/') for b in blocked):
            return
        if any(b.startswith(canonical.rstrip('/') + '/') for b in blocked):
            if os.path.isdir(path):
                parent = os.open(path, os.O_PATH | os.O_CLOEXEC)
                try:
                    rule = Beneath(1 << 3, parent)
                    if libc.syscall(445, fd, 1, ctypes.byref(rule), 0) < 0:
                        raise OSError(ctypes.get_errno(), 'Landlock ancestor read_dir')
                finally:
                    os.close(parent)
                for entry in os.scandir(path):
                    grant(entry.path)
            return
        parent = os.open(path, os.O_PATH | os.O_CLOEXEC)
        try:
            allowed = rights if os.path.isdir(path) else rights & ((1 << 0) | (1 << 1) | (1 << 2) | (1 << 14) | (1 << 15))
            rule = Beneath(allowed, parent)
            if libc.syscall(445, fd, 1, ctypes.byref(rule), 0) < 0:
                raise OSError(ctypes.get_errno(), 'Landlock add_rule')
        finally:
            os.close(parent)

    try:
        grant('/')
        if libc.prctl(38, 1, 0, 0, 0) != 0 or libc.syscall(446, fd, 0) != 0:
            raise OSError(ctypes.get_errno(), 'Landlock restrict_self')
    finally:
        os.close(fd)
    return abi


def self_test(blocked, captured):
    # Values captured BEFORE installing policy/env changes cannot bypass it.
    denied = []
    for label, action in (
        ('cached_read', lambda: captured.read_bytes()),
        ('cached_append', lambda: captured.open('ab')),
        ('cached_truncate', lambda: captured.open('wb')),
        ('cached_directory_listing', lambda: list(captured.parent.iterdir())),
        ('cached_chmod', lambda: captured.chmod(0o600)),
        ('cached_utime', lambda: os.utime(captured, None)),
        ('cached_unlink', lambda: captured.unlink()),
    ):
        try:
            action()
        except PermissionError:
            denied.append(label)
        else:
            raise AssertionError('isolation failed: ' + label)
    child = subprocess.run([sys.executable, '-S', '-c',
        'import sys; open(sys.argv[1], "rb").read()', str(captured)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if child.returncode == 0 or b'PermissionError' not in child.stderr:
        raise AssertionError('child kernel isolation failed')
    target = Path.home() / 'allowed.txt'
    target.write_text('synthetic fixture')
    assert target.read_text() == 'synthetic fixture'
    # This real target is only opened after policy; no content/metadata is read.
    for root in blocked[:2]:
        try:
            list(Path(root).iterdir())
        except PermissionError:
            pass
        else:
            raise AssertionError('real directory readable')
    print(json.dumps({'guard': 'PASS', 'denied': denied,
                      'child_without_site_hook_denied': True,
                      'temporary_home_writable': True}), flush=True)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--child':
        blocked = json.loads(os.environ['SIEGFRIED_TEST_BLOCKED_PATHS'])
        # Model a module-level absolute path captured before sandbox activation.
        cached_module = types.ModuleType('cached_path_fixture')
        exec('from pathlib import Path\nCACHED = Path(' + repr(os.environ['SIEGFRIED_TEST_CAPTURED_PATH']) + ')', cached_module.__dict__)
        captured = cached_module.CACHED
        abi = restrict_filesystem(blocked)
        print('Isolation: Landlock ABI %s; temporary HOME/XDG; real HOME/runtime denied' % abi, flush=True)
        args = sys.argv[2:]
        if args == ['--self-test']:
            self_test(blocked, captured)
            return
        from siegfried.storage.initialization import ensure_user_runtime
        from siegfried.storage.paths import SiegfriedPaths
        ensure_user_runtime(SiegfriedPaths())
        sys.argv = [args[0], *args[1:]]
        if args[0] == '-m':
            sys.argv = [args[1], *args[2:]]
            runpy.run_module(args[1], run_name='__main__', alter_sys=True)
        else:
            runpy.run_path(args[0], run_name='__main__')
        return
    args = sys.argv[1:]
    if args and args[0] == '--':
        args = args[1:]
    if not args:
        args = ['-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py']
    actual_home = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
    real_runtime = '/run/user/' + str(os.getuid())
    with contextlib.ExitStack() as stack:
        temporary = stack.enter_context(tempfile.TemporaryDirectory(prefix='siegfried-isolated-tests-'))
        base = Path(temporary)
        inherited = json.loads(os.environ.get('SIEGFRIED_TEST_BLOCKED_PATHS', '[]'))
        if inherited:
            captured = Path(os.environ['SIEGFRIED_TEST_CAPTURED_PATH'])
            forbidden = captured.parent
        else:
            forbidden = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='siegfried-isolation-probe-', dir='/var/tmp')))
            captured = forbidden / 'captured.txt'
            captured.write_text('synthetic cached data')
        home = base / 'home'
        home.mkdir(mode=0o700)
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8',
               'HOME': str(home), 'SIEGFRIED_HOME': str(home / '.siegfried'),
               'SIEGFRIED_ENABLE_KWIN': '0', 'SIEGFRIED_ENABLE_SESSION': '0',
               'PYTHONDONTWRITEBYTECODE': '1',
               'SIEGFRIED_TEST_BLOCKED_PATHS': json.dumps(list(dict.fromkeys([actual_home, real_runtime, str(forbidden), *inherited]))),
               'SIEGFRIED_TEST_CAPTURED_PATH': str(captured),
               'PYTHONPATH': os.pathsep.join([str(ROOT / 'tools/test_isolation'), str(ROOT / 'src'), str(ROOT)]),
               'SYSTEMD_UNIT_PATH': '/usr/lib/systemd/user:/lib/systemd/user'}
        for variable, suffix in [('XDG_RUNTIME_DIR', 'run'), ('XDG_CONFIG_HOME', '.config'),
                                 ('XDG_DATA_HOME', '.local/share'), ('XDG_STATE_HOME', '.local/state'),
                                 ('XDG_CACHE_HOME', '.cache'), ('TMPDIR', 'tmp')]:
            directory = (base if variable == 'TMPDIR' else home) / suffix
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            env[variable] = str(directory)
        result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--child', *args],
                                cwd=ROOT, env=env, close_fds=True)
        raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
