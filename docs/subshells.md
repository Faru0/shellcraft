# Subshells, alerts and the clip

← [Manual](README.md)

This page is for you if you keep more than one thing going at once: an editor, a log you're
watching, a long build, an SSH session. After reading it you'll be able to run programs side by
side inside ShellCraft and hop between them, send yourself alerts when something finishes, and
keep the last few things you copied within reach.

There are three ideas here. A **subshell** is a program running next to your prompt. The
**footer** is the strip at the bottom of the screen that lists them. An **alert** is a short
message in that footer, and the **clip** is where ShellCraft keeps what you copy from alerts.

## Open a subshell

Reach for a subshell when you want to start something and still have your prompt. Say you want
`htop` open while you clean up a folder, or you're editing notes in `vim` and want to check a
command without quitting the editor.

```
sub                          # your own shell: bash, zsh, pwsh…
sub htop                     # any program, with the whole screen
sub vim notes.md
sub ssh myserver
sub shellcraft               # a light ShellCraft inside ShellCraft (sub sc for short)
```

`sub` on its own opens your login shell. With a program name, it runs that program instead. The
program takes over the whole screen, as it would in a plain terminal. When it exits, you're back
at the prompt.

The difference from typing `htop` directly is what happens when you want to leave without
quitting. Press **Ctrl-]** and you're back at your prompt while the program keeps running.
To go back to it, press **Ctrl-]** and its number: **Ctrl-] 1** for the first one.

Ctrl-] is ShellCraft's key, so the program never sees it. If the program itself needs a Ctrl-]
(telnet uses it, for example), press **Ctrl-]** twice and one is passed through.

If you type a name ShellCraft can't find, you get
`sub: NAME: not a command, a module or a program on PATH`. Check the spelling, or give the full
path to the program.

## Run a command in the background

Some commands take a while and don't need the screen: a search across the whole disk, a
`make`, a big `grep`. Put `&` at the end of the line and it runs in the background, as in bash.
`sub` followed by a ShellCraft command does the same thing:

```
grep -r TODO . &
sub find / -name "*.log"
```

You get your prompt back at once. When the command finishes, a `[2] done` line shows up above your
next prompt, followed by its output. The number is the job number, which you'll use with `jobs`
below.

The difference between `sub htop` and `sub find …` is that `htop` is a full-screen program, so
it gets its own screen you can switch to. `find` only prints lines, so it runs as a background
command and you read its output when it's done, or follow it as it grows with `jobs -f`.

## The footer

Once something runs next to your prompt, the footer appears at the bottom of the screen. Each
subshell and background command gets a numbered line under `main`, which is your own prompt:

```
 Payment API is down · from mcp · claude-code                            crit  ×
 ● main ▾                                                         ● 1  ● 1  ● 1 ▸
 ○ 1  bash --norc  ● Disk /home at 91%
 ○ 2  find / -name "*.log"                                                done  ×
```

Here's how to read it:

- **●** marks the shell you're in right now. The others have **○**.
- A line that's still running is plain. A finished one turns green with `done`, or red with
  `exit 2` or `killed`. Exit status 0 means success, anything else means the command reported a
  problem, so red is your cue to look.
- A finished line gets a **×** on the right. Click it to remove the line.
- The top line in this example is an alert, and the colored dots at the right of `main` count
  alerts by level. [Alerts](#alerts) explains both.

You can move around with the mouse or the keyboard:

- **Click a line**, or press **Ctrl-]** and its number, to switch to it. Click `main`, or press
  **Ctrl-]** alone, to go back to the prompt.
- **↓** on an empty prompt moves into the footer. Then ↑ and ↓ walk it, Enter switches to the
  chosen line, `x` stops it, and Esc leaves the footer.
- The **▾** after `main`, or **Ctrl-] c**, folds the subshell lines away when you need the room.
  Do it again to unfold them.

The same footer shows inside every subshell, so you can switch from anywhere, not only from the
prompt.

## Manage them with jobs

`jobs` is the typed version of the footer. It's handy when your hands are on the keyboard, or
when you want to do something the footer can't, like rename a job or follow its output.

```
jobs                 # every subshell and background command
jobs 2               # switch to job 2
jobs -f 2            # follow its output as it grows, like tail -f (Ctrl-C stops following)
jobs -k 2            # stop it
jobs -r 2 api logs   # rename it: the footer shows "api logs"
jobs -c              # clear the finished ones
```

Renaming is worth it when you have three `ssh` sessions open and can't tell them apart. `jobs -f`
is what you want for a long build: you see each line as it's printed, and Ctrl-C stops the
following, not the build.

Finished jobs leave the footer on their own after their `done` line has been shown. If you'd
rather keep them until you clear them yourself, turn that off with
`settings jobs_autoclear off`, and use `jobs -c` when you're done looking.

Jobs still running when you leave ShellCraft are stopped. If something has to keep running after
you log out, start it with a tool made for that, such as `tmux` or `nohup`, in a real terminal.

## How switching works

Each program runs in its own small terminal emulator inside ShellCraft. That's why switching back
to `vim` or `htop` shows its screen exactly as you left it, scrollback included, instead of a
blank screen.

