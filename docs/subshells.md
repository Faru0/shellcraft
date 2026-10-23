# Subshells, alerts and the clip

← [README](../README.md) · [All docs](README.md)

Programs and background commands as numbered subshells, the footer that lists them, alerts from
anywhere, and the clip register that Alt-v pastes.

## Subshells

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
 Payment API is down · from mcp · claude-code                            crit  ✖
 ● main ▾                                                         ● 1  ● 1  ● 1 ▸
 ○ 1  bash --norc  ● Disk /home at 91%
 ○ 2  find / -name "*.log"                                                done  ✖
```

The ● is the shell you are in. A running line is plain; a finished one is green (`done`) or
red (`exit 2`, `killed`), with a **✖** on the right that removes it when you are done with it. The `▾` after `main`
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

An AI client can open one too (its `open_subshell` tool): a lite ShellCraft named `✦ AI · finding`
in the footer, started in the background, that prints the AI's brief first (its findings, the
evidence, what to do next), with an alert pointing to it; Ctrl-] N or a click shows it, `exit`
returns. See [MCP server and the AI co-pilot](mcp-and-ai.md) and [ai/README.md](../ai/README.md).

Programs run on their own pseudo-terminal: `pty` on Linux and macOS, ConPTY through the
`pywinpty` package on Windows (installed with the requirements there). Without `pywinpty`,
`sub PROGRAM` on Windows runs the program in the foreground instead (you are back when it
exits) and it is not listed as a job. If a footer line does not change color as its job runs and
ends, `diag` prints what the footer writes for each state and the live state of every job, to
paste into a bug report (start once with `--diag` to get the `diag` command).

## Alerts in the status panel

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
To dismiss an alert, click the **✖** at the right end of its row (`alert -c` from the keyboard). With no subshell to fold into, every alert shows as a row.

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
