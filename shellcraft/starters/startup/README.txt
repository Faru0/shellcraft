Startup scripts: Python files that run when the interactive shell starts
=========================================================================

Every .py file in this folder runs once at startup, in name order, before the first prompt.
Number them to set the order: 10-env.py, 20-mounts.py. Files named _name.py are helpers the
others can import. Nothing here runs for `shellcraft --mcp`.

Use them for what a command line can't do: set environment variables for everything ShellCraft
starts, check a mount or a VPN, print a reminder. A script that fails, or takes longer than 30
seconds, is reported in one line and the shell starts anyway.

A script that should keep running (a watcher, a tunnel) starts in the background instead, so the
shell never waits for it: put this line at the top of the file:

    # shellcraft: background, restart

Two examples come with this folder. They don't run while their names end in .sample:

    10-example.py.sample    sets variables for the shell and prints a note
    30-watcher.py.sample    a background script that alerts you about new files

To try one, copy it to a name ending in .py:

    cp 10-example.py.sample 10-example.py

Inside ShellCraft:
    startup              lists the scripts with their last result
    startup run          runs them again now
    man startup          the details

The manual: docs/startup.md
