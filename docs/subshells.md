# Subshells, alerts and the clip

← [Manual](README.md)

ShellCraft can run other programs next to it, each in its own subshell, and switch between them
from a footer at the bottom of the screen. This page covers subshells, the footer, alerts you can
send from anywhere, and the clip register that keeps what you copy.

## Open a subshell

```
sub                          # your own shell: bash, zsh, pwsh…
sub htop                     # any program, with the whole screen
sub vim notes.md
sub ssh myserver
sub shellcraft               # a light ShellCraft inside ShellCraft (sub sc for short)
```

The program takes over the screen. When it exits, you're back at the prompt.

To get back to the prompt while it keeps running, press **Ctrl-]**. To go back to it, press
**Ctrl-]** and its number, **Ctrl-] 1** for the first one. Pressing **Ctrl-]** twice sends a
Ctrl-] to the program itself.

## Run a command in the background

A `&` at the end of a line runs it in the background, as in bash. So does `sub` with a ShellCraft
command:

```
grep -r TODO . &
sub find / -name "*.log"
```

You stay at the prompt. When it finishes, you get a `[2] done` line at the next prompt, with its
output.

## The footer

Every subshell gets a numbered line in the footer, under `main`:

```
 Payment API is down · from mcp · claude-code                            crit  ✖
 ● main ▾                                                         ● 1  ● 1  ● 1 ▸
 ○ 1  bash --norc  ● Disk /home at 91%
 ○ 2  find / -name "*.log"                                                done  ✖
```

- **●** marks the shell you're in.
- A running line is plain. A finished one turns green (`done`) or red (`exit 2`, `killed`), with
  a **✖** on the right that removes it.
- **Click a line**, or press **Ctrl-]** and its number, to switch to it. Click `main`, or press
  **Ctrl-]** alone, to go back to the prompt.
- **↓** on an empty prompt moves into the footer: ↑ and ↓ walk it, Enter switches, `x` stops the
  chosen one, Esc leaves.
- The **▾** after `main`, or **Ctrl-] c**, folds the subshell lines away when you need the room.

The same footer shows inside every subshell, so you can switch from anywhere.

## Manage them with jobs

```
jobs                 # every subshell and background command
jobs 2               # switch to job 2
jobs -f 2            # follow its output as it grows, like tail -f (Ctrl-C stops following)
jobs -k 2            # stop it
jobs -r 2 api logs   # rename it: the footer shows "api logs"
jobs -c              # clear the finished ones
```

Finished jobs leave the footer on their own after their `done` line. `settings jobs_autoclear off`
keeps them until `jobs -c`. Jobs still running when you leave ShellCraft are stopped.

## How switching works

Each program runs in its own terminal emulator, so switching to it redraws its screen exactly as
it was, scrollback included. Inside a subshell the mouse wheel scrolls the program's own
scrollback, unless the program uses the mouse itself, the way `htop` does. Any key brings you back
to the live screen.

At the prompt, the wheel scrolls your terminal's history as usual. `settings scroll_view on`
opens the session's recent output in the viewer instead. `settings footer_mouse off` leaves the
mouse to your terminal at the prompt, and `settings footer off` removes the footer altogether.

## A light ShellCraft: sub shellcraft

`sub shellcraft` opens a light ShellCraft, with your commands, modules, settings, aliases and
history, but without the banner, startup files, hot reload or the MCP server. Its prompt shows
`⚡shellcraft [1]`.

There's only one level of subshells. Typing `sub htop` or `make &` inside a light ShellCraft asks
the main shell to start it, so it appears in the footer under `main` while you stay where you are.
`sc &` starts a light ShellCraft in the background.

An AI client can open one too, named `✦ AI · finding` in the footer, that shows the AI's findings
when you switch to it. See [MCP and the AI co-pilot](mcp-and-ai.md).

## Alerts

An alert is a short colored message in the footer. Anything can send one: you, a script, a
program in a subshell, or an AI client.

```
alert deploy finished                 # green, gone after 15 seconds
alert -l warn disk at 90%             # amber, 60 seconds
alert -l crit pager is down           # red, stays until you close it
alert --list                          # see them
alert -c                              # close them all (alert -c N closes one)
```

The **badge** at the right of the `main` line counts alerts by level. Click it to show one line per
alert, most severe first, with where it came from and how long it stays. Click again to fold them.
A critical alert always keeps its own line, so it can't be missed. Click the **✖** at the right end
of a line to close it.

An alert sent from a subshell also shows on that subshell's line, after its command.

### Send one from anywhere

| From | How |
| --- | --- |
| ShellCraft | `alert MESSAGE` |
| A program in a subshell (`sub bash`) | `alert MESSAGE`: the command is on its `PATH`, so `make && alert done` works |
| A script outside ShellCraft | `shellcraft-alert MESSAGE` |
| An AI client | the `send_tui_alert` MCP tool |

Each alert says where it came from: `· from sub 2 (bash)`, `· from mcp · claude-code`. Senders
drop a small file into `~/.shellcraft/alerts/`, a folder only you can read, so there's no network
port and no password.

## The clip register

An alert can carry data, such as a file's content or a command's output:

```
alert -d /var/log/app.log "errors in the log"
make 2>&1 | alert -l crit build failed -d -
```

**Right-click** an alert to copy its data, or its message if it has none, into the clip register.
The clip keeps your last ten copies: `$CLIP` is the newest, `$CLIP1` the one before, up to `$CLIP9`.

| Do this | To |
| --- | --- |
| **Alt-v** at the prompt | Paste the newest copy. Press it again to swap in the one before |
| **Ctrl-] v** in a subshell | Type the newest copy into the program |
| **Alt-c** | Copy the top alert without the mouse |
| `clip -l` | List the copies |
| `clip N` | Print copy N |
| `clip \| jq` | Pipe the newest one on |
| `clip -i N` | Start the next prompt with copy N |

If Alt doesn't reach the shell (Konsole keeps Alt-v for its menu), press Esc, then the key.

## Windows

Subshells need the `pywinpty` package on Windows, which `pip install -r requirements.txt`
installs. Without it, `sub PROGRAM` runs the program in the foreground and you're back when it
exits.

`man sub`, `man jobs`, `man alert` and `man clip` have every detail.
