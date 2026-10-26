# Getting started

← [Manual](README.md)

This page takes about ten minutes. You'll install ShellCraft, start it, and try the things you'll
use every day: running commands, piping them together, finding help, and changing how it looks.

## Install

You need Python 3.11 or newer. Get the code and install what it needs into a virtual environment:

```bash
git clone https://github.com/Faru0/shellcraft.git
cd shellcraft
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On Windows, use `py -m venv .venv` and activate it with `.venv\Scripts\Activate.ps1`. If PowerShell
refuses to run that script, run `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser` once.

To get a `shellcraft` command that works from any folder, also run `pip install .` (or
`pip install -e .` if you plan to edit ShellCraft itself).

## Start it

```bash
python main.py
```

You'll see the banner, a loading bar for a moment, then the prompt:

```
╭─ ⚡shellcraft ─ you@laptop ─ ~/shellcraft ─ ⎇ main
╰─❯
```

The first time it starts, ShellCraft creates a `~/.shellcraft` folder with a few starter files in
it. Nothing in them runs until you change them. [Your ~/.shellcraft folder](your-shellcraft-folder.md)
explains each one.

To leave, type `exit` or press Ctrl-D.

## Run a few commands

ShellCraft has its own versions of the commands you know from Linux, written in Python, so they
work the same on Windows, macOS and Linux:

```
ls
cd ~
pwd
echo hello
date
```

Start typing a command and press **Tab** to complete it. Tab also completes switches, with a
short description of each, and file names:

```
grep -<Tab>
```

As you type, a grey suggestion from your history appears after the cursor. Press **→** to take it.
Press **Ctrl-R** to search everything you've typed before.

## Pipe commands together

The output of one command can be the input of the next. This counts the error lines in a log,
grouped by message:

```
cat app.log | grep ERROR | sort | uniq -c
```

`>` writes the result to a file, and `>>` adds to the end of one:

```
grep -i error app.log > errors.txt
```

[Pipes and scripting](scripting.md) covers variables, loops and conditions too.

## Find help

```
help          # every command, grouped by what it's for
man grep      # the full manual for one command
help grep     # just its usage line and summary
hotkeys       # every keyboard shortcut
```

`man` opens a viewer you can scroll with the arrow keys or the mouse. Press `/` to search and `q`
to close it.

## What's built in

`help` lists every command, grouped. Each one has a manual: `man ls`, `man grep`.

| Group | Commands |
| --- | --- |
| Text | `echo` `printf` `cat` `grep` `head` `tail` `wc` `sort` `uniq` `cut` `tr` `tee` `diff` `seq` `yes` `jq` `less` `more` |
| Files | `ls` `find` `tree` `du` `touch` `mkdir` `cp` `mv` `rm` `basename` `dirname` `realpath` `stat` |
| System | `date` `which` `type` `command` `env` `whoami` `hostname` `uname` `curl` |
| Shell | `cd` `pwd` `exit` `clear` `help` `man` `hotkeys` `theme` `prompt` `settings` `alias` `history` `modules` `reload` `startup` `profile` `func` `source` `export` `read` `sleep` `time` `xargs` `test` |
| Scripting | `for` `while` `until` `if` `case` `select` `function` |
| Subshells and alerts | `sub` `jobs` `alert` `clip` `record` `script` |
| AI and MCP | `mcp` `ai` `watch` `tasks` `findings` `mark` `tell` `review` `ingest` |

Colored output on screen turns into plain text when you pipe it or write it to a file, so
`ls | wc -l` and `grep -n TODO *.py > todo.txt` give clean results. `rm` refuses to delete a
filesystem root, your home folder, or the folder you're in.

## Try a module

Modules are commands added on top of the built in ones. These come with ShellCraft:

| Module | What it does |
| --- | --- |
| `myip` | Your public IP, or where any IP is. `-p PORT` checks whether a port is open |
| `ip2geo` | Where an IP or domain is: country, city, ISP, and whether it's a proxy or a hosting provider |
| `queryDns` | A domain's DNS records with each IP's owner (DnsDumpster, needs a free key) |
| `queryCert` | Certificates and subdomains from Certificate Transparency logs |
| `queryCensys` | Open ports and software on a host (Censys, needs a token) |

```
ip2geo 8.8.8.8
```

`modules` lists every module, and `man ip2geo` explains one. You can write your own: see
[Your first module](modules/first-module.md).

## Make it look the way you like

```
theme              # the themes, with a color preview
theme nord         # switch, and keep it for next time
settings           # every setting, on or off
```

## Open another program

`sub` runs another program while keeping ShellCraft open. Its name appears in the footer at the
bottom of the screen:

```
sub htop
```

Press **Ctrl-]** to come back to the prompt while it keeps running, and **Ctrl-] 1** to return to
it. [Subshells](subshells.md) has the rest.

## Where to go next

- Add your own shortcuts and startup commands: [Aliases and history](aliases-and-history.md) and [Startup](startup.md)
- Write your own command: [Your first module](modules/first-module.md)
- Copy ready-made setups: [Cheat sheet](cheatsheet.md)
