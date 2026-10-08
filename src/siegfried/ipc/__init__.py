"""IPC package exposing protocol framing, client and server abstractions."""

from siegfried.ipc.protocol import serialize_frame, deserialize_frame
from siegfried.ipc.client import IPCClient
from siegfried.ipc.server import IPCServer, CommandHandler

__all__ = [
    "serialize_frame",
    "deserialize_frame",
    "IPCClient",
    "IPCServer",
    "CommandHandler",
]
