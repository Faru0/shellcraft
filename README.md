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
| 🧰 **79 built-in commands** | `ls`, `cat`, `grep`, `find`, `sort`, `cut`, `tr`, `diff`, `du`, `tree`, `printf`, `seq`, `xargs`… written in Python, so they behave the same on every OS. |
| 🧩 **Pluggable modules** | Drop a `.py` (plus its `.md` manual and `.skill` AI description) into `modules/` and it's a new command, hot-reloaded as you edit. Turn any of them off with `modules disable`. |
| 🔁 **Loops & conditions** | Bash's `for`, `while`, `until`, `if` and `case`, or `for -py h in (cat hosts.txt) { if (h.endswith(".gov")) { queryDns $h } }`: no escaping, and conditions are sandboxed Python expressions. A whole loop can be piped: `done \| sort`. |
| ↩️ **Shell habits** | Variables (`x=1`, `$x`, `$HOME`, `$((n + 1))`), `$(…)`, globs, `$?`, `!!`, `!$`, `!N`, `;`, `&&`, `\|\|`, `<`, `2>`, `2>&1`, `>&2`, functions, `select`, `test` / `[ ]` / `[[ ]]`, with the exit status and git branch in the prompt. |
| ⌨️ **Smart input** | Syntax highlighting as you type, a toolbar with the usage of the command you are typing, ghost-text suggestions from history, Ctrl-R, and Tab completion for commands, switches (with descriptions), switch values, positional values and paths (quoted when they need it). |
| 📖 **Man pages & pager** | `man <command>` for everything, in a built-in scrollable viewer with search, wrapping, sideways scrolling and mouse support; `cmd \| less` pages anything else; `help NAME` for the short version. |
| 🎨 **Themes** | Cyberpunk, Matrix, Nord, Solarized, Dracula, Gruvbox, Catppuccin, Tokyo Night, two light variants, or your own; `theme preview` tries one. |
| 🪟 **Subshells** | `sub` opens your own shell, `sub vim`, `sub htop` or `sub ssh host` hands the terminal to a program, `sub shellcraft` nests a ShellCraft, and `sub grep -r TODO .` runs a command in the background. A footer on the last row shows every shell as a tab: click one, or press Ctrl-] N, to switch; Ctrl-] alone comes back. |
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

- `a | b | c` passes each command's output to the next command as input. All of them run at once, and lines flow as they are made: `yes | head -3` stops `yes`, and `tail -f app.log | grep ERROR` prints each error the moment it is written (Ctrl-C stops it). A pipeline that finishes quickly looks as before, colors and pager included.
- `> file` writes the final output to a file, and `>> file` appends to it.
- Quote arguments that contain spaces or operators: `grep "a | b" notes.txt`. Backslashes are kept as typed, so Windows paths like `C:\data\log.txt` work.
- If any command fails, the pipeline stops, an error panel names the failing step, and nothing is written to the redirect file.
- `cd` only works on its own line. `cd x | pwd` is refused, because it would change the shell's directory from inside a pipeline.

