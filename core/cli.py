"""`python -m core.cli` still starts ShellCraft (the package is `shellcraft` since 0.3.0)."""

import sys

from shellcraft.cli import *  # noqa: F401,F403
from shellcraft.cli import main

if __name__ == "__main__":
    sys.exit(main())
