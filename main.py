#!/usr/bin/env python3
"""ShellCraft launcher.

    python main.py                 interactive shell
    python main.py --mcp           MCP server over stdio
"""

import signal
import sys


def _quiet_interrupt(signum, frame):
    """Ctrl-C before the shell is ready: no Python traceback, just out, as a shell should."""
    sys.stderr.write("\n^C interrupted during startup\n")
    sys.exit(130)


if __name__ == "__main__":
    # Installed before the imports: a Ctrl-C while they load (the first 150 ms or so) ended in
    # a traceback. The shell replaces it with its own handling once it is running.
    signal.signal(signal.SIGINT, _quiet_interrupt)
    from shellcraft.cli import main

    sys.exit(main())
