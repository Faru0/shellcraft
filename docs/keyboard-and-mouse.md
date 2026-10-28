# Keyboard and mouse

← [Manual](README.md)

This page covers everything you can do with the keyboard and the mouse in ShellCraft: the help you
get while you type, how the mouse behaves, the viewer for long output, and every shortcut. Read the
first three sections once to learn what's there. The tables after them are for looking up a key
when you need it.

You don't have to come back here to remember a key. Inside the shell, `hotkeys` prints the
shortcut tables.

## While you type

ShellCraft watches the line as you type and helps in a few ways. None of them change what runs, so
you can ignore any of them, and each one can be switched off.

- **Colors.** Commands ShellCraft knows are green and unknown ones red. Switches, quotes and
  `$variables` get their own colors. `settings highlight off` turns this off.
- **The toolbar** under the prompt shows how to use the command you're typing.
  `settings toolbar off` hides it.
- **Grey suggestions.** Start typing a command you've used before and the rest appears in grey.
  Press **→** to take it. **Ctrl-R** searches everything you've typed.
- **Tab** completes command names, switches (with a description of each), switch values, file
  names, and the arguments of commands like `man`, `theme` and `settings`. A name with spaces is
  quoted for you.
- **Tab follows each command's usage line.** It offers what can come next, not only file names:
  `cut <Tab>` offers `-f` and `-c`, `mcp <Tab>` its actions. Where only a value you make up fits,
  like grep's pattern, Tab offers nothing and the toolbar shows what to type. Your own modules get
  this from the `usage` line in their `.skill` file.
- **An unfinished line** that ends inside `{ … }` or `( … )`, or with a `\`, asks for the next line.
  That's how you type a loop or a function over several lines at the prompt.
- **Ctrl-C** drops the line or stops the running command. It never closes ShellCraft. Only
  **Ctrl-D** on an empty line, or `exit`, does. So pressing Ctrl-C is always safe when you're not
  sure what's happening.

A red command name is worth a second look before you press Enter. It means ShellCraft doesn't know
that name, so you'll get `command not found`. Usually it's a typo, or a program from your system
while [OS commands](settings-and-themes.md#your-systems-programs) are off.

## The mouse

A terminal program that uses the mouse takes it away from the terminal. Then you can't select text
or scroll back the way you're used to. To avoid that, ShellCraft only takes the mouse while there's
something to click: the completion menu, the viewer, or the footer. The rest of the time your
terminal keeps it, so selecting text and scrolling work as usual.

- In the completion menu, the entry under the mouse is highlighted, a click picks it, and the
  wheel moves through the list.
- `settings mouse_always on` keeps the mouse at the prompt too. Then you can click to place the
  cursor, click parts of the prompt (the path runs `ls`, the git branch runs `git status`), and use
  the wheel to move through your history. The cost: selecting text then needs **Shift**.
- `settings links on` makes file names from `ls`, `tree`, `find`, `du` and `grep` into links.
  Ctrl-click (Cmd-click on macOS) opens them.
- `settings mouse off` turns all of this off.

## Long output

Some output is too long for one screen. You have two ways to read it.

`man` opens a scrollable viewer, and so does anything you pipe into `less` (`cat big.log | less`).
Other output prints straight to the screen, so you can scroll back through it in your terminal.

In the viewer, the arrow keys and the wheel scroll, `/` searches, `n` jumps to the next match, `w`
wraps long lines, `?` lists the keys and `q` closes it. The full list is
[below](#the-viewer-man-less-the-scroll-view). `settings pager off` prints `man` pages instead.

## At the prompt: editing

These are the same keys as in bash and most other shells, so your fingers may know them already.
Ctrl-A and Ctrl-E are worth learning first: jumping to the start of a long line to fix a typo
beats holding the left arrow.

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
| `Ctrl-Z` | Nothing. ShellCraft doesn't suspend itself (there may be no shell behind it to bring it back) |

## The clip register and alerts

The clip register holds text you copied from output or alerts, so you can paste it at the prompt.
[Subshells, alerts and the clip](subshells.md) explains where the copies come from.

| Keys or mouse | What they do |
| --- | --- |
| `Alt-v` | Paste the newest copy (`$CLIP`). Press it again right away for `$CLIP1`, `$CLIP2`… |
| `Esc` then `Ctrl-V` | The same paste (Ctrl+Alt+v: what AltGr+v sends) |
| `Alt-c` | Copy the top alert's content |
| Right-click an alert | Copy its content (`clip -a N` from the keyboard) |
| Click an alert's `×` | Dismiss it (`alert -c N` from the keyboard) |
| Click the badge on `main` | Show / fold the alert rows |
| `clip -i [N]` | Start the next prompt with copy N, no key chord at all |

## The footer and subshells

The footer is the list of running programs at the bottom of the screen. Most of these keys start
with the prefix key, Ctrl-] by default: press it, let go, then press the second key.

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
| Click the `×` of a finished line or an alert | anywhere | Remove it from the footer |

`Ctrl-]` is the default prefix key. `settings prefix_key` lists the others (`ctrl-b`, `ctrl-a`,
`ctrl-t`, `ctrl-g`, `ctrl-s`) and what each one shadows; `settings prefix_key ctrl-b` switches
to Ctrl-B everywhere at once, and `settings reset prefix_key` goes back to Ctrl-].

## Mouse: scrolling and selecting

What the wheel and a drag do depends on whether ShellCraft has the mouse at that moment. This table
sorts it out.

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

The viewer uses the same keys as `less`, plus a few arrows and the mouse.

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

Most keys work the same everywhere. These are the differences, and what to do when a key doesn't
arrive.

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

See [Troubleshooting](troubleshooting.md#the-start-is-slow).
