# Stress test notes

What happened when ShellCraft was driven hard through a pseudo-terminal, what turned out to
be broken, what turned out to be the test's fault, and what was done. The harness lives in
`tools/stress/` and its README says how to run it. Dates: October 2026, Linux (Fedora, Python
3.14). Windows was not run; its console paths were reviewed by reading only.

## How it was tested

A Python driver forks ShellCraft on a pty, renders its output with `pyte`, and acts like a
real terminal: it answers cursor-position queries with a random delay of up to 0.4 s and
tracks the mouse and paste modes the shell switches on and off. It then fires commands and
interrupts them with Ctrl-C at random moments, sends mouse reports, bad bytes and resizes, and
after every step checks for a traceback, a dead or hung shell, escape fragments on the prompt
line, leftover input, and whether a command typed afterwards still shows its output.

Around 600 random steps across these groups:

- Ctrl-C at random moments in: long pipelines (`seq 1 400000 | grep 1`), big styled and plain
  output (`cat` and `grep` on a 300,000-line file), `sleep`, loops, `read`, `man` and
  `less`, `sub bash` with a chatty program, `sub sc`, background jobs, `jobs -f`, a `curl`
  that cannot connect, `find /`, `tree`, `theme`, and Ctrl-C at an idle prompt.
- Quick commands with one to six Ctrl-Cs as the prompt redraws.
- Clicks and wheel reports sent while a command runs, then Ctrl-C.
- Type-ahead: the next command typed before the prompt is back, with slow cursor replies.
- Binary output, invalid UTF-8, 20 KB lines, pasted blocks, bracketed paste, Ctrl-D, Ctrl-Z,
  Ctrl-\, raw escape junk typed at the prompt, typing while output streams, unfinished quotes
  and blocks, resizes mid-command, alias and module churn, `!!`, and a subshell that turns
  mouse motion reporting on.
- Ctrl-C during the first 30 to 600 ms of a start.

## What was broken

1. **Ctrl-C during startup** (up to about 150 ms in) printed a raw Python traceback from inside
   the imports and exited with status 2. Fixed: `main.py` installs a quiet handler before the
   imports (status 130, one line on stderr); the shell takes over once it runs.
2. **Terminal reports arriving while a command runs showed up as text at the next prompt**, with
   the escape prefix eaten: a click became `<0;10;5M`, a wheel notch `<64;10;5M`. A real
   terminal only sends these when mouse reporting is on, and the main shell turns it off while
   commands run, so in practice this needs a report in flight at the moment reporting is
   switched off (the wheel exactly as a subshell exits, say). Fixed: before each prompt,
   `shellcraft/typeahead.py` reads what is pending, parses it the way the prompt would, drops
   cursor replies and mouse reports, and keeps text, Enter and pastes as typeahead.
3. **`^C` glued to the next prompt header** when the Ctrl-C landed in the gap right as a command
   finished (where it is deliberately dropped). Fixed: the prompt starts on the next line.
4. **Ctrl-Z typed a `^Z` character** into the line. Fixed: bound to nothing. Suspending was
   considered and rejected: when ShellCraft is the terminal's own shell nothing can resume it.

## Seen, not reproduced on demand

In three early sessions the harness's terminal emulator choked on sequences like
`...;247;6;207B` where `...;207m` was expected: a color code cut off with the next text glued
on. On screen that is junk like `;207m` and wrong colors, which matches the reports from users.
The only mechanism consistent with it is the tty driver's flush on Ctrl-C, which discards
queued output and can cut a sequence in half when the terminal is behind (a real terminal
rendering 300,000 colored lines is behind). Deliberate attempts to force it, with a slow
reader and with and without the `NOFLSH` flag, gave 0 in 64 interrupts, so it is not proven.
Two things were done anyway, both cheap and safe: `NOFLSH` is set for the session on POSIX,
and an interrupted print now resets colors and ends the line before anything else is written.
Windows has no such flush, so if the junk appears there the cause is something else.

## What was not a bug

- "Typed input lost after Ctrl-C", chased for a while, was the harness checking the screen
  before the echo had arrived. A syscall trace showed the shell read the line and printed its
  output 20 ms later. With the check fixed: 0 lost lines in 60.
- Two "shell died" cases were the harness itself typing `exit` at the main prompt after a
  chained leftover keystroke.
- Type-ahead with slow cursor replies: 0 problems in 80 rounds.
- The viewer leaves its page on screen after both `q` and Ctrl-C; consistent, and by design.
- Ctrl-D at an empty prompt exits, as in bash.
- Ctrl-C spam, resizes mid-command, bad UTF-8, binary output, huge lines, pasted blocks, raw
  escape junk and Ctrl-\ were all clean.

## Verification after the fixes

`tools/stress/probes.py`: Ctrl-C during startup 0/8 tracebacks (exit 130); a click or wheel at
an idle prompt, during `sleep`, and during `seq | grep` followed by Ctrl-C leaves the prompt
clean; Ctrl-Z leaves the line empty and the shell working. `tests/test_interrupts.py` covers
the typeahead filter, the `NOFLSH` flag and its restore, the Ctrl-Z binding, the newline after
a dropped interrupt, and a Ctrl-C during startup in a real pty.

## Open

- A reproduction on a real terminal of the cut color code, with what was running and whether
  the mouse was used.
- The same runs on Windows 11 (Windows Terminal and conhost), which the harness cannot do.
