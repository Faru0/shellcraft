#!/usr/bin/env python3
"""ShellCraft launcher.

    python main.py                 interactive shell
    python main.py -c "a | b > f"  run one command line
    python main.py --mcp           MCP server over stdio
"""

import sys

from core.cli import main

if __name__ == "__main__":
    sys.exit(main())
