#!/usr/bin/env python3
"""ShellCraft launcher.

    python main.py                 interactive shell
    python main.py --mcp           MCP server over stdio
"""

import sys
import os 
from core.cli import main

os.system('cls' if os.name == 'nt' else 'clear')


if __name__ == "__main__":
    sys.exit(main())