Inside a subshell, the mouse wheel scrolls the program's own scrollback. The exception is a
program that uses the mouse itself, the way `htop` does: then the wheel goes to the program. When
you've scrolled back, any key brings you to the live screen again.

At the prompt, the wheel scrolls your terminal's history as usual. Three settings change how the
mouse and the footer behave there:

- `settings scroll_view on` makes the wheel open the session's recent output in a viewer instead.
- `settings footer_mouse off` leaves the mouse to your terminal at the prompt, so you can select
  text the way you're used to. The footer still works from the keyboard.
- `settings footer off` removes the footer altogether.

## A light ShellCraft: sub shellcraft

Sometimes you want a second ShellCraft prompt: one to watch a log in, one to work in. `sub
shellcraft` (or `sub sc`) opens a light ShellCraft. It has your commands, modules, settings,
aliases and history, but it skips the banner, the startup files, hot reload and the MCP server,
so it starts fast and doesn't fight the main shell over any of those. Its prompt shows
`⚡shellcraft [1]` so you can tell which one you're in.

There's only one level of subshells. If you type `sub htop` or `make &` inside a light ShellCraft,
it asks the main shell to start it instead. The new job appears in the footer under `main`, and you
stay where you are. `sc &` starts a light ShellCraft in the background without switching to it.

An AI client can open one too. It's named `✦ AI · finding` in the footer and shows the AI's
findings when you switch to it. See [MCP and the AI co-pilot](mcp-and-ai.md).

## Alerts

An alert is a short colored message in the footer. Use one when you want to know that something
happened without staring at it: a deploy finished, a disk is filling up, a long test run failed.
Anything can send one: you, a script, a program in a subshell, or an AI client.

```
alert deploy finished                 # green, gone after 15 seconds
alert -l warn disk at 90%             # amber, 60 seconds
alert -l crit pager is down           # red, stays until you close it
alert --list                          # see them
alert -c                              # close them all (alert -c N closes one)
```

The level decides the color and how long the alert stays. With no `-l`, it's a short green note.
`warn` stays a minute. `crit` stays until you close it, because a critical message you missed
while getting coffee is worse than one you have to dismiss.

The **badge** at the right of the `main` line counts alerts by level. Click it to show one line per
alert, most severe first, with where it came from and how long it stays. Click again to fold them.
A critical alert always keeps its own line, so it can't be missed. Click the **×** at the right end
of a line to close it.

An alert sent from a subshell also shows on that subshell's own line, after its command. That's
the `● Disk /home at 91%` you see on line 1 of the footer above.

### Send one from anywhere

The useful part is that alerts don't have to come from the prompt. A typical case: you start a
build in `sub bash` and want to hear when it's done. Inside that bash, `alert` is on the `PATH`,
so this works:

```
make && alert build done
```

`&&` means "only if the previous command succeeded", so you only get the alert when `make` exits
with status 0.

| From | How |
| --- | --- |
| ShellCraft | `alert MESSAGE` |
| A program in a subshell (`sub bash`) | `alert MESSAGE`: the command is on its `PATH`, so `make && alert done` works |
| A script outside ShellCraft | `shellcraft-alert MESSAGE` |
| An AI client | the `send_tui_alert` MCP tool |

Each alert says where it came from, such as `· from sub 2 (bash)` or `· from mcp · claude-code`.

You might wonder how a script outside ShellCraft reaches it. Senders drop a small file into
`~/.shellcraft/alerts/`, a folder only your user can read, and the shell picks it up. There's no
network port and no password, so nothing else on the network can send you alerts.

## The clip register

An alert can carry data along with its message, such as a file's content or a command's output.
That's useful when the message says "the build failed" and you want the actual error at hand:

```
alert -d /var/log/app.log "errors in the log"
make 2>&1 | alert -l crit build failed -d -
```

`-d FILE` attaches a file. `-d -` attaches whatever is piped in, which is why the second line
sends `make`'s output into `alert`. `2>&1` folds the error stream into the normal output first, so
the compiler errors come along too.

**Right-click** an alert to copy its data into the clip register. An alert with no data copies its
message. The clip keeps your last ten copies: `$CLIP` is the newest, `$CLIP1` the one before, up
to `$CLIP9`. You can use them like any variable, for example `echo "$CLIP" | grep error`.

Here's what you can do with the clip, and from where:

| Do this | To |
| --- | --- |
| **Alt-v** at the prompt | Paste the newest copy. Press it again to swap in the one before |
| **Ctrl-] v** in a subshell | Type the newest copy into the program |
| **Alt-c** | Copy the top alert without the mouse |
| `clip -l` | List the copies |
| `clip N` | Print copy N |
| `clip \| jq` | Pipe the newest one on |
| `clip -i N` | Start the next prompt with copy N |

If Alt-v or Alt-c does nothing, your terminal is probably keeping Alt for itself (Konsole uses
Alt-v for its menu). Press Esc, let go, then press the letter. Terminals send Alt-v as Esc
followed by v, so this does the same thing.

## Windows

Subshells need the `pywinpty` package on Windows, which `pip install -r requirements.txt`
installs. Without it, `sub PROGRAM` still runs the program, but in the foreground: it has the
screen until it exits, and you can't switch away from it with Ctrl-].

## More detail

`man sub`, `man jobs`, `man alert` and `man clip` have every option.
