# Keyboard and mouse

← [README](../README.md) · [All docs](README.md)

Typing helpers, the viewer for long output, and every keyboard shortcut and mouse action by
where it works, with what differs between Linux and Windows. In the shell, `hotkeys` prints the
shortcut tables and `man hotkeys` opens them in the viewer.

## Typing helpers

- **Syntax highlighting:** known commands are green, unknown ones red, operators in the accent color, quotes, switches and `$variables` in their own colors (`settings highlight off` turns it off).
- **The toolbar** under the prompt shows the usage of the command you are typing, or says it is not found (`settings toolbar off`).
- **Ghost text:** start typing a command you've used before, and a faint suggestion appears. Press **→** to accept it. **Ctrl-R** searches the history as you type.
- **Tab** completes command names and aliases, **switches** for every command (`grep -<Tab>` shows what each one does), **switch values** (`ip2geo -o <Tab>`, `find . -type <Tab>`), **positional values** from a module's `.skill` (`queryCensys <Tab>` → `host cert search`), arguments for `help`, `man`, `theme`, `prompt`, `settings`, `modules` and `unalias`, commands inside `for` / `if` blocks, and paths everywhere else. A name with spaces is quoted for you, and completion works inside quotes.
- **Completion follows the usage line** of each command and module (its `usage`, as `help` shows it): what may come next is offered, not file names. `cut <Tab>` offers `-f` and `-c`, which it can't run without; `mcp <Tab>` its actions, and `mcp token <Tab>` → `rotate`; `time <Tab>` a command; `grep foo <Tab>` the files. Where only a free value fits (`grep <Tab>`: a PATTERN) Tab on an empty word offers nothing, and the usage toolbar shows what to type. A module gets this from the `usage` in its `.skill`, with no code.
- **The mouse** works in the completion menu: the entry under the mouse is highlighted as you move, a click picks it, the wheel moves through the list. ShellCraft asks the terminal for the mouse only while a menu is open (and in the pager), so selecting text and wheel-scrolling the scrollback work as usual the rest of the time. `settings mouse off` turns it off; most terminals also let you select text under any program by holding **Shift**.
- **`settings mouse_always on`** keeps the mouse on at the prompt: click to place the cursor, click a prompt segment (`⚡` → `help`, the path → `ls`, `⎇ branch` → `git status`, `● mcp` → `mcp log`, `[✗ N]` → the last error again), click the toolbar to open the man page of the command you are typing, and roll the wheel over the input line to move through the history (or through the matches during Ctrl-R). The command runs above the prompt and what you typed stays. The cost: the terminal needs **Shift** for text selection and wheel scrollback, which is why it is off by default.
- **Clickable paths:** file names printed by `ls`, `tree`, `find`, `du` and `grep` can be terminal hyperlinks: `settings links on`, then Ctrl-click (Cmd-click on macOS) opens the file or folder, in Windows Terminal, GNOME Terminal, kitty, WezTerm, iTerm2 and others. Off by default, so a stray click in a listing opens nothing.
- **Ctrl-C** cancels the current line or a running command, as many times as you like: it never closes ShellCraft (a module that ignores it keeps running in the background, where Python can't stop it). Only **Ctrl-D** on an empty line, or `exit`, leaves the shell. On Windows, Ctrl-Break works like Ctrl-C. If ShellCraft ever hits an unexpected error, it says so and keeps going; the details are in `~/.shellcraft/crash.log` (and in `diag`) for a bug report.
- A line that ends inside `{ … }` or `( … )` asks for another line (`┆ …`); the whole command is saved to the history as one line.
- `banner` shows the welcome banner again (`banner -c` clears the screen first). `settings clear_on_start off` keeps the screen at startup.
- **The prompt** shows `user@host`, the directory, `⎇ branch` in a git repository, `● mcp :38765` while `mcp start` runs, `[✗ N]` after a failure and `⏱` after a slow command. `prompt name path git status` picks the segments, `prompt -r clock` adds a right-hand clock, `prompt reset` goes back (`man prompt`). `settings transient_prompt on` collapses the two-line prompt to `❯ command` after Enter, so the scrollback stays dense.

## Long output

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

## At the prompt: editing

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
| `Ctrl-C` | Drop the line (or stop the running command); it never closes ShellCraft |
| `Ctrl-D` | Leave ShellCraft, on an empty line |

## The clip register and alerts

| Keys or mouse | What they do |
| --- | --- |
| `Alt-v` | Paste the newest copy (`$CLIP`). Press it again right away for `$CLIP1`, `$CLIP2`… |
| `Esc` then `Ctrl-V` | The same paste (Ctrl+Alt+v: what AltGr+v sends) |
| `Alt-c` | Copy the top alert's content |
| Right-click an alert | Copy its content (`clip -a N` from the keyboard) |
| Click an alert's `✖` | Dismiss it (`alert -c N` from the keyboard) |
| Click the badge on `main` | Show / fold the alert rows |
| `clip -i [N]` | Start the next prompt with copy N, no key chord at all |

## The footer and subshells

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

`Ctrl-]` is the default prefix key. `settings prefix_key` lists the others (`ctrl-b`, `ctrl-a`,
`ctrl-t`, `ctrl-g`, `ctrl-s`) and what each one shadows; `settings prefix_key ctrl-b` switches
to Ctrl-B everywhere at once, and `settings reset prefix_key` goes back to Ctrl-].

## Mouse: scrolling and selecting

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

## The viewer (man, less, the scroll view)

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

## Linux and Windows

| | Linux | Windows 11 |
| --- | --- | --- |
| Alt shortcuts (`Alt-v`, `Alt-c`) | Left and right Alt; AltGr unless it types a character | Left, right Alt and AltGr |
| Alt does not arrive | Press `Esc` then the key (Konsole's menu bar takes Alt-v) | Press `Esc` then the key |
| Subshells (`sub`) | Always | Need the `pywinpty` package (the banner says when it is missing) |
| Terminal | Any modern terminal | Windows Terminal or the classic console; keys and the mouse are read the way prompt_toolkit's own Windows support does it (VT input when the console takes it) |
| What is missing | The banner (and `diag`) name a missing package (`pyte`, `mcp`) | The same, plus `pywinpty`: without it there are no subshells, no footer and no mouse there |

`diag` is hidden until you start ShellCraft once with `python main.py --diag` (or run
`settings diagnostics on`); `settings diagnostics off` hides it again. `diag keys` shows what
each key press reaches the shell as: Alt-v must show `escape v`. On
Windows it also shows the console's Alt flags, to paste into a bug report. When a key does not
show there at all, `diag keys --raw` prints what the console itself delivers, below the key
reader (`--vt` with the console's VT input on), and `diag keys --raw --mouse` the same for
the mouse wheel and clicks.

## A slow start

ShellCraft prints its banner before loading the prompt library, so something shows at once, and
the prompt follows (about 0.15 s on a typical Linux machine). If the start feels slow, run
`python main.py --timing`: just before the prompt it prints how long each phase took, starting
with the time the process existed before ShellCraft's own code ran (the Python interpreter, its
packages, and on Windows antivirus scanning the files). `diag` keeps the last start's table, to
paste into a bug report.
