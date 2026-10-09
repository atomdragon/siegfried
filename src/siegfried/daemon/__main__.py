"""Module execution entrypoint for python3 -m siegfried.daemon."""

import os
import sys
from siegfried.core.errors import StorageError
from siegfried.daemon.app import SiegfriedDaemon


def main() -> int:
    ex_config = getattr(os, "EX_CONFIG", 78)
    try:
        daemon = SiegfriedDaemon()
        daemon.run_forever()
        return 0
    except StorageError as e:
        print(f"[FATAL] Siegfried Daemon startup failure: {e}", file=sys.stderr)
        return ex_config
    except KeyboardInterrupt:
        return 0
    except Exception as e:
        print(f"[FATAL] Siegfried Daemon unexpected failure: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
