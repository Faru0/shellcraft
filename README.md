<div align="center">

# ⚡ ShellCraft

**A terminal shell in Python that you extend with your own commands.**
**The same on Linux, macOS and Windows.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Platforms](https://img.shields.io/badge/platform-linux%20%7C%20windows%20%7C%20macOS-555)](#install)
[![MCP server](https://img.shields.io/badge/MCP-server-ff2a6d)](docs/mcp-and-ai.md)
[![License: MIT](https://img.shields.io/badge/license-MIT-05d9e8)](LICENSE)

<img src="docs/demo.gif" alt="ShellCraft demo: a pipeline, jq, Tab completion from the usage line, a loop, a bash subshell in the footer, an alert whose data Alt-v pastes and runs, the man viewer and a theme switch" width="880">

[Install](#install) · [Quick start](#quick-start) · [Manual](docs/README.md) · [Write a command](docs/modules/first-module.md) · [Cheat sheet](docs/cheatsheet.md)

</div>

---

ShellCraft is an interactive shell with pipes, redirects, loops and the everyday Unix commands,
all written in Python so they behave the same on every system. Drop a Python file into a folder
and it becomes a new command, with a manual page, Tab completion and pipe support. Connect an AI
client over MCP and it can use your commands too.

- **80+ built in commands.** `ls`, `grep`, `find`, `sort`, `jq`, `curl`, `diff` and the rest, with the switches you know from bash.
- **Your own commands in plain Python.** A function called `run` is enough. Or let the ingest wizard convert a script you already have.
- **Real pipelines and scripting.** `a | b > file`, variables, `for`, `while`, `if`, functions, `$?`, `!!`, written bash style or Python style (`for -py line in (cat f):` and an indented body).
- **Made to be customized.** An rc file, aliases, startup files, funcs and profiles, all plain files in `~/.shellcraft`.
- **Subshells side by side.** Run `htop`, `vim` or `ssh` in a subshell and switch between them from a footer.
- **An AI co-pilot over MCP.** Claude and other AI clients can use your commands, read your screen and run commands you approve.

## Install

You need Python 3.11 or newer.

```bash
git clone https://github.com/Faru0/shellcraft.git
cd shellcraft
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

On Windows, use `py -m venv .venv` and `.venv\Scripts\Activate.ps1`. To get a `shellcraft` command
that works from any folder, also run `pip install .`

## Quick start

```bash
python main.py
```

Then try:

```
help                                      # every command
man grep                                  # the manual for one
cat app.log | grep ERROR | sort | uniq -c # pipes
theme nord                                # change the colors
```

Your settings and files live in `~/.shellcraft`. The first start puts commented examples there to
get you going.

## Where to go next

| You want to | Read |
| --- | --- |
| Learn the basics in ten minutes | [Getting started](docs/getting-started.md) |
| Write your own command | [Your first module](docs/modules/first-module.md) |
| Turn a script you already have into a command | [Ingest a script](docs/modules/ingest.md) |
| Set up aliases, an rc file, funcs and profiles | [Your ~/.shellcraft folder](docs/your-shellcraft-folder.md) |
| Write scripts: variables, questions, loops, menus, functions | [Scripting guide](docs/scripting/README.md) |
| Copy ready-made setups | [Cheat sheet](docs/cheatsheet.md) |
| Connect Claude or another AI client | [MCP and the AI co-pilot](docs/mcp-and-ai.md) |
| Read everything | [The manual](docs/README.md) |

> [!WARNING]
> Connected AI clients can read any file your user account can read, and while your shell runs,
> its screen and your history. Only connect AI clients you trust.

## Contributing

[DEVELOPMENT.md](DEVELOPMENT.md) explains how ShellCraft is built and how to run the tests.
[CHANGELOG.md](CHANGELOG.md) lists what changed in each version.

<div align="center">

MIT licensed · [LICENSE](LICENSE)

<sub>The demo is <a href="docs/demo.cast">docs/demo.cast</a> (<code>asciinema play docs/demo.cast</code>), recorded by <code>python tools/demo.py</code>.</sub>

</div>
