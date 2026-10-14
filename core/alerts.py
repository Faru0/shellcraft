"""`python -m core.alerts` (alert shims written by ShellCraft 0.2) still sends alerts."""

import sys

from shellcraft.alerts import *  # noqa: F401,F403
from shellcraft.alerts import main

if __name__ == "__main__":
    sys.exit(main())
