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
| 🧰 **61 built-in commands** | `ls`, `cat`, `grep`, `find`, `sort`, `cut`, `tr`, `diff`, `du`, `tree`, `printf`, `seq`, `xargs`… written in Python, so they behave the same on every OS. |
| 🧩 **Pluggable modules** | Drop a `.py` (plus its `.md` manual and `.skill` AI description) into `modules/` and it's a new command, hot-reloaded as you edit. Turn any of them off with `modules disable`. |
| 🔁 **Loops & conditions** | Bash's `for`, `while`, `until`, `if` and `case`, or `for -py h in (cat hosts.txt) { if (h.endswith(".gov")) { queryDns $h } }`: no escaping, and conditions are sandboxed Python expressions. A whole loop can be piped: `done \| sort`. |
| ↩️ **Shell habits** | Variables (`x=1`, `$x`, `$HOME`, `$((n + 1))`), `$(…)`, globs, `$?`, `!!`, `!$`, `!N`, `;`, `&&`, `\|\|`, `<`, `2>`, `2>&1`, `test` / `[ ]` / `[[ ]]`, with the exit status and git branch in the prompt. |
| ⌨️ **Smart input** | Syntax highlighting as you type, a toolbar with the usage of the command you are typing, ghost-text suggestions from history, Ctrl-R, and Tab completion for commands, switches (with descriptions), switch values, positional values and paths (quoted when they need it). |
| 📖 **Man pages & pager** | `man <command>` for everything, in a built-in scrollable viewer with search, wrapping, sideways scrolling and mouse support; `help NAME` for the short version. |
| 🎨 **Themes** | Cyberpunk, Matrix, Nord, Solarized, Dracula, Gruvbox, Catppuccin, Tokyo Night, two light variants, or your own; `theme preview` tries one. |
| ⤷ **Subshells** | `sub` opens your own shell, `sub vim`, `sub htop` or `sub ssh host` hands the terminal to a program, `sub shellcraft` nests a ShellCraft, and `sub grep -r TODO .` runs a command in the background. Ctrl-] detaches, the bar under the prompt lists them, Alt-N (or a click) selects one. |
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

