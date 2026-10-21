<div align="center">

# ⚡ ShellCraft

**A modular, pipe-friendly terminal shell in Python — the same on Linux and Windows.**

*input → process → pipe/redirect out*

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Platforms](https://img.shields.io/badge/platform-linux%20%7C%20windows%20%7C%20macOS-555)](#installation)
[![MCP server](https://img.shields.io/badge/MCP-server-ff2a6d)](docs/mcp-and-ai.md)
[![License: MIT](https://img.shields.io/badge/license-MIT-05d9e8)](LICENSE)

<img src="docs/demo.gif" alt="ShellCraft demo: a pipeline, jq, Tab completion from the usage line, a loop, a bash subshell in the footer, an alert whose data Alt-v pastes and runs, the man viewer and a theme switch" width="880">

[Install](#installation) · [Quick start](#quick-start) · [Features](#features) · [Guide](docs/README.md) · [Modules](#bundled-modules) · [MCP](docs/mcp-and-ai.md) · [Write a module](#write-your-own-module)

</div>

---

## Features

| | |
| --- | --- |
| 🔗 **Real pipelines** | `a \| b \| c`, `> file` and `>> file`, with an error panel that points at the step that failed. |
| 🧰 **80+ built-in commands** | `ls`, `cat`, `grep`, `find`, `sort`, `cut`, `tr`, `diff`, `du`, `tree`, `printf`, `seq`, `xargs`… written in Python, so they behave the same on every OS. |
| 🧩 **Pluggable modules** | Drop a `.py` (plus its `.md` manual and `.skill` AI description) into `modules/` and it's a new command, hot-reloaded as you edit. Turn any of them off with `modules disable`. |
| 🔁 **Loops & conditions** | Bash's `for`, `while`, `until`, `if` and `case`, or `for -py h in (cat hosts.txt) { if (h.endswith(".gov")) { queryDns $h } }`: no escaping, and conditions are sandboxed Python expressions. A whole loop can be piped: `done \| sort`. |
| ↩️ **Shell habits** | Variables (`x=1`, `$x`, `$HOME`, `$((n + 1))`), `$(…)`, globs, `$?`, `!!`, `!$`, `!N`, `;`, `&&`, `\|\|`, `<`, `2>`, `2>&1`, `>&2`, functions, `select`, `test` / `[ ]` / `[[ ]]`, with the exit status and git branch in the prompt. |
| ⌨️ **Smart input** | Syntax highlighting as you type, a toolbar with the usage of the command you are typing, ghost-text suggestions from history, Ctrl-R, and Tab completion for commands, switches (with descriptions), switch values, positional values and paths (quoted when they need it). |
| 📖 **Man pages & pager** | `man <command>` for everything, in a built-in scrollable viewer with search, wrapping, sideways scrolling and mouse support; `cmd \| less` pages anything else; `help NAME` for the short version. |
| 🎨 **Themes** | Cyberpunk, Matrix, Nord, Solarized, Dracula, Gruvbox, Catppuccin, Tokyo Night, two light variants, or your own; `theme preview` tries one. |
| 🪟 **Subshells** | `sub` opens your own shell, `sub vim`, `sub htop` or `sub ssh host` hands the terminal to a program, `sub shellcraft` nests a ShellCraft, and `sub grep -r TODO .` runs a command in the background. A footer on the last row shows every shell as a tab: click one, or press Ctrl-] N, to switch; Ctrl-] alone comes back. |
| 🤖 **An AI co-pilot over MCP** | Claude Code, Claude Desktop and other AI clients use your modules as typed tools, read your shell's screen, open a subshell with their findings, and run commands: read-only ones at once, the rest after you approve them. `mark` and `tell` send them what you see. |
| 🔑 **Settings & API keys** | On/off settings and masked API keys for modules, saved in one config file. |

## Installation

You need **Python 3.11 or newer**.

```bash
git clone https://github.com/Faru0/shellcraft.git
cd shellcraft
python3 -m venv .venv
source .venv/bin/activate          # Windows (PowerShell): .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

<details>
<summary>Windows notes, and installing a <code>shellcraft</code> command</summary>

On Windows, create the venv with `py -m venv .venv`. If PowerShell refuses to run the activation
script, run `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser` once.

`pip install .` installs a `shellcraft` command that works from any folder. Use `pip install -e .`
instead if you edit the bundled modules, so changes take effect without reinstalling.

</details>

## Quick start

```bash
python main.py                                        # start the interactive shell
python main.py --mcp                                  # run as an MCP server
```

Inside the shell, try:

```
help                                          # every command, grouped
man grep                                      # manual pages, in a scrollable viewer
cat app.log | grep ERROR | sort | uniq -c     # pipelines
grep -i error app.log > errors.txt            # > overwrites, >> appends
alias errs='grep -i error'                    # your own shortcuts
ip2geo 8.8.8.8 -f country,isp                 # geolocate an IP or domain name
theme matrix                                  # switch the color theme
settings                                      # on/off settings and API keys
history 20                                    # what you typed
```

---

## A short tour

Each part has its own page in [`docs/`](docs/README.md), and every command has a manual in the
shell (`man NAME`).

### Pipes and scripting

```
cat app.log | grep ERROR | sort | uniq -c > counts.txt   # pipes, > and >>; lines flow as they are made
for h in $(cat hosts.txt); do queryDns $h; done          # bash's for, while, if, case, select…
backup() { cp -r "$1" "$1.bak" && echo saved; }          # functions, local, return
grep -q TODO notes.txt && echo found || echo clean       # $?, &&, ||, test, [ ], [[ ]]
alias errs='grep -i error'                               # aliases, kept in ~/.shellcraft/aliases
```

`~/.shellcraft/rc` runs at startup like `~/.bashrc`, and Python scripts in
`~/.shellcraft/startup/` can set up the environment or keep a watcher running.
**→ [Pipes, scripting and startup](docs/scripting.md)**

### Subshells, alerts and the clip

```
sub                          # your own shell (bash, zsh, pwsh…)
sub htop                     # any program; Ctrl-] goes back to the prompt, Ctrl-] N switches
grep -r TODO . &             # a command in the background
alert -l warn disk at 90%    # an alert in the footer, from anywhere (scripts and AI clients too)
```

A footer on the last rows lists every subshell and alert; click a line or press Ctrl-] N to
switch. A right-click on an alert copies its data, and Alt-v pastes it.
**→ [Subshells, alerts and the clip](docs/subshells.md)**

### Keyboard and mouse

Syntax highlighting, a usage toolbar, ghost-text suggestions, Ctrl-R, and Tab completion that
follows each command's usage line. `man` and `less` open a viewer with search and mouse support.
`hotkeys` lists every shortcut.
**→ [Keyboard and mouse](docs/keyboard-and-mouse.md)**

### Settings and themes

`settings` lists every on/off setting and the API keys modules need; `settings KEY on|off`
changes one. `theme` lists ten themes, and you can define your own.
**→ [Settings, API keys and themes](docs/settings.md)**

### An AI co-pilot over MCP

`python main.py --mcp` (or `mcp start` inside the shell) serves your modules as typed tools to
Claude Code, Claude Desktop and other MCP clients. While your shell runs, an AI can also read its
screen, open a subshell with its findings, and run commands: read-only ones at once, the rest
after you approve them with `review`. You flag things for it with `mark` and talk to it with
`tell`.
**→ [MCP server and the AI co-pilot](docs/mcp-and-ai.md)** · agents and skills in [ai/](ai/README.md)

> [!WARNING]
> Connected AI clients can read any file your user account can read, and while your shell runs,
> its screen and your history. Only connect AI clients you trust.

---

## Built-in commands

Built into ShellCraft (written in Python), so they work the same on every operating system. Each
one has a manual: `man ls`, `man grep`, …

| Group | Commands |
| --- | --- |
| Shell | `cd` `pwd` `exit` `clear` `help` `man` `hotkeys` `theme` `prompt` `settings` `alias` `unalias` `history` `modules` `reload` `banner` `startup` `source` `.` `export` `unset` `declare` `local` `shift` `read` `sleep` `time` `xargs` `test` `[` `[[` `true` `false`, and `diag` once diagnostics are on |
| Scripting | `for` `while` `until` `if` `case` `select` `function` (`man for`, `man if`…) |
| Subshells and alerts | `sub` `jobs` `alert` `clip` `record` `script` |
| AI and MCP | `mcp` `mark` `tell` `review` `ingest` |
| Text | `echo` `printf` `cat` `grep` `head` `tail` `wc` `sort` `uniq` `cut` `tr` `tee` `diff` `seq` `yes` `jq` `less` `more` |
| Files | `ls` `find` `tree` `du` `touch` `mkdir` `cp` `mv` `rm` `basename` `dirname` `realpath` `stat` |
| Info | `date` `which` `type` `env` `whoami` `hostname` `uname` |

- **Colored on screen, plain when piped:** `ls` shows colored columns, `tree` draws a colored tree, and `grep` highlights matches. Piped or redirected, they output plain text, one item per line.
- **`which NAME`** tells you whether a name runs an alias, a builtin, a module or an OS program.
- **`rm` safety:** there is no trash can, so `rm` refuses a filesystem root, your home folder, and the current folder or its parents.
- **`diff -u old new`** gives the Git-style format; **`du -sh`** totals a folder; **`history | grep ssh`** finds an old command.
- **`jq` and deeply nested data:** `cat org.json | jq -s` outlines dict -> list -> dict -> list… to any depth,
  `jq -R email -P` finds every `email` at any depth and where it lives, `jq -W 'status=error'` every object that
  matches, `jq -f '..members[*].name'` / `[*].teams[*].name` go through the levels, `jq -F | grep` flattens to
  `a.b[0].c = value`, `-d 2` stops at two levels, and a wrong key says where it really is (`man jq`).
- **`grep -rn TODO .`**, **`grep -C 2 error app.log`**, **`ls -lt`**, **`find . -size +1M -mtime -7`**, **`sort -h`**, **`tail -f app.log`**, **`seq 10 | xargs -n 2 echo`**: the switches a bash user reaches for are there. A mistyped switch shows the usage line and a "did you mean".

## Bundled modules

| Module | What it does |
| --- | --- |
| `fetch` | Reads text from a URL, a file or stdin. `--head N` keeps the first N lines, and `--json PATH` extracts a field from JSON. |
| `filter` | Keeps lines matching a regular expression (like `grep`, for stdin). |
| `myip` | Shows your public IP, or the location/network owner of any IP (via ipconfig.io). `-p PORT` checks whether a port is reachable. |
| `ip2geo` | Geolocates IPs or domain names: country, city, ISP, ASN, and mobile/proxy/hosting flags (via ip-api.com, no key). |
| `queryDns` | A domain's DNS records (A, MX, NS, TXT, CNAME) with each IP's owner, country and netblock (DnsDumpster API; **needs a free key**). |
| `queryCert` | TLS certificates and subdomains from Certificate Transparency logs (via crt.sh, no key). `-s` finds subdomains. |
| `queryCensys` | Open ports and software on a host, certificate details, or Censys searches (Censys Platform; **needs a token** and `pip install censys-platform`). |

See `man <module>` for details, e.g. `man queryDns`.

### Turning modules on and off

```
modules                        # the enabled modules (the default)
modules -a                     # every module, with an on/off column
modules disable queryCensys    # off: not a command, not an MCP tool, never imported
modules enable queryCensys     # back on, loaded right away
```

The choice is saved in `~/.shellcraft/config.json` and applies to the MCP server as well
(`mcp restart` passes a change on to one that's already running). Running a disabled module says
how to enable it. MCP clients can list modules but can't enable or disable them.

## Write your own module

Put your modules in **`~/.shellcraft/modules`**: it is loaded next to the bundled ones, a module there
shadows a bundled one of the same name, and `modules` shows where each comes from.

A module is up to three files: the code (`.py`, always a command), its manual (`.md`,
for `man`) and its AI description (`.skill`, which makes it an MCP tool). The code is either a
`run(args, stdin)` function that returns text or a plain Python script that prints; files named
`_name.py` are helpers the others import.

Already have a Python script? `ingest myscript.py` inside the shell (or `shellcraft ingest
myscript.py` from a terminal) converts it with the fewest possible changes, writes the `.md` and
`.skill`, and checks the result; the new command works at the next prompt. It can also hand the
job to Claude or OpenAI. `ingest check --all` checks every module. The **module guide** has the
details, the rules, a reference module and the checker:

**→ [templates/README.md](templates/README.md)**

## More documentation

| Document | For |
| --- | --- |
| [docs/](docs/README.md) | The user guide: scripting, subshells, keyboard and mouse, settings, MCP |
| [templates/README.md](templates/README.md) | Writing and testing your own modules |
| [ai/README.md](ai/README.md) | Agents and skills that teach an AI client to work as a co-pilot in ShellCraft |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Working on ShellCraft itself: layout, architecture, running the tests |
| [CHANGELOG.md](CHANGELOG.md) | What changed in each release |

<div align="center">

MIT licensed · [LICENSE](LICENSE)

<sub>The demo is <a href="docs/demo.cast">docs/demo.cast</a> (<code>asciinema play docs/demo.cast</code>), recorded by <code>python tools/demo.py</code>.</sub>

</div>
