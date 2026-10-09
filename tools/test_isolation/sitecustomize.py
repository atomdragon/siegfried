"""Supplement Landlock with metadata mutation guards in Python test processes.

The kernel denies content I/O regardless of this hook. stat/chmod/utime are not
mediated by Landlock; stat remains available, while audited mutations below
are rejected. Only installed when the isolated runner adds this directory.
"""
import json
import os
import sys

_BLOCKED = tuple(json.loads(os.environ.get('SIEGFRIED_TEST_BLOCKED_PATHS', '[]')))
_MUTATIONS = {'os.listdir': (0,), 'os.scandir': (0,), 'os.chmod': (0,), 'os.chown': (0,), 'os.utime': (0,),
              'os.remove': (0,), 'os.rmdir': (0,), 'os.mkdir': (0,),
              'os.rename': (0, 1), 'os.link': (0, 1), 'os.symlink': (1,),
              'os.truncate': (0,), 'os.setxattr': (0,), 'os.removexattr': (0,)}


def _path(value, args, event, index):
    if isinstance(value, int):
        return os.path.realpath('/proc/self/fd/' + str(value))
    if not isinstance(value, (str, bytes, os.PathLike)):
        return None
    path = os.fsdecode(value)
    if not os.path.isabs(path):
        # Audit tuple signatures include optional dir_fd; inspect these too.
        offsets = {'os.chmod': 2, 'os.chown': 3, 'os.utime': 3,
                   'os.remove': 1, 'os.rmdir': 1, 'os.mkdir': 2,
                   'os.rename': 2 + index, 'os.link': 2 + index,
                   'os.symlink': 2}
        offset = offsets.get(event)
        fd = args[offset] if offset is not None and len(args) > offset else -1
        parent = os.readlink('/proc/self/fd/' + str(fd)) if isinstance(fd, int) and fd >= 0 else os.getcwd()
        path = os.path.join(parent, path)
    return os.path.realpath(path)


def _guard(event, args):
    for index in _MUTATIONS.get(event, ()):
        if len(args) <= index:
            continue
        path = _path(args[index], args, event, index)
        if path and any(path == root or path.startswith(root + '/') for root in _BLOCKED):
            raise PermissionError('isolated regression forbids real HOME/runtime mutation')


if _BLOCKED:
    sys.addaudithook(_guard)