- `a ; b` runs one command after the other; `a && b` runs `b` only if `a` succeeded, `a || b` only if it failed; `! a` negates (see [Loops and conditions](#loops-and-conditions)).
- `cmd < file` feeds a file to the first command. A command's failure message goes where you send it: `cmd 2> err.txt` (or `2>>`), `cmd 2>&1 | grep …` (the message becomes the command's output, and the pipeline goes on), `cmd &> both.txt`.
- `*.log`, `?` and `[abc]` expand to matching file names (the word stays as typed when nothing matches); `$(command)` is replaced by the command's output; `$((n * 2))` is arithmetic.

Background jobs (`cmd &`) are not supported; a lone `&` is rejected with a parse error rather than
passed on as an argument. Quote it to use it as text.

### Variables

```
host=example.com                 # a shell variable for this session
queryDns $host                   # $name expands anywhere outside single quotes
echo "$HOME $PATH"               # environment variables work too (never over MCP)
export API_URL=https://x.test    # ... and export makes a variable one; unset removes it
n=$((n + 1))                     # integer arithmetic: + - * / % ** comparisons, && || !
```

A value with spaces stays one argument on a plain command line (`f="my file"; cat $f`); inside
bash-style blocks it is split at whitespace unless quoted, as in bash.

### Loops and conditions

Two syntaxes. **Bash** is the default, and works the way you'd type it in bash:

```
for ip in 1.1.1.1 8.8.8.8; do ip2geo $ip; done
for h in $(cat hosts.txt); do queryDns $h >> dns.txt; done
for n in {1..5}; do echo $n; done                         # {a,b}, {01..10}, {a..e} too
for f in *.log; do if grep -q ERROR "$f"; then echo "$f"; fi; done

for n in {1..10}                                          # an unfinished loop continues on the next line
do
  if [ $n -gt 5 ]; then echo big $n; elif [[ $n == 1* ]]; then echo ten-ish; else echo $n; fi
done

n=0; while [ $n -lt 3 ]; do echo $n; n=$((n+1)); done     # while and until
case $f in *.py) echo python;; *.md|*.txt) echo text;; *) echo other;; esac
for f in *.log; do wc -l $f; done | sort -n | tail -3     # a whole loop can be piped or redirected
[ -f hosts.txt ] && echo found || echo missing
```

- Expansion inside these blocks is bash's: an unquoted `$x` or `$(…)` is split at whitespace
  (quote it: `"$x"`), and the `for` list gets brace expansion and globs.
- Conditions are commands: `[ … ]`, `[[ … ]]` (with `==` patterns and `=~` regexes), `test`,
  `grep -q`, `true`/`false`. `!`, `&&` and `||` work anywhere. `man test` lists the tests.

**`-py`** is ShellCraft's own syntax for when quoting gets in the way: the loop is parsed by
ShellCraft, `$x` is always **one** argument whatever it contains, and conditions are Python.

```
for -py ip in (cat ips.txt) {                              # one item per line of output
  if (ip.startswith("10.")) { echo internal $ip }
  elif (ip in ("8.8.8.8", "1.1.1.1")) { echo dns $ip }
  else { ip2geo $ip }
}
for -py n in 1..5 { if (int(n) % 2 == 0) { echo even $n } }
if -py (match(r"^v\d+", ver)) { echo versioned }
```

- Python conditions are checked and evaluated by ShellCraft (no `eval`): comparisons,
  `and`/`or`/`not`, `in`, slicing, string methods, `len`, `int`, `match(regex, text)`,
  `exists(path)` and a few more; imports and other functions are refused. `status` is `$?`.
- Inside a `-py` block, nested `for`/`if` use the same syntax without the flag.

In both: `break`/`continue`; a failing command shows its error and the loop goes on, as in bash;
loop variables and `x=1` assignments outlive the loop. `man for` and `man if` have the details.
Loops also work over MCP, with the same restrictions.

### Exit status and history

- `$?` is the last command's exit status: 0 for success, 1 for a failure, 2 for a syntax error,
  127 for an unknown or disabled command, 130 after Ctrl-C, and an OS program's own code. `grep`
  returns 1 when nothing matches, without an error panel, so `if grep -q …` works.
- The prompt shows `[✗ 127]` after a command fails.
- `!!` is the previous command, `!$` its last word and `!*` its other words, as in bash: `sudo !!`,
  `cat !$`. `!N` is command N of `history` (`!-2`: two back) and `!prefix` the last command that
  starts with `prefix`. The expanded line is printed before it runs and saved in the history (not
  inside single quotes; `!=`, `hi!` and `[[ ! -f x ]]` are left alone).
- The prompt shows `⏱ 4.2s` after a command that took more than two seconds; `time CMD` prints
  the exact time of one command.

### Typing helpers

- **Syntax highlighting:** known commands are green, unknown ones red, operators in the accent color, quotes, switches and `$variables` in their own colors (`settings highlight off` turns it off).
- **The toolbar** under the prompt shows the usage of the command you are typing, or says it is not found (`settings toolbar off`).
- **Ghost text:** start typing a command you've used before, and a faint suggestion appears. Press **→** to accept it. **Ctrl-R** searches the history as you type.
- **Tab** completes command names and aliases, **switches** for every command (`grep -<Tab>` shows what each one does), **switch values** (`ip2geo -o <Tab>`, `find . -type <Tab>`), **positional values** from a module's `.skill` (`queryCensys <Tab>` → `host cert search`), arguments for `help`, `man`, `theme`, `prompt`, `settings`, `modules` and `unalias`, commands inside `for` / `if` blocks, and paths everywhere else. A name with spaces is quoted for you, and completion works inside quotes.
- **The mouse** works in the completion menu: the entry under the mouse is highlighted as you move, a click picks it, the wheel moves through the list. ShellCraft asks the terminal for the mouse only while a menu is open (and in the pager), so selecting text and wheel-scrolling the scrollback work as usual the rest of the time. `settings mouse off` turns it off; most terminals also let you select text under any program by holding **Shift**.
- **`settings mouse_always on`** keeps the mouse on at the prompt: click to place the cursor, click a prompt segment (`⚡` → `help`, the path → `ls`, `⎇ branch` → `git status`, `● mcp` → `mcp log`, `[✗ N]` → the last error again), click the toolbar to open the man page of the command you are typing, and roll the wheel over the input line to move through the history (or through the matches during Ctrl-R). The command runs above the prompt and what you typed stays. The cost: the terminal needs **Shift** for text selection and wheel scrollback, which is why it is off by default.
- **Clickable paths:** file names printed by `ls`, `tree`, `find`, `du` and `grep` are terminal hyperlinks. Ctrl-click (Cmd-click on macOS) opens the file or folder, in Windows Terminal, GNOME Terminal, kitty, WezTerm, iTerm2 and others; the rest show plain names. `settings links off` turns it off.
- **Ctrl-C** cancels the current line or a running command. **Ctrl-D** or `exit` leaves the shell.
- A line that ends inside `{ … }` or `( … )` asks for another line (`┆ …`); the whole command is saved to the history as one line.
- `banner` shows the welcome banner again (`banner -c` clears the screen first). `settings clear_on_start off` keeps the screen at startup.
- **The prompt** shows `user@host`, the directory, `⎇ branch` in a git repository, `● mcp :38765` while `mcp start` runs, `[✗ N]` after a failure and `⏱` after a slow command. `prompt name path git status` picks the segments, `prompt -r clock` adds a right-hand clock, `prompt reset` goes back (`man prompt`). `settings transient_prompt on` collapses the two-line prompt to `❯ command` after Enter, so the scrollback stays dense.

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
page, `d`/`u` half a page, `g`/`G` jump to the top or end, `←`/`→` scroll sideways through long
lines and `w` wraps them instead, `/` searches (matches are highlighted), `n`/`N` step through
matches, the mouse wheel scrolls, `?` lists the keys, and `q` closes it. The status bar names the
command whose output you are reading, and its keys (`/`, `n`, `w`, `?`, `q`) can be clicked; the bar
on the right is a scrollbar you can click or drag, and a click on a line marks it so `n` / `N`
search on from there. Turn the viewer off with `settings pager off`.

### Subshells

```
sub                          # your own shell (bash, zsh, pwsh…); exit brings you back to ShellCraft
sub vim notes.md             # any program, with the keyboard and the screen handed to it
sub htop                     # Ctrl-] detaches: it keeps running, you are back at the prompt
sub shellcraft --theme nord  # a nested ShellCraft (its prompt shows ⚡shellcraft ⤷1)
sub find / -name "*.log"     # a builtin or a module, run in the background with its output collected
jobs                         # the table of subshells; jobs 2 selects one, jobs -k 2 stops it, jobs -c clears
```

Every subshell is a numbered job. While there are any, a bar sits under the prompt, bottom left:
`⤷ 1:bash ● 2:find / … ✓` (● running, ✓ done, ✗ failed). **Alt-N** selects job N: a program is
attached again (its last lines are shown, full-screen programs redraw), a command's output so far
is shown. With `settings mouse_always on` a click on the bar does the same, and the entry under
the mouse lights up. A finished job prints `⤷ [N] done: …` at the next prompt, and jobs still
running when you leave ShellCraft are stopped. `man sub` has the details.

Programs run on their own pseudo-terminal, which Windows does not have: there `sub PROGRAM` runs
the program in the foreground (you are back when it exits), and background commands work the same
as everywhere else.

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
| `--expose NAME,...` | With `--mcp` / `--mcp-http`: offer only these modules as tools. |
| `--token TOKEN`, `--no-token` | With `--mcp-http`: the bearer token clients must send (default: a random one, printed at startup), or none. |
| `--version`, `-h` | Show the version, or the help text. |

| Environment variable | Meaning |
| --- | --- |
| `SHELLCRAFT_HOME` | Where config and history are stored (default `~/.shellcraft`). |
| `SHELLCRAFT_MODULES` | The default modules folder (overridden by `--modules`). |
| `SHELLCRAFT_MCP_TOKEN` | The bearer token for `--mcp-http` (overridden by `--token`). |
| `SHELLCRAFT_MCP_TIMEOUT` | Seconds an MCP tool call may take (default 120). |

</details>

---

## Built-in commands

Built into ShellCraft (written in Python), so they work the same on every operating system. Each
one has a manual: `man ls`, `man grep`, …

| Group | Commands |
| --- | --- |
| Shell | `cd` `pwd` `exit` `clear` `help` `man` `theme` `prompt` `settings` `alias` `unalias` `history` `modules` `reload` `banner` `mcp` `sub` `jobs` `ingest` `export` `unset` `xargs` `sleep` `time` `test` `[` `[[` `true` `false`, and the keywords `for` `while` `until` `if` `case` |
| Text | `echo` `printf` `cat` `grep` `head` `tail` `wc` `sort` `uniq` `cut` `tr` `tee` `diff` `seq` |
| Files | `ls` `find` `tree` `du` `touch` `mkdir` `cp` `mv` `rm` `basename` `dirname` `realpath` `stat` |
| Info | `date` `which` `type` `env` `whoami` `hostname` `uname` |

- **Colored on screen, plain when piped:** `ls` shows colored columns, `tree` draws a colored tree, and `grep` highlights matches. Piped or redirected, they output plain text, one item per line.
- **`which NAME`** tells you whether a name runs an alias, a builtin, a module or an OS program.
- **`rm` safety:** there is no trash can, so `rm` refuses a filesystem root, your home folder, and the current folder or its parents.
- **`diff -u old new`** gives the Git-style format; **`du -sh`** totals a folder; **`history | grep ssh`** finds an old command.
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

## Settings

`settings` shows every setting and the API keys modules need. `settings KEY on|off|toggle` changes
a setting immediately, and `settings reset KEY` restores its default. Everything is saved in
`~/.shellcraft/config.json`.

| Key | Default | Meaning |
| --- | --- | --- |
| `system_commands` | off | Allow OS programs from your `PATH` (see [OS commands](#os-commands)). |
| `pager` | on | Open man pages, and output taller than the window, in the scrollable viewer. |
| `spinner` | on | Show a spinner while slow commands run (with the elapsed time after 3 s). |
| `banner` | on | Show the startup banner. |
| `diagnostics` | off | Print the console diagnostics report at startup. |
| `hot_reload` | on | Reload modules when their `.py`, `.md` or `.skill` files change, in the shell and in the MCP server. |
| `lazy_modules` | on | Import a module's code the first time it runs, not at startup. |
| `clear_on_start` | on | Clear the screen when the interactive shell starts. |
| `toolbar` | on | Show the usage of the command being typed under the prompt. |
| `highlight` | on | Color the command line as you type. |
| `transient_prompt` | off | Collapse the prompt to one line after Enter. |
| `mouse` | on | Click and scroll in the completion menu and the pager; the terminal keeps the mouse at other times. |
| `mouse_always` | off | Keep the mouse on at the prompt: click to place the cursor, click prompt segments and the toolbar, wheel through history. Hold Shift to select text. |
| `links` | on | File names from `ls`, `tree`, `find`, `du` and `grep` are hyperlinks: Ctrl-click opens them. |

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
`tokyonight`, plus `solarized-light` and `catppuccin-latte` for a light terminal background.
`theme` lists them with color swatches, `theme NAME` switches immediately and saves your choice,
`theme preview NAME` tries one without saving, and `theme reset` goes back to the default.

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
`gradient_from` and `gradient_to`, as `#rrggbb` hex values, plus `surface` and `surface_text`
(the completion menu and toolbars; derived from `muted` when left out) and `"light": true` for a
light terminal. An invalid color is replaced by the base theme's color, with a warning shown in
the banner.

</details>

---

## Use it from an AI client (MCP)

`python main.py --mcp` starts an MCP server over stdio; `python main.py --mcp-http 127.0.0.1:38765`
serves the same tools over streamable HTTP at `http://127.0.0.1:38765/mcp` (this machine only, with
DNS-rebinding protection).

The HTTP server requires a **bearer token** on every request, because any local user or process
can reach a loopback port: pass `--token TOKEN` (or set `SHELLCRAFT_MCP_TOKEN`), let it generate
one (printed at startup), or turn it off with `--no-token`.

To keep using the shell while the HTTP server runs, start it from inside the shell instead:
`mcp start [HOST:PORT]` runs it in the background as a child process (Ctrl-C at the prompt doesn't
stop it, and the prompt shows `● mcp :38765` while it runs), generates a token and prints the
`claude mcp add … --header "Authorization: Bearer …"` line to use; `mcp` shows its status and
the token again, `mcp log` its log, and `mcp stop` stops it. It also stops when you leave the
shell, even if the shell is killed outright. See `man mcp`.

- **One tool per module**, with typed parameters from its `.skill` file, e.g. `{"pattern": "error", "ignore_case": true, "stdin": "..."}`.
- **`shellcraft_pipeline`** runs a whole command line, such as `{"command": "cat notes.txt | grep -i todo | sort"}`, using the read-only builtins. It **cannot** redirect to files, change files, read environment variables, change the shell's state or settings, or run OS programs (unless you start the server with `--allow-system`).
- **Man pages as resources** at `shellcraft://man/<name>`.
- **Hot reload:** editing, adding or removing a module updates connected clients' tool lists.
- **Limits:** a call that runs longer than `SHELLCRAFT_MCP_TIMEOUT` seconds (default 120) is abandoned with an error, and stdin and output are capped (10 MB in, 2 MB out).
- **Local-only modules:** `expose = false` in a module's `.skill` keeps it out of the tool list and out of `shellcraft_pipeline`; `--expose NAME,...` offers only the modules named.

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
Over HTTP: `claude mcp add --transport http shellcraft http://127.0.0.1:38765/mcp --header "Authorization: Bearer TOKEN"`.
To poke at the server by hand: `npx @modelcontextprotocol/inspector .venv/bin/python main.py --mcp`.

> [!WARNING]
> Connected AI clients can read any file your user account can read (through `fetch`, `cat`,
> `grep`, …), and `fetch` can request any URL. They can't change files. Modules that use your API
> keys (`queryDns`, `queryCensys`) spend your quota when an AI calls them. Only connect AI clients
> you trust.

---

## Write your own module

A module is three files in `modules/`: the code (`.py`), its manual (`.md`) and its AI description
(`.skill`). The module only needs a `run(args, stdin)` function that returns text.

Already have a Python script? `ingest myscript.py` inside the shell (or `shellcraft ingest
myscript.py` from a terminal) converts it with the fewest possible changes, writes the `.md` and
`.skill`, and checks the result; the new command works at the next prompt. It can also hand the
job to Claude or OpenAI. `ingest check --all` checks every module. The **module guide** has the
details, the rules, a reference module and the checker:

**→ [templates/README.md](templates/README.md)**

## More documentation

| Document | For |
| --- | --- |
| [templates/README.md](templates/README.md) | Writing and testing your own modules |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Working on ShellCraft itself: layout, architecture, running the tests |
| [CHANGELOG.md](CHANGELOG.md) | What changed in each release |
| [TODO.md](TODO.md) | Known issues and the roadmap |

<div align="center">

MIT licensed · [LICENSE](LICENSE)

<sub>The demo recording (<a href="docs/demo.cast">docs/demo.cast</a>) can be replayed in a terminal with <code>asciinema play docs/demo.cast</code>.</sub>

</div>
