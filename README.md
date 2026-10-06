<div align="center">

# ⚡ ShellCraft

**A modular, pipe-friendly terminal shell in Python — the same on Linux and Windows.**

*input → process → pipe/redirect out*

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Platforms](https://img.shields.io/badge/platform-linux%20%7C%20windows%20%7C%20macOS-555)](#installation)
[![MCP server](https://img.shields.io/badge/MCP-server-ff2a6d)](#use-it-from-an-ai-client-mcp)
[![License: MIT](https://img.shields.io/badge/license-MIT-05d9e8)](LICENSE)

<img src="docs/demo.gif" alt="ShellCraft demo: pipes, redirection, Tab completion, aliases, man pages, settings and history in the cyberpunk theme" width="880">

[Install](#installation) · [Quick start](#quick-start) · [Features](#features) · [Modules](#bundled-modules) · [MCP](#use-it-from-an-ai-client-mcp) · [Write a module](#write-your-own-module)

</div>

---

## Features

| | |
| --- | --- |
| 🔗 **Real pipelines** | `a \| b \| c`, `> file` and `>> file`, with an error panel that points at the step that failed. |
| 🧰 **39 built-in commands** | `ls`, `cat`, `grep`, `find`, `sort`, `cut`, `tr`, `diff`, `du`, `tree`… written in Python, so they behave the same on every OS. |
| 🧩 **Pluggable modules** | Drop a `.py` (plus its `.md` manual and `.skill` AI description) into `modules/` and it's a new command, hot-reloaded as you edit. Turn any of them off with `modules disable`. |
| 🔁 **Loops & conditions** | `for h in (cat hosts.txt) { if (h.endswith(".gov")) { queryDns $h } }`: parsed by ShellCraft, so no escaping, and conditions are sandboxed Python expressions. |
| ↩️ **Shell habits** | `$?`, `!!`, `!$` and `;`, with the exit status in the prompt. |
| ⌨️ **Smart input** | Ghost-text suggestions from history, and Tab completion for commands, switches (with descriptions), switch values and paths. |
| 📖 **Man pages & pager** | `man <command>` for everything, in a built-in scrollable viewer with search and highlighting. |
| 🎨 **Themes** | Cyberpunk, Matrix, Nord, Solarized, Dracula, Gruvbox, Catppuccin, Tokyo Night, or your own. |
| 🤖 **Built-in MCP server** | Claude Desktop, Claude Code and other AI clients can use your modules as typed tools, safely sandboxed from writes. |
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

## Using the shell

### Pipes and redirection

- `a | b | c` passes each command's output to the next command as input.
- `> file` writes the final output to a file, and `>> file` appends to it.
- Quote arguments that contain spaces or operators: `grep "a | b" notes.txt`. Backslashes are kept as typed, so Windows paths like `C:\data\log.txt` work.
- If any command fails, the pipeline stops, an error panel names the failing step, and nothing is written to the redirect file.
- `cd` only works on its own line. `cd x | pwd` is refused, because it would change the shell's directory from inside a pipeline.

- `a ; b` runs one command after the other; `b` runs even if `a` failed (see [Loops and conditions](#loops-and-conditions)).

Not supported yet: `2>`, `<`, `&&`, `||`, environment variables (`$HOME`) and `*` globbing outside
`for` lists (see [TODO.md](TODO.md)). The operators are rejected with a parse error rather than
passed on as arguments. Quote them to use them as text.

### Loops and conditions

ShellCraft parses loops itself, so there is none of the quoting and escaping a loop needs when
it is passed through another shell: `$x` is the current item as **one** argument, whatever it
contains.

```
for ip in 1.1.1.1 8.8.8.8 { ip2geo $ip }
for h in (cat hosts.txt) { queryDns $h >> dns.txt }        # each line of a command's output
for n in 1..5 { echo $n }                                  # ranges: 5..1, 0..100..10
for f in *.log { if grep -q ERROR $f { echo $f } }         # globs; a command as the condition

for ip in (cat ips.txt) {                                  # an open { continues on the next line
  if (ip.startswith("10.")) { echo internal $ip }
  elif (ip in ("8.8.8.8", "1.1.1.1")) { echo dns $ip }
  else { ip2geo $ip }
}
```

- **Conditions** are either a command (true when it succeeds; `if ! cmd` negates it), or a
  Python expression in parentheses: loop variables are names (`ip`, or `$ip`), `status` is `$?`,
  and you get comparisons, `and`/`or`/`not`, `in`, slicing, string methods (`startswith`,
  `split`, `isdigit`…), `len`, `int`, `match(regex, text)`, `exists(path)` and a few more.
  Expressions are checked and evaluated by ShellCraft (no `eval`), so imports, other attributes
  and other functions are refused. Loop variables are text: compare numbers with `int(n) > 3`.
- `break` and `continue` work in loops; statements are separated by `;` or new lines.
- A failing command shows its error and the loop goes on, as in bash; `$?` holds its status.
- Braces are words of their own: `{ echo $x }`. A loop's output can be redirected per command
  (`>> file` inside the body), not as a whole yet.

`man for` and `man if` have the details. Loops also work over MCP (with the same restrictions).

### Exit status and history

- `$?` is the last command's exit status: 0 for success, 1 for a failure, 2 for a syntax error,
  127 for an unknown or disabled command, 130 after Ctrl-C, and an OS program's own code. `grep`
  returns 1 when nothing matches, without an error panel, so `if grep -q …` works.
- The prompt shows `[✗ 127]` after a command fails.
- `!!` is the previous command and `!$` its last word, as in bash: `sudo !!`, `cat !$`. The
  expanded line is printed before it runs and saved in the history (not inside single quotes;
  `!=` and `hi!` are left alone).

### Typing helpers

- **Ghost text:** start typing a command you've used before, and a faint suggestion appears. Press **→** to accept it.
- **Tab** completes command names and aliases, **switches** for every command (`grep -<Tab>` shows what each one does), **switch values** (`ip2geo -o <Tab>`, `find . -type <Tab>`), arguments for `man`, `theme`, `settings`, `modules` and `unalias`, commands inside `for` / `if` blocks, and paths everywhere else.
- **Ctrl-C** cancels the current line or a running command. **Ctrl-D** or `exit` leaves the shell.
- A line that ends inside `{ … }` or `( … )` asks for another line (`┆ …`); the whole command is saved to the history as one line.
- `banner` shows the welcome banner again (`banner -c` clears the screen first).

### Aliases

```
alias ll='ls -l'        # a shorter name for a command you type often
alias ls='ls -a -l'     # or change what a command does by default
alias                   # list your aliases
unalias ll              # remove one (unalias -a removes all)
\ls                     # a leading backslash skips the alias
```

An alias replaces the first word of a command, and anything you type after it is added at the end.
Aliases work in every step of a pipeline, are saved in `~/.shellcraft/config.json`, and apply in the
shell, never to AI clients over MCP. `which ls` shows whether a name is an alias.

### Long output

`man <command>` always opens in a scrollable viewer (`man -p <command>` prints it instead), and so does any other output taller than the window (`help` is the exception: it is printed in full, so you can scroll back to it): `↑`/`↓` or `j`/`k` scroll, `PgUp`/`PgDn`/`space`
page, `g`/`G` jump to the top or end, `/` searches (matches are highlighted), `n`/`N` step through
matches, and `q` closes it. Turn it off with `settings pager off`.

<details>
<summary>Command-line options and environment variables</summary>

| Option | Meaning |
| --- | --- |
| `--mcp` | Run as an MCP server over stdio instead of the interactive shell. |
| `--mcp-http HOST:PORT` | Run as an MCP server over streamable HTTP at `http://HOST:PORT/mcp`. Only addresses on this machine are allowed (`127.0.0.1`, `localhost`, `::1`). |
| `--theme NAME` | Use a theme for this session only. |
| `--no-banner` | Skip the startup banner. |
| `--allow-system` | Allow OS commands for this session (see [OS commands](#os-commands)). |
| `--modules DIR` | Load modules from DIR instead of the bundled ones. |
| `--version`, `-h` | Show the version, or the help text. |

| Environment variable | Meaning |
| --- | --- |
| `SHELLCRAFT_HOME` | Where config and history are stored (default `~/.shellcraft`). |
| `SHELLCRAFT_MODULES` | The default modules folder (overridden by `--modules`). |

</details>

---

## Built-in commands

Built into ShellCraft (written in Python), so they work the same on every operating system. Each
one has a manual: `man ls`, `man grep`, …

| Group | Commands |
| --- | --- |
| Shell | `cd` `pwd` `exit` `clear` `help` `man` `theme` `settings` `alias` `unalias` `history` `modules` `reload` `banner` `mcp`, and the keywords `for` `if` |
| Text | `echo` `cat` `grep` `head` `tail` `wc` `sort` `uniq` `cut` `tr` `tee` `diff` |
| Files | `ls` `find` `tree` `du` `touch` `mkdir` `cp` `mv` `rm` |
| Info | `date` `which` `env` |

- **Colored on screen, plain when piped:** `ls` shows colored columns, `tree` draws a colored tree, and `grep` highlights matches. Piped or redirected, they output plain text, one item per line.
- **`which NAME`** tells you whether a name runs an alias, a builtin, a module or an OS program.
- **`rm` safety:** there is no trash can, so `rm` refuses a filesystem root, your home folder, and the current folder or its parents.
- **`diff -u old new`** gives the Git-style format; **`du -sh`** totals a folder; **`history | grep ssh`** finds an old command.

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

## Settings

`settings` shows every setting and the API keys modules need. `settings KEY on|off|toggle` changes
a setting immediately, and `settings reset KEY` restores its default. Everything is saved in
`~/.shellcraft/config.json`.

| Key | Default | Meaning |
| --- | --- | --- |
| `system_commands` | off | Allow OS programs from your `PATH` (see [OS commands](#os-commands)). |
| `pager` | on | Open man pages, and output taller than the window, in the scrollable viewer. |
| `spinner` | on | Show a spinner while slow commands run. |
| `banner` | on | Show the startup banner. |
| `hot_reload` | on | Reload modules when their `.py`, `.md` or `.skill` files change, in the shell and in the MCP server. |

### API keys

Some modules call services that need a key. `settings` lists them as *module · label*, and never
shows the value in full: `set ••••ab12`, `from environment`, or `not set`.

```
settings DNSDUMPSTER_API_KEY          # asks for the key; typing is hidden
settings DNSDUMPSTER_API_KEY VALUE    # or set it in one go
settings reset DNSDUMPSTER_API_KEY    # forget it
```

| Variable | Module | Where to get it |
| --- | --- | --- |
| `DNSDUMPSTER_API_KEY` | `queryDns` | Free account at [dnsdumpster.com](https://dnsdumpster.com): the key is on your dashboard. |
| `CENSYS_API_TOKEN` | `queryCensys` | Censys Platform → your user icon → **API Access** → *Create New Token*. |
| `CENSYS_ORG_ID` | `queryCensys` | Paid plans only (needed for `search`): the *Current Organization* box on the same page. |

<details>
<summary>How keys are stored and passed to modules</summary>

- **Storage:** plain text under `"env"` in `~/.shellcraft/config.json`, which is made readable by you only (mode 600).
- **Delivery:** stored keys are exported as environment variables at startup, in every mode including `--mcp`, and right after you set one. A variable already set in your shell (or an MCP client's `env` block) wins at startup. After `settings reset`, your shell's own value applies again.
- **History:** `settings NAME VALUE` is saved as `settings NAME ••••`, but prefer the prompt form, `settings NAME`. Start any line with a space to keep it out of the history entirely.
- `queryCensys` also needs the Censys SDK: `pip install censys-platform`, or `pip install -e ".[censys]"`.

</details>

### OS commands

By default ShellCraft runs **only** its builtins and modules, so a command line does the same thing
on Windows and Linux. `settings system_commands on` (or `--allow-system` for one session) lets
unknown commands run programs from your `PATH`; builtins still win over programs with the same
name (`which -a NAME` shows both). OS programs run with their output captured, so full-screen
programs such as `vim` or `top` don't work inside ShellCraft yet.

## Themes

`cyberpunk` (the default), `matrix`, `nord`, `solarized`, `dracula`, `gruvbox`, `catppuccin` and
`tokyonight`. `theme` lists them with color swatches, and `theme NAME` switches immediately and
saves your choice.

<details>
<summary>Make your own theme</summary>

Add it to `~/.shellcraft/config.json`. Any color you leave out is taken from `base`:

```json
{
  "theme": "sunset",
  "themes": {
    "sunset": {
      "base": "cyberpunk",
      "label": "Sunset",
      "prompt": "#ff7b00",
      "accent": "#ff0059",
      "gradient_from": "#ff7b00",
      "gradient_to": "#ff0059"
    }
  }
}
```

A theme can set `prompt`, `path`, `accent`, `muted`, `error`, `success`, `warning`,
`gradient_from` and `gradient_to`, as `#rrggbb` hex values. An invalid color is replaced by the
base theme's color, with a warning.

</details>

---

## Use it from an AI client (MCP)

`python main.py --mcp` starts an MCP server over stdio; `python main.py --mcp-http 127.0.0.1:8765`
serves the same tools over streamable HTTP at `http://127.0.0.1:8765/mcp` (this machine only, with
DNS-rebinding protection).

To keep using the shell while the HTTP server runs, start it from inside the shell instead:
`mcp start [HOST:PORT]` runs it in the background as a child process (Ctrl-C at the prompt doesn't
stop it, and the prompt shows `● mcp :8765` while it runs), `mcp` shows its status, `mcp log` its log, and `mcp stop` stops it. It also stops when
you leave the shell. See `man mcp`.

- **One tool per module**, with typed parameters from its `.skill` file, e.g. `{"pattern": "error", "ignore_case": true, "stdin": "..."}`.
- **`shellcraft_pipeline`** runs a whole command line, such as `{"command": "cat notes.txt | grep -i todo | sort"}`, using the read-only builtins. It **cannot** redirect to files, change files, read environment variables, change the shell's state or settings, or run OS programs (unless you start the server with `--allow-system`).
- **Man pages as resources** at `shellcraft://man/<name>`.
- **Hot reload:** editing, adding or removing a module updates connected clients' tool lists.

**Claude Code**

```bash
claude mcp add shellcraft -- /path/to/shellcraft/.venv/bin/python /path/to/shellcraft/main.py --mcp
```

**Claude Desktop** (or any client with an `mcpServers` config)

```json
{
  "mcpServers": {
    "shellcraft": {
      "command": "/path/to/shellcraft/.venv/bin/python",
      "args": ["/path/to/shellcraft/main.py", "--mcp"]
    }
  }
}
```

Use absolute paths. On Windows, the Python path is `C:\\path\\to\\shellcraft\\.venv\\Scripts\\python.exe`.
Over HTTP: `claude mcp add --transport http shellcraft http://127.0.0.1:8765/mcp`. To poke at the
server by hand: `npx @modelcontextprotocol/inspector .venv/bin/python main.py --mcp`.

> [!WARNING]
> Connected AI clients can read any file your user account can read (through `fetch`, `cat`,
> `grep`, …), and `fetch` can request any URL. They can't change files. Modules that use your API
> keys (`queryDns`, `queryCensys`) spend your quota when an AI calls them. Only connect AI clients
> you trust.

---

## Write your own module

A module is three files in `modules/`: the code (`.py`), its manual (`.md`) and its AI description
(`.skill`). The module only needs a `run(args, stdin)` function that returns text. The **module
guide** has the rules, a ready-to-copy template, a module tester (`python -m tools.modtest`), and a
prompt that has an AI write the `.md` and `.skill` for you:

**→ [templates/README.md](templates/README.md)**

## More documentation

| Document | For |
| --- | --- |
| [templates/README.md](templates/README.md) | Writing and testing your own modules |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Working on ShellCraft itself: layout, architecture, running the tests |
| [TODO.md](TODO.md) | Known issues and the roadmap |

<div align="center">

MIT licensed · [LICENSE](LICENSE)

<sub>The demo recording (<a href="docs/demo.cast">docs/demo.cast</a>) can be replayed in a terminal with <code>asciinema play docs/demo.cast</code>.</sub>

</div>
