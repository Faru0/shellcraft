"""Example startup script: copy it to ~/.shellcraft/startup/ (and rename it) to use it.

Every .py in ~/.shellcraft/startup/ runs once when the interactive shell starts, in name order,
before the first prompt. What it prints is shown under the banner. `man startup` has the rules.
"""

import os
import shutil

# A startup script runs as its own process: changing os.environ here would not reach the shell.
# To set variables for the shell and everything it starts later (subshells, modules, programs),
# append NAME=value lines to the file named in $SHELLCRAFT_ENV_FILE. `NAME=` unsets NAME.
with open(os.environ["SHELLCRAFT_ENV_FILE"], "a", encoding="utf-8") as env:
    env.write("PROJECT_ROOT=" + os.path.expanduser("~/projects") + "\n")
    env.write(f"EDITOR={shutil.which('code') or shutil.which('vim') or 'notepad'}\n")

# Anything else a startup task needs: check a mount, a VPN, a token's expiry date…
if not os.path.isdir(os.path.expanduser("~/projects")):
    print("note: ~/projects does not exist yet")

# Exit with a nonzero status (or raise) to report a problem: the variables above are then not
# applied, the shell says so in one line, and starts anyway. A script gets 30 seconds.
