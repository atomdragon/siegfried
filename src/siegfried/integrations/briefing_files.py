"""Small POSIX guards for ephemeral dedup and opt-in read-only agenda context."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from siegfried.contracts.config import validate_active_agenda


def directory_fd(path, *, private=False):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('unsafe_directory')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        st = os.fstat(fd)
        if private and (st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700):
            raise ValueError('insecure_directory')
        return fd
    except BaseException:
        os.close(fd)
        raise


def checked_file(fd, max_size):
    st = os.fstat(fd)
    if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or
            stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1 or st.st_size > max_size):
        raise ValueError('insecure_file')


def read_critical_task(paths):
    """Existing v1 agenda only, shared existing lock; no writes or lock creation."""
    directory = lock = data = None
    try:
        directory = directory_fd(paths.config_dir, private=True)
        lock = os.open(paths.active_agenda_lock_file.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                       dir_fd=directory)
        checked_file(lock, 512)
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        data = os.open(paths.active_agenda_file.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                       dir_fd=directory)
        checked_file(data, 65536)
        raw = os.read(data, 65537)
        if len(raw) > 65536:
            return None
        agenda = json.loads(raw.decode('utf-8'))
        validate_active_agenda(agenda)
        task = agenda['critical_task']
        return task.get('title') if isinstance(task, dict) else None
    except (OSError, ValueError, TypeError, RecursionError):
        return None
    finally:
        for fd in (data, lock, directory):
            if fd is not None:
                os.close(fd)


def session_key(environ=os.environ):
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    sid = environ.get('XDG_SESSION_ID', '')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', sid):
        sid = 'unknown-session'
    # Hash only: no bus address or session metadata recorded in plaintext.
    return hashlib.sha256((boot + '\0' + sid + '\0' + environ.get('DBUS_SESSION_BUS_ADDRESS', '')).encode()).hexdigest()


class BriefingLease:
    """One successful notification per graphical session; ephemeral runtime only."""
    def __init__(self, paths, key):
        if not re.fullmatch(r'[a-f0-9]{64}', key):
            raise ValueError('invalid_session_key')
        self.paths, self.key, self.fd = paths, key, None
        self.duplicate = False

    def __enter__(self):
        directory = directory_fd(self.paths.runtime_dir, private=True)
        try:
            self.fd = os.open(self.paths.briefing_marker_file.name,
                              os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
            checked_file(self.fd, 70)
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.duplicate = True
                return self
            raw = os.read(self.fd, 71)
            if raw and not re.fullmatch(rb'[a-f0-9]{64}\nSENT\n', raw):
                raise ValueError('invalid_briefing_marker')
            self.duplicate = raw == (self.key + '\nSENT\n').encode()
            return self
        except BaseException:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            raise
        finally:
            os.close(directory)

    def mark_sent(self):
        if self.fd is None or self.duplicate:
            raise ValueError('invalid_briefing_lease')
        os.lseek(self.fd, 0, os.SEEK_SET)
        os.write(self.fd, (self.key + '\nSENT\n').encode())
        os.ftruncate(self.fd, 70)

    def __exit__(self, *args):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
