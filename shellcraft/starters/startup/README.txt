# How to use this folder
# ----------------------
# Startup files: ShellCraft commands that run when the shell starts, like ~/.bashrc.d in bash.
#
# Every .sc file in this folder runs once at startup, right after ~/.shellcraft/rc, in name order.
# Number them to set the order: 10-vpn.sc, 20-project.sc. Keep one file per job.
#
# A .sc file is written exactly like ~/.shellcraft/rc: one ShellCraft command per line, as you'd
# type it at the prompt. env set, cd, theme, modules, loops, input() questions and sub all work.
# They run in your shell, so what one file sets, the next one and your prompt see.
#
# A file named _something.sc is a helper: it never runs on its own, other files pull it in with
#     source ~/.shellcraft/startup/_something.sc
#
# Nothing here runs for `shellcraft --mcp`, or in a `sub shellcraft` subshell.
# Skip it all for one start: shellcraft --no-startup
#
# 10-example.sc.sample is an example. Copy it to a name ending in .sc to use it:
#     cp 10-example.sc.sample 10-project.sc
#
# Inside ShellCraft:
#     startup              lists the files with their last result
#     startup run          runs them again now
#     man startup          the details
#
# More: docs/startup.md
