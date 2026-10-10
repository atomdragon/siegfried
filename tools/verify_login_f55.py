#!/usr/bin/env python3
"""Read-only F5.5 login diagnostics. Never starts/stops services or runs the hook.

--capture-reference prints sensitive fingerprints: redirect ONLY to a new 0600
file outside the runtime. Normal output contains no private contents or hashes.
Exit 0: technical prechecks pass; 1: actual failed check; 2: missing evidence.
Neither exit 0 nor SENT certifies the final gate or human notification visibility.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import time
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
HOME = Path.home()
BASE = HOME / '.siegfried'
RUN = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'))
UNIT = 'siegfried.service'


def read(path, limit=32 * 1024 * 1024):
    """No symlink traversal, no atime updates; fail rather than relax safety."""
    path = Path(path)
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        datafd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK, dir_fd=fd)
        try:
            before = os.fstat(datafd)
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise ValueError('unsafe_file')
            chunks = []
            size = 0
            while True:
                chunk = os.read(datafd, min(65536, limit + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > limit:
                    raise ValueError('oversized_file')
            after = os.fstat(datafd)
            if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError('concurrent_change')
            return b''.join(chunks)
        finally:
            os.close(datafd)
    finally:
        os.close(fd)


def command(argv):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=10,
                       env={**os.environ, 'LC_ALL': 'C', 'PYTHONDONTWRITEBYTECODE': '1'})
    if p.returncode:
        raise RuntimeError('command_unavailable')
    return p.stdout


def service():
    fields = ['LoadState', 'ActiveState', 'SubState', 'UnitFileState', 'MainPID',
              'NRestarts', 'Result', 'InvocationID', 'ControlGroup',
              'ExecMainStartTimestampMonotonic', 'ExecMainStartTimestamp', 'DropInPaths']
    return dict(line.split('=', 1) for line in command(
        ['systemctl', '--user', 'show', UNIT, *['--property='+x for x in fields]]).splitlines() if '=' in line)


def session():
    sid = os.environ.get('XDG_SESSION_ID', '')
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    safe_sid = sid if re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', sid) else 'unknown-session'
    key = hashlib.sha256((boot+'\0'+safe_sid+'\0'+os.environ.get('DBUS_SESSION_BUS_ADDRESS', '')).encode()).hexdigest()
    result = {'boot_id': boot, 'id': safe_sid, 'key': key}
    if safe_sid != 'unknown-session':
        raw = command(['loginctl', 'show-session', safe_sid, '-p', 'TimestampMonotonic', '-p', 'Type', '-p', 'Active'])
        result.update(dict(x.split('=', 1) for x in raw.splitlines() if '=' in x))
    return result


def inventory():
    result = {}
    for path in sorted(BASE.rglob('*')):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError('runtime_symlink')
        if stat.S_ISDIR(info.st_mode):
            result[str(path.relative_to(BASE))+'/'] = {k: getattr(info, 'st_'+k) for k in ['uid', 'gid', 'mode', 'ino', 'dev']}
        elif stat.S_ISREG(info.st_mode):
            name = str(path.relative_to(BASE))
            meta = {k: getattr(info, 'st_'+k) for k in ['uid', 'gid', 'mode', 'ino', 'dev', 'nlink']}
            # Logs are expected to append; retain security and identity separately.
            if not name.startswith('logs/'):
                meta.update({k: getattr(info, 'st_'+k) for k in ['size', 'mtime_ns', 'ctime_ns']})
                meta['sha256'] = hashlib.sha256(read(path)).hexdigest()
            result[name] = meta
    return result


def proc(pid):
    fields = (Path('/proc')/str(pid)/'stat').read_text().rsplit(') ', 1)[1].split()
    return {'ticks': int(fields[11])+int(fields[12]), 'start_ticks': int(fields[19]),
            'rss': int(fields[21])*os.sysconf('SC_PAGE_SIZE')}


def diagnose(reference, seconds, post):
    out = {'phase': 'post_login' if post else 'pre_logout', 'checks': {},
           'history_incident': 'UNRESOLVED; preserve current state',
           'human_observations': 'PENDING', 'login_to_visible_ms': None}
    checks = out['checks']
    def check(name, fn):
        try:
            checks[name] = fn()
        except Exception as exc:
            checks[name] = {'status': 'BLOCKED', 'reason': type(exc).__name__}
    def snapshots():
        evidence = json.loads(read(REPO/'docs/gates/GATE_F5_5_EVIDENCE.json'))
        results = {}
        for name, hashes in [('daemon', evidence['current_daemon_snapshot']['source_hashes']),
                             ('boot', evidence['current_boot_snapshot']['source_hashes'])]:
            root = HOME/'.local/share'/('siegfried-'+name)
            expected = set(hashes) | {'.siegfried-owned'}
            files = set()
            bad = 0
            for path in root.rglob('*'):
                st = path.lstat()
                rel = str(path.relative_to(root))
                mode = 0o700 if path.is_dir() or rel in ('bin/siegfried', 'bin/siegfried-daemon', 'scripts/boot_hook.py') else 0o600
                bad += int(st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != mode or path.is_symlink())
                if path.is_file():
                    files.add(rel)
                    bad += int(st.st_nlink != 1)
            for rel, digest in hashes.items():
                bad += int(hashlib.sha256(read(root/rel)).hexdigest() != digest)
            bad += int(read(root/'.siegfried-owned') != (b'F5.5\n' if name == 'daemon' else b'F5.3\n'))
            bad += len(files ^ expected)
            results[name] = {'files': len(hashes), 'discrepancies': bad}
        return {'status': 'PASS' if all(x['discrepancies'] == 0 for x in results.values()) else 'FAIL', **results}
    check('snapshots', snapshots)
    current = {}
    def systemd():
        current.update(service())
        good = current.get('ActiveState') == 'active' and current.get('UnitFileState') == 'enabled' and int(current.get('MainPID', 0)) > 0 and current.get('Result') == 'success' and current.get('NRestarts') == '0' and not current.get('DropInPaths')
        if int(current.get('MainPID', 0)) > 0:
            args = (Path('/proc')/current['MainPID']/'cmdline').read_bytes().split(b'\0')
            private_exec = str(HOME/'.local/share/siegfried-daemon/bin/siegfried-daemon').encode() in args
            good = good and private_exec
        else:
            private_exec = False
        return {'status': 'PASS' if good else 'FAIL', 'private_snapshot_exec': private_exec, **current}
    check('service', systemd)
    def duplicates():
        pids = []
        for p in Path('/proc').iterdir():
            if p.name.isdigit():
                try:
                    if p.stat().st_uid == os.getuid():
                        args = (p/'cmdline').read_bytes().split(b'\0')
                        if any(a.endswith(b'/siegfried-daemon') or a == b'siegfried.daemon' for a in args):
                            pids.append(int(p.name))
                except (OSError, ValueError):
                    pass
        main = int(current['MainPID'])
        group = Path('/sys/fs/cgroup')/current['ControlGroup'].lstrip('/')/'cgroup.procs'
        members = [int(x) for x in group.read_text().split()]
        return {'status': 'PASS' if sorted(pids) == [main] and members == [main] else 'FAIL', 'daemon_pids': sorted(pids), 'cgroup_pids': members}
    check('single_instance', duplicates)
    def ipc():
        path = RUN/'siegfried.sock'
        st = path.lstat()
        if not stat.S_ISSOCK(st.st_mode) or stat.S_IMODE(st.st_mode) != 0o600 or st.st_uid != os.getuid():
            return {'status': 'FAIL', 'socket_secure': False}
        results = {}
        for cmd in ('PING', 'STATUS'):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(2)
                sock.connect(str(path))
                reqid = os.urandom(4).hex()
                sock.sendall((json.dumps({'v': 1, 'cmd': cmd, 'args': {}, 'request_id': reqid})+'\n').encode())
                raw = b''
                while b'\n' not in raw and len(raw) <= 65536:
                    block = sock.recv(4096)
                    if not block:
                        break
                    raw += block
                response = json.loads(raw)
                good = response.get('v') == 1 and response.get('request_id') == reqid and response.get('status') == 'OK'
                if cmd == 'PING':
                    good = good and response.get('payload', {}).get('pong') is True
                results[cmd] = 'PASS' if good else 'FAIL'
        return {'status': 'PASS' if all(x == 'PASS' for x in results.values()) else 'FAIL', 'socket_secure': True, 'mode': '0600', **results}
    check('ipc_socket', ipc)
    def autostart():
        path = HOME/'.config/autostart/org.siegfried.BootBriefing.desktop'
        lines = read(path, 4096).decode().splitlines()
        props = dict(x.split('=', 1) for x in lines if '=' in x)
        st = path.lstat()
        target = str(HOME/'.local/share/siegfried-boot/scripts/boot_hook.py')
        good = props.get('Hidden') == 'false' and target in props.get('Exec', '') and str(REPO) not in props.get('Exec', '') and stat.S_IMODE(st.st_mode) == 0o600 and st.st_uid == os.getuid() and st.st_nlink == 1
        command(['desktop-file-validate', str(path)])
        return {'status': 'PASS' if good else 'FAIL', 'hidden': props.get('Hidden'), 'private_snapshot_exec': target in props.get('Exec', '')}
    check('autostart', autostart)
    def ready():
        sys.path.insert(0, str(HOME/'.local/share/siegfried-daemon/src'))
        from siegfried.storage.paths import SiegfriedPaths
        from siegfried.storage.validation import validate_user_runtime
        original = os.open
        def readonly_open(path, flags, *args, **kwargs):
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
                raise ValueError('write_forbidden')
            return original(path, flags | os.O_NOATIME, *args, **kwargs)
        with patch('os.open', readonly_open):
            result = validate_user_runtime(SiegfriedPaths(BASE, RUN), deep_vault_audit=True)
        return {'status': 'PASS' if result.is_ready else 'FAIL', 'runtime': result.status.value}
    check('runtime', ready)
    sess = {}
    def login():
        sess.update(session())
        changed = reference is not None and (sess['id'], sess['boot_id']) != (reference['session']['id'], reference['session']['boot_id'])
        return {'status': ('PASS' if changed and sess.get('Type') in ('wayland', 'x11') and sess.get('Active') == 'yes' else 'BLOCKED') if post else 'PENDING_LOGIN', 'session_changed': changed, 'type': sess.get('Type')}
    check('new_graphical_login', login)
    def briefing():
        path = RUN/'siegfried_briefing.lock'
        if not path.exists():
            return {'status': 'BLOCKED' if post else 'PENDING_LOGIN', 'sent_current_session': False}
        st = path.lstat()
        sent = read(path, 70) == (sess['key']+'\nSENT\n').encode()
        secure = st.st_uid == os.getuid() and stat.S_IMODE(st.st_mode) == 0o600 and st.st_nlink == 1
        return {'status': 'PASS' if sent and secure else 'BLOCKED', 'sent_current_session': sent, 'proves_visible_or_once': False, 'marker_mtime_ns': st.st_mtime_ns}
    check('briefing', briefing)
    def journal():
        invocation = current.get('InvocationID')
        if not invocation:
            raise ValueError('missing_invocation')
        rows = command(['journalctl', '--user', '-b', '_SYSTEMD_INVOCATION_ID='+invocation, '-o', 'json', '--no-pager']).splitlines()
        entries = [json.loads(x) for x in rows]
        errors = sum(int(x.get('PRIORITY', 7)) <= 3 for x in entries)
        warnings = sum(int(x.get('PRIORITY', 7)) == 4 for x in entries)
        return {'status': 'FAIL' if errors else ('PASS' if entries else 'BLOCKED'), 'current_invocation_entries': len(entries), 'priority_0_to_3': errors, 'priority_4': warnings, 'messages_disclosed': False}
    check('service_journal', journal)
    def resources():
        pid = int(current['MainPID'])
        initial = proc(pid)
        begin = time.monotonic()
        rss = [initial['rss']]
        for _ in range(seconds):
            time.sleep(1)
            final = proc(pid)
            if final['start_ticks'] != initial['start_ticks']:
                raise ValueError('pid_reused')
            rss.append(final['rss'])
        elapsed = time.monotonic()-begin
        cpu = 100*(final['ticks']-initial['ticks'])/os.sysconf('SC_CLK_TCK')/elapsed
        stable = service()
        same = stable['MainPID'] == current['MainPID'] and stable['NRestarts'] == current['NRestarts'] and stable['ActiveState'] == 'active'
        return {'status': 'PASS' if same else 'FAIL', 'seconds': elapsed, 'samples': len(rss), 'cpu_core_percent': cpu, 'cpu_tick_seconds': 1/os.sysconf('SC_CLK_TCK'), 'rss_min_bytes': min(rss), 'rss_max_bytes': max(rss), 'rss_limit_bytes': 30000000, 'rss_deviation': max(rss) > 30000000, 'cpu_target_percent': 0.2, 'cpu_deviation': cpu >= 0.2, 'sustained_certification': False}
    check('resources_stability', resources)
    def integrity():
        now = inventory()
        if reference is None:
            return {'status': 'BLOCKED', 'reason': 'missing_reference'}
        before = reference['files']
        changed = [name for name in before.keys() | now.keys() if before.get(name) != now.get(name)]
        # Never disclose names of unknown/private files.
        return {'status': 'PASS' if not changed else 'FAIL', 'changed_file_count': len(changed), 'history_unchanged': before.get('data/.history') == now.get('data/.history'), 'log_content': 'expected append; identity and security checked', 'atime': 'excluded across login; validator reads O_NOATIME'}
    check('private_integrity', integrity)
    if sess and current:
        start = int(current.get('ExecMainStartTimestampMonotonic', 0))
        login_us = int(sess.get('TimestampMonotonic', 0))
        out['service_start_relative_to_session_ms'] = (start-login_us)/1000 if start and login_us else None
        out['timing_scope'] = 'logind session creation to daemon start; not KDE readiness or visible notification'
        if post and start and login_us:
            checks['daemon_after_login'] = {'status': 'PASS' if start >= login_us else 'BLOCKED', 'started_after_session_reference': start >= login_us}
    statuses = [v['status'] for v in checks.values()]
    out['technical_result'] = 'FAIL' if 'FAIL' in statuses else 'BLOCKED' if 'BLOCKED' in statuses else 'PASS'
    out['gate_verdict'] = 'BLOCKED; real login and human observations required'
    return out


def save_persistent_reference():
    """Create a one-time private baseline for a possible later login test."""
    # Use the account's resolved HOME, but refuse symlinked path components.
    parent = HOME / '.local' / 'share'
    target_dir = 'siegfried-gate-f55'
    target_file = 'next-login-reference.json'
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in parent.parts[1:]:
            nxt = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        pst = os.fstat(fd)
        if pst.st_uid != os.getuid() or pst.st_mode & 0o022:
            raise ValueError('unsafe_reference_parent')
        try:
            os.mkdir(target_dir, 0o700, dir_fd=fd)
        except FileExistsError:
            pass
        refdir = os.open(target_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            dst = os.fstat(refdir)
            if dst.st_uid != os.getuid() or stat.S_IMODE(dst.st_mode) != 0o700:
                raise ValueError('unsafe_reference_directory')
            payload = json.dumps({'version': 1, 'uid': os.getuid(), 'base': str(BASE),
                                  'captured_unix_ns': time.time_ns(), 'session': session(),
                                  'files': inventory()}, indent=2).encode()
            ref = os.open(target_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=refdir)
            try:
                view = memoryview(payload)
                while view:
                    view = view[os.write(ref, view):]
                os.fsync(ref)
                fst = os.fstat(ref)
                if fst.st_uid != os.getuid() or stat.S_IMODE(fst.st_mode) != 0o600 or fst.st_nlink != 1:
                    raise ValueError('unsafe_reference_file')
            finally:
                os.close(ref)
            os.fsync(refdir)
        finally:
            os.close(refdir)
    finally:
        os.close(fd)
    return str(HOME / '.local/share' / target_dir / target_file)


def main():
    sys.dont_write_bytecode = True
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path)
    parser.add_argument('--capture-reference', action='store_true')
    parser.add_argument('--save-persistent-reference', action='store_true',
                        help='Create a one-time 0600 baseline under ~/.local/share for a future login test')
    parser.add_argument('--post-login', action='store_true')
    parser.add_argument('--seconds', type=int, default=20)
    args = parser.parse_args()
    if not 5 <= args.seconds <= 60:
        parser.error('--seconds must be between 5 and 60')
    try:
        if args.capture_reference:
            if args.reference or args.post_login:
                parser.error('capture cannot be combined with verification')
            print(json.dumps({'version': 1, 'uid': os.getuid(), 'base': str(BASE), 'captured_unix_ns': time.time_ns(), 'session': session(), 'files': inventory()}, indent=2))
            return 0
        if args.save_persistent_reference:
            if args.reference or args.post_login:
                parser.error('persistent capture cannot be combined with verification')
            print(json.dumps({'persistent_reference_created': True,
                              'path': save_persistent_reference(),
                              'permissions': '0700 directory / 0600 file',
                              'private_contents_or_hashes_printed': False}))
            return 0
        reference = None
        if args.reference:
            info = args.reference.lstat()
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
                raise ValueError('insecure_reference')
            reference = json.loads(read(args.reference))
            if reference['version'] != 1 or reference['uid'] != os.getuid() or reference['base'] != str(BASE):
                raise ValueError('wrong_reference')
        result = diagnose(reference, args.seconds, args.post_login)
        print(json.dumps(result, indent=2))
        return {'PASS': 0, 'FAIL': 1, 'BLOCKED': 2}[result['technical_result']]
    except Exception as exc:
        print(json.dumps({'technical_result': 'BLOCKED', 'reason': type(exc).__name__, 'private_content_disclosed': False}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