- `a ; b` runs one command after the other; `a && b` runs `b` only if `a` succeeded, `a || b` only if it failed; `! a` negates (see [Loops and conditions](#loops-and-conditions)).
- `cmd < file` feeds a file to the first command. A command's failure message goes where you send it: `cmd 2> err.txt` (or `2>>`), `cmd 2>&1 | grep …` (the message becomes the command's output, and the pipeline goes on), `cmd &> both.txt`.
- `cmd >&2` (or `1>&2`) sends a command's output to the error stream instead: `echo "error: no host given" >&2`
  shows in the error style and leaves `$?` alone. A `2> file` on the same line, or on the function call or
  loop around it (`myfn 2> err.txt`, `done 2>&1 | grep …`), decides where it goes.
- `*.log`, `?` and `[abc]` expand to matching file names (the word stays as typed when nothing matches); `$(command)` is replaced by the command's output; `$((n * 2))` is arithmetic.

A trailing `&` runs the line in the background, as a subshell job (see [Subshells](#subshells)):
`grep -r TODO . &`. An `&` anywhere else is rejected with a parse error rather than passed on as an
argument; quote it to use it as text.

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
select env in dev staging prod; do echo "deploy to $env"; break; done   # a numbered menu ($PS3, $REPLY)
for f in *.log; do wc -l $f; done | sort -n | tail -3     # a whole loop can be piped or redirected
[ -f hosts.txt ] && echo found || echo missing
```

- Expansion inside these blocks is bash's: an unquoted `$x` or `$(…)` is split at whitespace
  (quote it: `"$x"`), and the `for` list gets brace expansion and globs.
- Conditions are commands: `[ … ]`, `[[ … ]]` (with `==` patterns, `=~` regexes, `<` / `>` / `<=` / `>=`
  text order, Python's `in` / `not in` / `is` / `is not`, and `&&`, `||`,
  `!` and `( … )` inside: `[[ -f $f && ( $x == a || $x == b ) ]]`), `test`, `grep -q`,
  `true`/`false`. `!`, `&&` and `||` work anywhere. `man test` lists the tests.

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

### Functions

```
greet() { echo "hello $1, you gave $# argument(s)"; }
greet Ada

backup() {                                   # over several lines, at the prompt or in ~/.shellcraft/rc
  local stamp=$(date +%Y%m%d)                # local: put back when the function returns
  cp -r "$1" "$1.$stamp" && echo "saved $1.$stamp" || return 1
}
countdown() { local n=$1; while [ $n -gt 0 ]; do echo $n; n=$((n - 1)); done; }
countdown 3 | sort -n                        # a function is a command: pipes, redirects, && / ||
declare -f · unset -f backup · type greet    # list, forget, inspect
```

Inside a function `$1`…`$9` are its arguments, `$#` how many and `$@` all of them; `return N` sets
its status. Variables are the shell's own unless declared `local`, as in bash. A function wins over
a builtin or module of the same name (not over `exit`, `export`, `unset`, `source` and a few other
shell builtins), and `\name` runs the command itself. Functions last for the session (put them in
`~/.shellcraft/rc` to keep them) and are never available to AI clients over MCP. `man function`.


Inside a function, `"$@"` keeps each argument its own word, `shift` drops the first one, and
`${1:-default}` gives a default when an argument is missing:

```
each() { while [ $# -gt 0 ]; do echo "arg: $1"; shift; done; }
greet() { echo "hello ${1:-stranger}"; }
```
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
- **Completion follows the usage line** of each command and module (its `usage`, as `help` shows it): what may come next is offered, not file names. `cut <Tab>` offers `-f` and `-c`, which it can't run without; `mcp <Tab>` its actions, and `mcp token <Tab>` → `rotate`; `time <Tab>` a command; `grep foo <Tab>` the files. Where only a free value fits (`grep <Tab>`: a PATTERN) Tab on an empty word offers nothing, and the usage toolbar shows what to type. A module gets this from the `usage` in its `.skill`, with no code.
- **The mouse** works in the completion menu: the entry under the mouse is highlighted as you move, a click picks it, the wheel moves through the list. ShellCraft asks the terminal for the mouse only while a menu is open (and in the pager), so selecting text and wheel-scrolling the scrollback work as usual the rest of the time. `settings mouse off` turns it off; most terminals also let you select text under any program by holding **Shift**.
- **`settings mouse_always on`** keeps the mouse on at the prompt: click to place the cursor, click a prompt segment (`⚡` → `help`, the path → `ls`, `⎇ branch` → `git status`, `● mcp` → `mcp log`, `[✗ N]` → the last error again), click the toolbar to open the man page of the command you are typing, and roll the wheel over the input line to move through the history (or through the matches during Ctrl-R). The command runs above the prompt and what you typed stays. The cost: the terminal needs **Shift** for text selection and wheel scrollback, which is why it is off by default.
- **Clickable paths:** file names printed by `ls`, `tree`, `find`, `du` and `grep` can be terminal hyperlinks: `settings links on`, then Ctrl-click (Cmd-click on macOS) opens the file or folder, in Windows Terminal, GNOME Terminal, kitty, WezTerm, iTerm2 and others. Off by default, so a stray click in a listing opens nothing.
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
Aliases work in every step of a pipeline and apply in the shell, never to AI clients over MCP.
`which ls` shows whether a name is an alias.

They are kept in **`~/.shellcraft/aliases`**, a plain text file like bash's `~/.bash_aliases`:

```
# my shortcuts
alias ll='ls -l'
alias errors='grep -i error'
```

Edit it in any editor and the change applies to the next command, no restart needed. `alias` and
`unalias` edit the same file and leave your comments alone, `alias --file` prints its path, and a
line that can't be used (a `|` in the value, an unclosed quote) is skipped and listed by `alias`
with its line number. Aliases from older versions, saved in `config.json`, move into the file the
first time the new version starts.

### Long output

`man <command>` opens in a scrollable viewer (`man -p <command>` prints it instead), and so does
anything you pipe into **`less`** (`ls -l | less`, `cat big.log | less`, `less FILE`; `more` is the
same). Nothing else pages by itself: long output just prints, so you can scroll back through it.
In the viewer: `↑`/`↓` or `j`/`k` scroll, `PgUp`/`PgDn`/`space`
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
sub shellcraft --theme nord  # a lite ShellCraft in a subshell (its prompt shows ⚡shellcraft [1])
sub find / -name "*.log"     # a builtin or a module, run in the background with its output collected
find / -name "*.log" &       # the same, bash style: a trailing & backgrounds any line (a program isn't attached)
jobs                         # the table of subshells; jobs 2 selects one, jobs -k 2 stops it, jobs -c clears
jobs -r 2 api logs           # rename job 2: the footer shows "api logs" instead of its command
jobs -f 2                    # follow a background command's output as it grows, like tail -f (Ctrl-C stops following)
```

Every subshell is a numbered job. A **footer** on the last rows of the terminal lists them as a
stack, at the prompt and inside every subshell alike: `main` first, then one line per subshell,
newest at the bottom, growing as you open more (up to half the screen, then "… and N more"):

```
 ✖ Payment API is down · from mcp · claude-code                              crit
 ● main ▾                                                         ● 1  ● 1  ● 1 ▸
 ○ 1  bash --norc  ● Disk /home at 91%
 ○ 2  find / -name "*.log"                                                   done
```

The ● is the shell you are in. A running line is plain; a finished one is green (`done`) or
red (`exit 2`, `killed`), with an **×** on the right that removes it when you are done with it. The `▾` after `main`
(a click, or **Ctrl-] c**) folds the subshell lines into `main  2 subshells ▸` when you need
the room; the same again unfolds them. Alerts are part of the stack: see below. With no
subshell the footer is the `main` line alone; `settings footer off` goes back to a panel under
the prompt that appears only while something is running.

**↓** on an empty line moves into the footer (↑ / ↓ walk it, **Enter** switches, **x** or Delete
stops the chosen one, Esc leaves); **Ctrl-] N** switches from anywhere. The footer's lines, an
alert's ✖ and the badge are clickable at the prompt and inside a subshell. While the shell has
the mouse that way, select text with **Shift** and a drag. The **wheel** inside a subshell scrolls
the subshell's own scrollback; at the prompt, a turn of the wheel hands the mouse back to the
terminal, so the turns that follow scroll the terminal's own history as in any terminal, until the
next key press takes the mouse again. `settings scroll_view on` opens the **scroll view** instead:
the session's recent output in the viewer, at its end (wheel down past the end, or `q`, closes
it). With nothing in the footer to click, the terminal keeps the mouse and its own wheel.
`settings footer_mouse off` always leaves the mouse to the terminal at the prompt.

Subshells are shown like the pages of a book. Each program has its own terminal emulator, so
switching to it redraws its screen exactly as it was, scrollback included, and switching away
and back never garbles anything. They are shown on the terminal's alternate screen, so coming
back to the prompt restores it untouched. Inside a subshell the same footer sits at the bottom of the terminal, with the
● on the shell you are in: click a line or press Ctrl-] N to turn the page; **Ctrl-]** alone, or
the `main` line, brings you back to the prompt, and **Ctrl-] Ctrl-]** sends a Ctrl-] to the
program. The **mouse wheel** scrolls through the subshell's own scrollback (any key returns
to the live page), unless the program uses the mouse itself, as `htop` does. (Alt-N is not used:
terminals need it for their own tabs.) Switching to a command that is still running follows its output as it grows (Ctrl-C goes back to the prompt and leaves it running); a finished one shows everything it printed. A finished
job prints `[N] done: …` (and a command's output) at the next prompt and leaves the stack by
itself (`settings jobs_autoclear off` keeps it until `jobs -c`), jobs still running when you leave ShellCraft are
stopped, and `settings footer off` removes the footer. `settings panel_hint on` adds a header line
to the panel that lists these keys. `man sub` has the details.

`sub shellcraft` opens a **lite** ShellCraft: builtins, modules, settings, aliases and history,
without the banner, the startup scripts, hot reload, the alert watcher or the MCP server. Every
subshell belongs to the main shell; there is no subshell inside a subshell. `sub pwsh` or
`make &` typed inside one hands the request to the main shell, which runs it in the background:
it appears in the footer under `main`, you stay where you are, and Ctrl-] N switches to it.
`sub sc` is short for `sub shellcraft`, and `sc &` starts a lite ShellCraft in the background.

Programs run on their own pseudo-terminal: `pty` on Linux and macOS, ConPTY through the
`pywinpty` package on Windows (installed with the requirements there). Without `pywinpty`,
`sub PROGRAM` on Windows runs the program in the foreground instead (you are back when it
exits) and it is not listed as a job. If a footer line does not change color as its job runs and
ends, `diag` prints what the footer writes for each state and the live state of every job, to
paste into a bug report.

### Startup: the rc file and scripts

**`~/.shellcraft/rc`** is ShellCraft's `~/.bashrc`: a file of ShellCraft command lines that run when
the interactive shell starts, as if you typed them. Anything you can type works there:

```
theme nord
export PROJECT_ROOT=~/projects
cd ~/projects
sub find ~/projects -name "*.log" -mtime -1
alert -l info "shell ready"
```

It can also ask you something, Python style, and use the answer later:

```
name = input("What is your name? ")
echo "hello $name"
count = int(input("How many servers? "))   # asks again until it gets a whole number
for n in $(seq $count); do echo server-$n; done
read -s -p "API token: " token      # the bash spelling; -s hides what you type
```

As in bash, the rc file can pull in other files, so you can keep one per use case:

```
if [ -f ~/.shellcraft/rc.work ]; then
    . ~/.shellcraft/rc.work
fi
for f in ~/.shellcraft/rc.d/*.rc; do [ -f "$f" ] && source "$f"; done
```

A failing line is reported with its line number (`✗ rc:4: …`) and the rest still run.
`source FILE` runs any such file by hand (`source ~/.shellcraft/rc` reruns it). It never runs for
`--mcp` / `--mcp-http`. `templates/startup/rc.template` is a short starter (a question, a variable, `myip`, a loop) and `templates/startup/rc.example` a longer commented one.

For things a command line can't do, Python scripts in **`~/.shellcraft/startup/`** run when the interactive shell starts, in name order
(`10-env.py`, `20-mounts.py`), before the first prompt. Use them to set environment variables for
everything ShellCraft starts later, check a mount or a VPN, or print a reminder. A script is its own
process, so it hands variables back by appending `NAME=value` lines to the file named in
`$SHELLCRAFT_ENV_FILE`:

```python
import os
with open(os.environ["SHELLCRAFT_ENV_FILE"], "a") as f:
    f.write("PROJECT_ROOT=/srv/app\n")
```

The shell, every subshell and every module then see `PROJECT_ROOT`. A script that fails or runs
longer than 30 s is reported in one line and the shell starts anyway. They run before the rc file,
so its lines see their variables. `startup` lists them with their last result, `startup run` runs
them again, `settings startup off` or `--no-startup` skips both the scripts and the rc file.

A script that should keep running (a watcher, a tunnel, a periodic check) starts in the
background instead, with a header line, so the shell never waits for it:

```python
# shellcraft: background, restart
```

It becomes a subshell (`startup: 30-watcher.py` in the panel; Ctrl-] N attaches, **Ctrl-] x** ends
it), `restart` starts it again after a crash (giving up after 5 crashes in 10 minutes, with an
alert), and `startup stop / start / restart NAME` control it. `# shellcraft: timeout=120` gives a
normal script more than 30 s. `templates/startup/30-watcher.example.py` is an example. `templates/startup/10-example.py` is a commented example; `man startup` has the details.

### Alerts in the status panel

```
alert deploy finished                 # green, 15 s
alert -l warn disk at 90%             # amber, 60 s
alert -l crit pager is down           # red, stays until alert -c
alert --list · alert -c [N]           # see them, dismiss them
```

Alerts live in the panel under the prompt (and in the footer inside a subshell), three at most.
An alert sent from a subshell shows inline on that subshell's line, after its command, in its
color (`● Disk /home at 91%`; `+1` when it has more). The **badge** at the right end of the
`main` line counts every alert by level (`● 1  ● 1  ● 1 ▸`, a filled bubble in each level's color): a click on it unfolds one full row
per alert, most severe first, with the message, where it came from and the time left; another
click folds them back. A **crit** alert always keeps its full row, so it cannot be missed.
To dismiss an alert, click its **✖** at the start of the row (`alert -c` from the keyboard). With no subshell to fold into, every alert shows as a row.

An alert can carry **data**: `alert -d /var/log/app.log "errors in the log"` or
`make 2>&1 | alert -l crit build failed -d -`. A **right-click** on its row copies the payload
(or the message) into the shell's own clip register, which keeps the last ten copies: `$CLIP`
is the newest, `$CLIP1` the one before, up to `$CLIP9` (`clip -l` lists them). **Alt-v** pastes
`$CLIP` at the prompt, in the main shell and in a `sub shellcraft`; pressed again right away it
swaps in `$CLIP1`, then `$CLIP2`… (**Ctrl-] v** types the newest into another program in a
subshell). `clip -i N` starts the next prompt with copy N, `clip N` prints it, `clip | jq` pipes
the newest on. (**Alt-c** copies the top alert from the keyboard, `clip -a N` alert N. The left
Alt, the right Alt and AltGr all work, on Windows too; `diag keys` shows what a key arrives as. Konsole uses Alt-v for its View menu while its menu
bar is shown; Esc then v is the same key.) Alerts can come from anywhere: the main shell, a subshell
(`sub bash` and `sub pwsh` get an `alert` command on their PATH, so `make && alert done` works),
a nested ShellCraft, a script outside ShellCraft (`shellcraft-alert MESSAGE`), or an AI client
through the **`send_tui_alert`** MCP tool (`message`, `severity`, `timeout_seconds`). Each alert
says where it came from (`· from sub 2 (bash)`, `· from mcp · claude-code`). Senders drop a small
file into `~/.shellcraft/alerts/`, an owner-only spool folder the shell watches, so there is no
port and no token. `man alert` has the details.

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

## Keyboard and mouse

Every keyboard shortcut and mouse action in ShellCraft, by where it works, with what differs
between Linux and Windows. In the shell, `hotkeys` prints this list and `man hotkeys` opens it in the viewer.

### At the prompt: editing

| Keys | What they do |
| --- | --- |
| `Enter` | Run the line (a line with an open `{` or `(` asks for more) |
| `Tab` | Complete a command, a switch, a value or a path |
| `→` or `End` | Accept the grey suggestion from your history |
| `↑` `↓` | The previous / next command in the history |
| `Ctrl-R` | Search the history as you type |
| `Ctrl-A` `Ctrl-E` | The start / end of the line |
| `Alt-B` `Alt-F` | One word back / forward |
| `Ctrl-W` | Delete the word before the cursor |
| `Ctrl-U` `Ctrl-K` | Delete to the start / end of the line |
| `Ctrl-L` | Clear the screen |
| `Ctrl-C` | Drop the line (or stop the running command) |
| `Ctrl-D` | Leave ShellCraft, on an empty line |

### The clip register and alerts

| Keys or mouse | What they do |
| --- | --- |
| `Alt-v` | Paste the newest copy (`$CLIP`). Press it again right away for `$CLIP1`, `$CLIP2`… |
| `Esc` then `Ctrl-V` | The same paste (Ctrl+Alt+v: what AltGr+v sends) |
| `Alt-c` | Copy the top alert's content |
| Right-click an alert | Copy its content (`clip -a N` from the keyboard) |
| Click an alert's `✖` | Dismiss it (`alert -c N` from the keyboard) |
| Click the badge on `main` | Show / fold the alert rows |
| `clip -i [N]` | Start the next prompt with copy N, no key chord at all |

### The footer and subshells

| Keys or mouse | Where | What they do |
| --- | --- | --- |
| `Ctrl-]` then a digit | anywhere | Switch to subshell N (`0`: back to the prompt) |
| `Ctrl-]` alone | in a subshell | Back to the prompt; the subshell keeps running |
| `Ctrl-]` then `x` | in a subshell | End the subshell you are in |
| `Ctrl-]` then `c` | anywhere | Fold the subshell lines into `main`, or unfold them |
| `Ctrl-]` then `v` | in a subshell | Type the newest copy into the program (bash, pwsh, vim) |
| `Ctrl-]` twice | in a subshell | Send one `Ctrl-]` to the program |
| `↓` on an empty line | at the prompt | Into the footer: `↑` `↓`, `Enter` switches, `x` stops, `Esc` leaves |
| Click a line | anywhere | Switch to that shell |
| Click `▾` / `▸` after `main` | anywhere | Fold / unfold the subshell lines |
| Click the `×` of a finished line | anywhere | Remove it from the footer |

### Mouse: scrolling and selecting

| Mouse | When | What it does |
| --- | --- | --- |
| Wheel | nothing to click in the footer | The terminal's own scrolling |
| Wheel | the footer lists subshells or alerts | The terminal's own scrolling (the first turn hands it the mouse back) |
| Wheel | inside a subshell | Scrolls that subshell's output (a full-screen program such as `less` gets arrow keys) |
| Drag | nothing to click in the footer | The terminal's own selection |
| `Shift` + drag | the shell has the mouse (footer items, or a subshell) | The terminal's own selection |

Copy and paste of selected text is the terminal's own: on Linux usually `Ctrl-Shift-C` and
`Ctrl-Shift-V` (and a middle click pastes the selection); in Windows Terminal `Ctrl-C` with a
selection and `Ctrl-V`, or `Ctrl-Shift-C` and `Ctrl-Shift-V`. `settings footer_mouse off` leaves
the mouse to the terminal at the prompt; `settings mouse off` turns ShellCraft's mouse off.

### The viewer (man, less, the scroll view)

| Keys | What they do |
| --- | --- |
| `↑` `↓` or `j` `k` | One line |
| `PgUp` `PgDn`, `b` `f`, `Space` | One screen |
| `d` `u` | Half a screen |
| `g` `G`, `Home` `End` | The top / the end |
| `←` `→` | Sideways, when lines are not wrapped |
| `w` | Wrap long lines on / off |
| `/` then `n` `N` | Search, then the next / previous match |
| Wheel, the bar on the right | Scroll; click or drag the bar |
| `?` | The keys |
| `q` or `Esc` | Close |

### Linux and Windows

| | Linux | Windows 11 |
| --- | --- | --- |
| Alt shortcuts (`Alt-v`, `Alt-c`) | Left and right Alt; AltGr unless it types a character | Left, right Alt and AltGr |
| Alt does not arrive | Press `Esc` then the key (Konsole's menu bar takes Alt-v) | Press `Esc` then the key |
| Subshells (`sub`) | Always | Need the `pywinpty` package (the banner says when it is missing) |
| Terminal | Any modern terminal | Windows Terminal or the classic console; keys and the mouse are read the way prompt_toolkit's own Windows support does it (VT input when the console takes it) |
| What is missing | The banner and `diag` name a missing package (`pyte`, `keyring`) | The same, plus `pywinpty`: without it there are no subshells, no footer and no mouse there |

`diag keys` shows what each key press reaches the shell as: Alt-v must show `escape v`. On
Windows it also shows the console's Alt flags, to paste into a bug report. When a key does not
show there at all, `diag keys --raw` prints what the console itself delivers, below the key
reader (`--vt` with the console's VT input on), and `diag keys --raw --mouse` the same for
the mouse wheel and clicks.

---

## Built-in commands

Built into ShellCraft (written in Python), so they work the same on every operating system. Each
one has a manual: `man ls`, `man grep`, …

| Group | Commands |
| --- | --- |
| Shell | `cd` `pwd` `exit` `clear` `help` `man` `theme` `prompt` `settings` `alias` `unalias` `history` `modules` `reload` `banner` `mcp` `sub` `jobs` `ingest` `export` `unset` `xargs` `sleep` `time` `test` `[` `[[` `true` `false`, and the keywords `for` `while` `until` `if` `case` `record` `script` `diag` `hotkeys` `mark` `review` |
| Text | `echo` `printf` `cat` `grep` `head` `tail` `wc` `sort` `uniq` `cut` `tr` `tee` `diff` `seq` `jq` `clip` `less` `more` |
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
| `clear_on_start` | on | Start on a fresh screen (the scrollback is cleared too, as `clear` does). |
| `toolbar` | on | Show the usage of the command being typed under the prompt. |
| `highlight` | on | Color the command line as you type. |
| `transient_prompt` | off | Collapse the prompt to one line after Enter. |
| `mouse` | on | Click and scroll in the completion menu and the pager; the terminal keeps the mouse at other times. |
| `mouse_always` | off | Keep the mouse on at the prompt: click to place the cursor, click prompt segments and the toolbar, wheel through history. Hold Shift to select text. |
| `links` | off | File names from `ls`, `tree`, `find`, `du` and `grep` are hyperlinks: Ctrl-click opens them. |
| `keyring` | on | Keep API keys in the OS keyring when the `keyring` package is installed (else plain text in `config.json`). |
| `startup` | on | Run `~/.shellcraft/startup/*.py` and `~/.shellcraft/rc` when the interactive shell starts. |
| `footer_mouse` | on | While the footer lists a subshell or an alert, the shell has the mouse at the prompt: its lines, an alert's ✖ and the badge are clickable, text is selected with Shift, and the wheel hands the mouse back to the terminal. Off: the terminal keeps the mouse at the prompt. |
| `scroll_view` | off | A turn of the wheel at the prompt (while the shell has the mouse) opens the scroll view of the session's output instead of handing the mouse to the terminal. |
| `footer` | on | The shell stack on the last rows of the terminal, at the prompt and inside every subshell (click a line, or Ctrl-] N, to switch; Ctrl-] c folds it). Off: a panel under the prompt only while something is running, and nothing inside subshells. |
| `jobs_autoclear` | on | A finished subshell or background command leaves the stack at the next prompt, after its `[N] done` note and a command's output. Off: it stays until `jobs -c`. |
| `panel_hint` | off | Show a header line at the top of the subshell panel listing its keys (click or Ctrl-] N switches, Ctrl-] alone returns, Ctrl-] x ends it). |

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

- **Storage:** with the optional `keyring` package (`pip install keyring`, or `pip install -e ".[keyring]"`) and a working backend (Windows Credential Manager, macOS Keychain, Secret Service), a key goes into the OS keyring and `config.json` holds only the marker `@keyring`. **Without it, keys are plain text** under `"env"` in `~/.shellcraft/config.json`: mode 600 keeps other users out on Linux and macOS, but **does nothing on Windows**. `settings migrate-keys` moves keys already stored in plain text into the keyring; `settings keyring off` keeps them in `config.json`.
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
In a subshell (`sub shellcraft`), `theme NAME` changes that subshell only. `theme --sub NAME`
saves a theme for every subshell to start with, `theme --sub` shows it and `theme --sub --clear`
removes it (subshells then start with the main theme).

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
stop it, and the prompt shows `● mcp :38765` while it runs) and prints the
`claude mcp add … --header "Authorization: Bearer …"` line to use. The token is made once and
**saved** (in the OS keyring when there is one, else in `config.json`), and so is the address: every
`mcp start`, in any session, uses the same ones, so a client added once keeps working. `mcp show`
prints the token and the `claude mcp add` line again; `mcp token rotate` makes a new token (a
running server restarts with it) and prints `claude mcp remove shellcraft` and the new `claude mcp
add` line to run. `mcp` shows the status, `mcp log` the log, and `mcp stop` stops it. It also stops when you leave the
shell, even if the shell is killed outright. See `man mcp`.

- **One tool per module that has a `.skill` file** (a module without one is a shell command only), with typed parameters from it, e.g. `{"pattern": "error", "ignore_case": true, "stdin": "..."}`.
- **`send_tui_alert`** shows a short alert in the running shell's status panel (`message`, `severity`: info / warn / crit, `timeout_seconds`, optional `content`: a payload you copy with a right-click and paste with Alt-v), so a model can flag a finished task or a risk where you will see it, and hand you a path or a command along with it.
- **The review loop:** `get_flagged_items`, `request_human_review`, `get_review_status` and `resolve_review_item`, plus the `shellcraft://review/queue` resource. See **Reviewing with an AI** below.
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

### Reviewing with an AI

A two-way queue between you and an AI client, shared through files in `~/.shellcraft/review/`
(readable by you only), so it works with the `mcp start` server and with every client's own
`--mcp` server alike.

- **You flag things for the AI** with `mark`: what you pipe in, files, or a diff.
  ```
  pytest | mark --type test-failure -p high
  mark --file app.log --file config.toml -m "startup crash"
  mark --diff old.py new.py
  git diff | mark --type diff
  ```
  The AI reads them with `get_flagged_items` (or the `shellcraft://review/queue` resource) and
  closes them with `resolve_review_item`, saying what it did. Each piece keeps its last 256 KB.
  What you mark is stored as is, so mark only what an AI may read.
- **The AI asks you before destructive actions** with `request_human_review` (a title, a
  description, the commands or diffs involved, a priority). It shows in the footer as an alert,
  `info`, `warn` or `crit` for a low, medium or high priority, until you answer:
  ```
  review                       # what is pending
  review show r-…a1b2c3        # one item in full (the last characters of the id are enough)
  review approve a1b2c3 go ahead
  review reject a1b2c3 not on Fridays
  ```
  The AI either waits for your answer (`wait_seconds`, up to a little under the call timeout)
  or asks again with `get_review_status`, and goes ahead only on `approved`. This is advisory:
  ShellCraft's server instructions tell the AI to ask, but it can't stop what the AI does with
  its own tools.

Connected clients are told when the queue changes (a resource-updated notification for
`shellcraft://review/queue`). Items that are done are deleted after 7 days; pending ones stay
until answered.

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
| [templates/README.md](templates/README.md) | Writing and testing your own modules |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Working on ShellCraft itself: layout, architecture, running the tests |
| [CHANGELOG.md](CHANGELOG.md) | What changed in each release |

<div align="center">

MIT licensed · [LICENSE](LICENSE)

<sub>The demo recording (<a href="docs/demo.cast">docs/demo.cast</a>) can be replayed in a terminal with <code>asciinema play docs/demo.cast</code>.</sub>

</div>
