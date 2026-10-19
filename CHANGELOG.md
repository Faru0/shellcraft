# Changelog

All notable changes to ShellCraft. The format follows [Keep a Changelog](https://keepachangelog.com/),
and the project uses [semantic versioning](https://semver.org/).

## [Unreleased]

### Added

- **`diag keys --raw`:** for a key that `diag keys` does not show at all (Alt-v on some Windows
  setups). On Windows it prints every key record the console delivers, key down and key up, with
  the virtual key, scan code, character and modifier flags, before prompt_toolkit reads them;
  `--vt` turns the console's VT input on first. On Linux it prints the raw bytes of each key.

- **A saved MCP token:** `mcp start` used to make a new bearer token every time, so clients had to
  be added again. The token is now made once and saved (in the OS keyring when there is one,
  else in `config.json`, like an API key), and so is the address of the last `mcp start`.
  `mcp show` prints the token and the `claude mcp add` line; `mcp token rotate` makes a new token,
  restarts a running server with it, and prints what to run in the client:
  `claude mcp remove shellcraft`, then the new `claude mcp add` line. The `claude mcp add` line is
  no longer broken in two by the shell's line wrapping, so it copies as one line.
- **A review loop with an AI** (`mark`, `review`, and four MCP tools). You flag output for the AI
  with `mark` (`pytest | mark --type test-failure`, `mark --file F`, `mark --diff A B`); it reads
  them with `get_flagged_items` or the `shellcraft://review/queue` resource and closes them with
  `resolve_review_item`. Before a destructive action the AI calls `request_human_review`: the
  request shows in the footer as an alert (info / warn / crit for a low / medium / high
  priority) until you answer with `review approve ID [NOTES]` or `review reject ID [NOTES]`; the
  AI waits for the answer (`wait_seconds`) or asks again with `get_review_status`. The items are
  JSON files in `~/.shellcraft/review/` (owner-only), written atomically and changed under a
  per-item lock, so the shell, `mcp start`'s server and every client's own `--mcp` server can
  share them; connected clients are notified when the queue changes. Each piece keeps its last
  256 KB; done items are deleted after 7 days. Advisory: the server instructions tell the AI to
  ask, nothing stops its own tools.
- **`hotkeys`:** every keyboard shortcut and mouse action, by where it works (the prompt, the
  clip and alerts, the footer and subshells, scrolling and selecting, the viewer), with what
  differs between Linux and Windows. `man hotkeys` opens it in the viewer, `help` lists it among
  ShellCraft's own commands and its footer points to it, and the README has the same tables
  under "Keyboard and mouse".
- **Clip history:** the clip register keeps the last ten copies, newest first: a right-click on
  a second alert no longer loses the first. `$CLIP` is the newest, `$CLIP1` the one before, up to
  `$CLIP9`; **Alt-v** pastes the newest and, pressed again right away, swaps in the next older one
  (like Emacs's yank-pop). `clip -l` lists them, `clip N` prints one, `clip -i N` starts the next
  prompt with it, `clip -c` clears them all. Shared with `sub shellcraft` over the main link, in
  memory only.
- **The scroll view:** while the shell has the mouse at the prompt (the footer lists a subshell or
  an alert, so its lines, an alert's ✖ and the badge can be clicked), the terminal cannot scroll
  its own history with the wheel. The wheel now opens the scroll view instead: the session's
  recent output (the last 5,000 lines, colors kept, with the commands that produced them) in the
  viewer, at its end. The wheel scrolls it; wheel down past the end, or q, closes it. It lives in
  memory only, and `clear` empties it. With nothing to click, the terminal keeps the mouse and its
  own wheel.
- **`diag`** also shows each job's recording flag and the `record` signals received from
  subshells, for a recording bubble that does not turn red.

- **`diag keys`:** a key test that shows what each key press reaches the shell as (`escape v`
  is Alt-v), and on Windows the console's raw flags (left-alt, right-alt, left-ctrl, shift).

### Changed

- **`alert` is quicker to type:** the levels are `info`, `warn` and `crit` (the long `warning`
  and `critical` still work, from scripts, older spool files and AI clients), the data switch is
  `-d DATA` / `--data DATA` instead of `-C`, which was easy to mix up with `-c` (clear), and the
  usage line the toolbar shows while you type names the levels:
  `alert [-l LEVEL (info,warn,crit)] [-t SECONDS | -s] [-d DATA] MESSAGE... | --list | -c [N]`.
  Alert rows end with `crit` and `warn · 57s`; `alert --list` shows `[warn]` and `[crit]`. The
  `send_tui_alert` MCP tool takes `warn` and `crit` too.
- **The footer is clickable at the prompt again by default** (`footer_mouse` now on): while it
  lists a subshell or an alert, its lines, an alert's ✖ and the badge answer the mouse, and text
  is selected with Shift and a drag. `settings footer_mouse off` leaves the mouse to the terminal.

### Fixed

- **The wheel did nothing in the viewer on Windows 11** (the scroll view, `man`, `less`), with the
  legacy and the direct key reader alike: the Windows console reports wheel events at screen
  coordinates, which the viewer's layout did not match. The viewer now takes every wheel event
  itself, wherever the mouse is. The scroll view is also bottom-aligned, like the terminal: output
  shorter than the screen sits at its bottom, so opening it looks like scrolling up from the
  prompt.
- **A footer bubble stayed highlighted:** the line under the mouse lights up (its bubble filled,
  in the accent color) to show what a click picks, but it stayed lit after the click and after
  the mouse left the footer. It now goes back to its normal color when the mouse moves off the
  footer, after a click that acts, and after one second without the mouse moving over it.
- **Alt-v with the right Alt or AltGr on Windows:** prompt_toolkit's classic Windows key reader
  (the one ShellCraft uses in Windows Terminal) turns only the left Alt into the Escape prefix
  that means Alt, so right-Alt+v typed a plain `v`, and AltGr+v (right Alt + Ctrl to Windows)
  became Ctrl-V. ShellCraft's reader now counts the right Alt and AltGr with a letter or a digit
  as Alt, unless AltGr types a real character of the keyboard layout (`@`, `€`), which still
  types. Esc then Ctrl-V (Ctrl+Alt+v, how AltGr+v arrives through the VT key reader and Linux
  terminals) pastes too.

### Removed

- The copy additions of 2.0.0: the drag highlight and right-click copy of text inside a subshell,
  copying to the host's clipboard (OSC 52 and the Win32 clipboard, `clip -H`), and Ctrl-] v at the
  prompt. Select text with Shift and use the terminal's copy; Alt-v pastes the clip (Ctrl-] v
  still types it into another program inside a subshell), and `clip -i` stays.

## [2.0.0] — 2026-10-16

### Added

- **`diag`** prints the console diagnostics (`--diag`'s report) at any time, with a new **footer
  colors** section: the escape sequence the footer writes for each state (running, done, failed,
  the recording bubble, the alert marks), whether running / done / failed look different in the
  theme, the footer's rows as last drawn, and the live state of every job (running, status, and
  for a Windows pseudo-terminal whether pywinpty says the program is alive). For a footer whose
  colors do not change: run it with a subshell running and again after it ended, and paste it.
- **Copying to the host's clipboard:** a right-click on an alert, `clip -a N`, Alt-c and a text
  selection in a subshell now also put the text on the host's clipboard, with the terminal's
  OSC 52 sequence (Windows Terminal, kitty, WezTerm, foot, iTerm2, Alacritty and tmux honor it;
  it works over ssh) and, on Windows, the Win32 clipboard; no external program. `clip -H [TEXT]`
  copies text, piped input or the register there on demand.
- **More ways to paste the clip:** **Ctrl-] v** at the prompt (it does not depend on how a
  terminal sends Alt; Alt-v on a Windows console needs the left Alt key) and `clip -i`, which
  starts the next prompt with the text on the line, no key chord at all.
- **`jobs -r N NAME`** renames a subshell: the footer, the `jobs` table and the `[N] done` note
  show the name instead of the command (`jobs -r N` alone puts the command back).
- **`record` / `script`:** a transcript of the session, like Linux `script` without the escape
  codes: every command with its time and what the shell printed, into
  `~/.shellcraft/records/session-DATE.txt` or a file you name (`record notes.txt`); `record
  status`, `record stop` (leaving the shell stops it too). The footer shows which shell records:
  its bubble (● or ○) turns red, on main's line or on the line of a `sub shellcraft` that runs
  `record`. The footer and what happens inside an attached subshell are not recorded.
- **Core warnings in the banner:** like a module's missing import, the banner (and the start
  without one) now says what the shell itself is missing: pywinpty on Windows (no subshells on
  a pseudo-terminal, so no footer), pyte, the `mcp` package, keyring, each with the pip command.
- **`sub` inside a subshell:** instead of being refused, it hands the request to the main shell
  (through the alert spool), which opens the subshell in the background under `main`: it
  appears in the footer, you stay where you are, Ctrl-] N switches to it. `cmd &` inside a
  subshell goes the same way. Every subshell belongs to main; there is no subshell inside a
  subshell. `sub sc` is short for `sub shellcraft`, and `sc &` starts one in the background.
- **The footer at the prompt too:** the shell stack (`● main`, then one line per subshell,
  newest at the bottom) now sits on the last rows of the terminal at the main prompt as well as
  inside every subshell, growing as subshells open (up to half the screen, then "… and N
  more"), with `main` there even when nothing runs. The prompt and its menus stay above it.
  **Ctrl-] c**, or a click on the `▾` after `main`, folds the subshell lines into
  `main  3 subshells ▸` and unfolds them. `settings footer off` goes back to a panel under
  the prompt only while something runs.
- **`shellcraft --lite`**, what `sub shellcraft` now runs: the builtins, modules, settings,
  aliases and history, without the banner, the clear on start, the startup scripts and rc, hot
  reload, the alert spool watcher (a nested shell used to take alerts meant for the main one)
  or the MCP server.
- **Alerts with a payload, and the clip register:** `alert -C CONTENT MESSAGE` (or `-C -` for
  what was piped in; `content` in the `send_tui_alert` MCP tool, `shellcraft-alert -C`) keeps up
  to 64 KB with the alert: a path, a command, a URL, JSON. A **right-click** on the alert's row
  copies it (or the message) into the shell's own clip register, and the row says `copied`.
  **Alt-v** (or Esc then v) pastes it at the prompt, **Ctrl-] v** into an attached subshell, the
  new `clip` builtin prints or sets it (`clip | jq`, `echo x | clip`, `clip -c`) and `$CLIP`
  holds it. Nothing touches the system clipboard, so it works in every terminal and over ssh.
- **`jq`:** a builtin that looks inside JSON with Python's `json.loads`: `cat f.json | jq`
  pretty-prints, `jq f.json -k` lists the keys, `-f ['name']` / `-f [5]['name']` / `-f .items[0].id`
  picks a value, `-i N` starts inside element N of a list and `-i *` goes through every element
  (`jq -i * -f ['name']`, `jq -i * -k`). `-r FILE` reads a file, `-c` prints compact JSON, `-n`
  counts. `jq` looks at what it got first: on screen a bare `jq` starts with a line saying the
  shape and what fits it (`# list of 3 objects (keys: name, age) · jq [0] · jq -t · jq -i '*'
  -f name`), `-s` prints just that line, and a path on its own (`jq [3]`, `jq .items[0]`) works
  like `-f`, so a list is indexed the way Python does it. JSON Lines, Python-style
  dictionaries and XML (`-x`, or by itself when the input starts with a tag: converted to
  JSON-style data first, attributes as `@name`, repeated tags as lists) are accepted. Words on a `jq` line are not
  globbed, so `*` and `[name]` arrive as typed. It is not the jq language, and it shadows a jq
  program on the PATH (run that one by its full path, such as `/usr/bin/jq`).
- **`jq -w`, `-t`, `-p`:** `-w COND` keeps the list elements that match (`-w 'age>30'`,
  `-w name~ad` for contains, `-w active=true`, `-w email` for "has the key"; more than one must all
  match; status 1 when none do). `-t` shows a list of objects as a table, tab-separated in a pipe.
  `-p` prints every value with its path (`.items[0].id = 7`), and each path works as a `-f` filter.
- **`jq` for deeply nested data** (dict -> list -> dict -> list, any number of levels):
  `-s` (and the header on screen) describes data nested four levels or more as an outline of its
  deepest branch (`object{company} -> company: object{name,departments} -> departments: list of
  2 objects{name,teams} -> …`) and a chain (`dict -> dict -> list[2] -> dict …   (11 levels)`);
  the header then suggests the switches that fit. `-R KEY` / `-f ..KEY` finds a key at any depth,
  `..` is every value, `[*].items[*].tags[*]` goes through many levels in one filter, `-W COND`
  finds the objects (or, with no path, the scalar values) that match at any depth (`-W
  'status=error'`, `-W '~timeout'`), `-F` flattens to `a.b[0].c = value` lines (with `-t`, one flat
  row per record, a column per nested key), `-d N` stops at N levels (`{…3 keys}`, and `-k -d N`
  is the key outline `items[*].tags[*].label`), `-P` prints the path of each result. A filter
  that does not fit the shape says what is there and what to type (`'name' is inside each
  element; try .company.departments[*].name`, `'level' is deeper: try -R level`, did you mean).
  Everything walks the data without recursion, so thousands of levels and hundreds of thousands
  of values are fine. A number in a condition (`-w 'age>30'`, `-W '>3'`) now compares with
  numbers only (text that reads as a number counts), instead of by text order.
- **Background startup scripts:** `# shellcraft: background` at the top of a script in
  `~/.shellcraft/startup/` starts it as a subshell when the prompt appears, so it never slows the
  start; `# shellcraft: restart` restarts it after a crash (2 s, 4 s … 60 s; gives up with a red
  alert after 5 crashes in 10 minutes); `# shellcraft: timeout=N` gives a normal script more time.
  `startup stop [NAME]`, `startup start NAME`, `startup restart NAME`. Example in
  `templates/startup/30-watcher.example.py`.
- **Ending a subshell:** Ctrl-] x inside an attached subshell ends it and returns to the prompt;
  `x` or Delete on the chosen line of the subshell panel stops that one.

- **Following a background command:** `jobs -f N` prints what a command job has written so far and
  then each new line as it comes, like `tail -f`, until the job ends (its status line) or Ctrl-C
  (the job keeps running). Selecting a still-running command job (`jobs N`, Ctrl-] N, a click)
  follows it the same way; a finished one shows its whole output. Background commands record
  their output as it streams, so `tail -f app.log | grep ERROR &` can be followed.
- **`cmd &`:** a trailing `&` runs the line in the background, like `sub cmd`: a builtin, module,
  pipeline or loop as a command job (the line as typed, quotes kept), a single OS program as a
  program job that is not attached (Ctrl-] N switches to it). Interactive shell only; an `&`
  anywhere else is still a parse error, which now says only a trailing one is supported.
- **Streaming pipelines:** every command of a pipeline runs at the same time, and lines flow to
  the next one as they are made. `yes | head -3` returns at once (the producer is stopped when the
  reader has enough), `tail -f app.log | grep ERROR` prints each match the moment it is written,
  and output that keeps coming is printed live (Ctrl-C stops it, status 130). A pipeline that
  finishes quickly looks exactly as before (colors, the pager). `cat`, `grep`, `head`, `tail -f`,
  `wc`, `tee` and the new `yes` read their input as it arrives; OS programs in a pipeline stream
  through their pipes; a module whose `run()` yields lines streams too. Builtins opt in with
  `@builtin(..., streams=True)` and get `core.streams.Lines` as stdin.

- **Shell functions:** `name() { …; }` and `function name { …; }`, on one line or several (at the
  prompt and in rc files), with `$1`…`$9`, `$#`, `$@`, `$*`, `local NAME[=VALUE]` (put back when the
  call returns), `return [N]`, recursion (100 calls deep at most), and use as a command in pipes,
  redirects and `&&` / `||` (a function's commands read its input). A function takes precedence
  over a builtin or module of the same name, as in bash, except `exit`, `export`, `unset`, `source`,
  `.`, `local`, `declare`, `alias` and `unalias`; `\name` skips it. `declare -f` / `declare -F` list
  them, `unset -f` removes one, `type` and `which` report them; never available over MCP. `$1` and
  `$#` also work inside `$(( ))`, as do `$x` and `${x}`.
- **`select` menus:** `select NAME in WORD...; do …; done` shows a numbered menu, asks with `$PS3`
  (default `#? `), sets NAME to the chosen word (empty for an invalid answer) and `$REPLY` to what
  was typed, and repeats until `break`, Ctrl-D or Ctrl-C (status 130). An empty answer shows the
  menu again. Interactive shell only (`man select`).
- **`&&`, `||`, `!` and `( … )` inside `[[ ]]`**, with bash precedence (`&&` binds tighter):
  `[[ -f $f && ( $env == prod || $env == staging ) ]]`. The script splitter no longer cuts a
  statement at an `&&` / `||` that is inside the brackets.
- **`>&2` and `1>&2`:** a command's output goes to the error stream: `echo "error: bad input" >&2`
  shows in the error style on screen and leaves `$?` alone; with `2> file` it goes to the file,
  with `2>&1` it stays output. Inside a function or a loop it follows the call's or the block's
  own redirection (`myfn 2> err.txt`, `done 2>&1 | grep …`), as do the body's failing commands. A
  background `sub` job keeps it in the job's output. Without a screen (MCP) it goes to the
  process's stderr. Other `>&` forms are still rejected, with a message saying which work.

- **`"$@"`, `shift` and default values in functions:** a quoted `"$@"` gives one word per argument
  (prefix and suffix join the first and last; no arguments, no word), in commands and `for` lists;
  `shift [N]` drops arguments (status 1 past the end); `${NAME:-word}` and `${NAME-word}` give a
  default, everywhere variables expand.

- **Comparisons inside `[[ ]]`:** `<`, `>`, `<=` and `>=` compare text order (bash's two, plus
  Python's `<=` / `>=`), and Python's `in` / `not in` (contains) and `is` / `is not` (plain text
  equality) work there too: `[[ err in $line && $a < $b ]]`. Outside `[[ ]]`, `<` and `>` are
  still redirections.

### Changed

- **No Shift for the wheel and for selecting text:** while a program has the mouse, a terminal
  keeps its own wheel scrolling and text selection for Shift. ShellCraft no longer takes the mouse
  at the prompt when an alert shows (that put the wheel and selection behind Shift again, and
  made the wheel step through the history). At the prompt the terminal keeps it, so its wheel and
  its selection work as usual; `settings footer_mouse on` gives the footer's lines, an alert's ✖
  and the badge their clicks back at that price. Inside an attached subshell, where the footer
  needs the mouse, ShellCraft now supplies both itself: the wheel scrolls the subshell's page
  (and sends arrow keys to a full-screen program such as `less` or `man`), a left-button drag
  highlights text and a right-click copies it to the clipboard. A mouse report cut in two reads
  is held for the rest instead of being typed into the program.
- **The wheel scrolls the output again:** at the prompt the mouse is no longer reported to the
  shell just because a subshell or an alert is listed (that took the terminal's wheel and text
  selection away). The footer's lines, an alert's ✖ and the badge are clickable at the prompt
  with the new `footer_mouse` setting (off by default); inside a subshell they always are, and
  the keys (Ctrl-] N, ↓, Ctrl-] c, alert -c) work everywhere.
- **A new `help` screen:** grouped by purpose, at most 100 columns wide, no table. ShellCraft's
  own features (`sub`, `jobs`, `alert`, `clip`, `record`, `mcp`…) come first with a short
  description; Shell, Files, Text, Scripting and System are grids of names; the modules come
  last, in their own color, with the first clause of their summary (◆ marks your own).
  `help -a` describes every command; `help -k WORD` searches; `help NAME` and `man NAME` have
  the usage. In a pipe it is still one `name  summary` line per command.
  `local`, `shift` and `declare`, which only make sense inside a function, are not listed;
  `man function` has their pages now (each still has its own `man` page and completes).
- **The pager only when asked:** output taller than the window no longer opens the viewer by
  itself; it prints, so you can scroll back. `man` still opens it, and the new **`less`** (and
  `more`) page anything on demand: `ls -l | less`, `cat big.log | less`, `less FILE`. The
  `pager` setting now means: open man pages and `less` in the viewer (off: print them).
- **Footer lines:** a running subshell's line is plain (it was amber); the main line's color
  runs to the right edge like every other; a finished line has an × after `done` / `exit N`
  that removes it with a click.
- **Finished jobs clear themselves:** `sub ls` or `make &` used to stay in the stack as `done`
  until `jobs -c`. A job that ended is now reported at the next prompt (`[N] done: …`, plus a
  command job's output) and then leaves the stack, like a finished `ls &` in bash. `settings
  jobs_autoclear off` keeps the old behavior.
- **The stack is no longer cut at 8 subshells:** it may take up to half the screen. On
  Windows, clicks on the footer at the prompt are mapped from screen-buffer rows to window
  rows, so they land on the right line when there is scrollback.
- **`mcp start` waits up to 45 s** (was 15 s) for the server to start listening: a cold start
  that imports mcp and uvicorn on a slow disk or a busy machine sometimes needed more.
- **Alerts fold into the panel:** an alert sent from a subshell shows inline on that subshell's
  line, after its command, in its color; a badge at the right end of the `main` line counts every
  alert by level (`● 1  ● 1  ● 1 ▸`, a filled bubble in each level's color) and a click on it unfolds one row per alert (another folds
  them back). A critical alert always keeps its full row. Every level has the same ✖ mark, in
  its color, and a click on it dismisses the alert. Alerts without a subshell to fold into (no
  `sub` running) still show as rows. The `⤷` glyph is gone from the footer, the prompt (`[1]`
  inside `sub shellcraft`) and the job notes: many console fonts have no such character.
- **Shorter subshell lines:** the elapsed time and the word `running` are gone from the right
  end of a subshell's line (its color is the state; `jobs` has the times); a finished one still
  says `done`, `exit 2` or `killed`.
- **Shorter subshell panel:** the header line with the keys (`⤷ 1 subshell · click or Ctrl-] N
  switches · …`) is hidden, so the panel and its alerts take one row less. `settings panel_hint on`
  brings it back.
- **`jobs -k` stops a background command for real:** each background command job has a stop flag
  that every pipeline it runs inherits, so endless producers, `tail -f`, `sleep`, loops, modules
  checking `modkit.cancelled()` and quiet OS programs (killed by a watcher) all stop, and its
  threads end; before, it was only marked killed and kept running until the shell exited.
- One cancellation mechanism: a pipeline segment's stop flag is also `modkit`'s cancel event, so a
  module checking `modkit.cancelled()` stops when `head` has its lines or on Ctrl-C.

### Fixed

- **Footer lines never changed color on Windows:** a program in a subshell (`sub pwsh`, `sub
  shellcraft`) stayed `running` for good once it exited. pywinpty's `read()` is a blocking
  recv that gets no EOF when a ConPTY program exits (the pipe stays open until the pseudo
  console is closed), so a program that ends without printing anything more, such as `exit` in
  pwsh, never ended its job: the line stayed plain, never green or red, the `[N] done` note never
  came, and attaching waited on it. The reads now run in a helper thread while the job asks the
  process itself whether it is alive (`isalive()`, every 0.1 s), then closes the pipe to release
  the reader. Also on Windows: an exit status pywinpty reports late (`None`) is waited for
  instead of becoming 0, so a failing program is red `exit N` and not green `done`; a job end is
  reported once however it is noticed; typing into a program that has just ended no longer
  raises an `EOFError` in the attach loop; and a killed job is always red, whatever status the
  terminate left. Verified on Linux against a fake pywinpty process that behaves as its source
  says; the ConPTY itself could not be run here (`diag` shows what it reports).

- **Footer clicks inside a subshell stopped now and then:** a program in the subshell that turns
  its own mouse reporting on and off (the lite ShellCraft's completion menu and pager, `less`,
  `vim`, `htop`) passed those switches straight to the terminal, so the footer could lose the
  mouse until the next one. While a subshell is attached the footer keeps the mouse: the
  program's mode switches are replaced by the footer's (clicks in SGR form, plus the motion the
  program asked for), and its mouse events are handed to it in the form it asked for.
- **The clip register inside `sub shellcraft`:** a right-click on an alert is handled by the main
  shell (it draws the footer), so the copy went to the main shell's register and `clip`,
  `$CLIP` and Alt-v in a lite subshell found nothing. A lite subshell now asks the main shell
  for its register over a private channel (a local socket, a named pipe on Windows) locked with
  a random key that only `sub shellcraft` is given; the register stays in the main shell's
  memory, never on disk.
- **Right-click on an alert at the prompt:** since the wheel fix the prompt never asked for the
  mouse, so the terminal kept right-clicks and the ✖ and the badge did nothing. (Superseded: the
  prompt now leaves the mouse to the terminal again, see Changed; `settings footer_mouse on`
  brings those clicks back.) New keyboard ways: **Alt-c** copies the top alert, `clip -a N`
  copies alert N.
- **A blank toolbar row:** the usage toolbar stayed on into the next prompt (the input buffer
  still held the last command when the toolbar was decided) and drew as an empty colored row;
  with the footer at the prompt it sat right above it. The buffer is cleared first now. A
  footer that gets shorter (a fold, a subshell gone) clears the rows it gave up.
- **Alert clicks:** with alerts showing and no subshell, the prompt left the mouse to the
  terminal, so the × (and its hover) and the right-click did nothing; the mouse is now on
  whenever the panel shows. On Windows a release is reported without its button
  (`RIGHT;MOUSE_DOWN`, then `NONE;MOUSE_UP`), so the right-click copy and the badge click never
  fired; both now go by the button that was pressed, at the prompt and in a subshell's footer.
- **`clear` on Windows 11:** `clear` (and `banner -c`) now clear the screen and the scrollback
  the way the startup clear does, by writing the terminal codes directly. Before, they used Rich's
  clear, which Windows Terminal turned into scrolling the old text out of view, or skipped.

## [0.3.0] — 2026-10-13

Subshells and the subshell panel, alerts, the rc file and startup scripts, the aliases file,
mouse support, a user modules folder, the `shellcraft` package name, API keys in the OS keyring,
and Ctrl-C that reaches running modules.

### Changed (robustness and packaging)

- **The package is `shellcraft` now, not `core`** (a generic name that could collide with other
  packages in site-packages). Module authors: write `from shellcraft.modkit import …`. A `core`
  compatibility shim stays for the 0.3 releases: `import core.X` returns the very same module as
  `shellcraft.X` (with a DeprecationWarning), and `python -m core.cli` / `python -m core.alerts`
  still run, so modules and alert shims from 0.2 keep working. It is removed in 0.4.

### Added (robustness and packaging)

- **Ctrl-C reaches running modules:** `modkit.check_cancel()` (raises `Cancelled`, a
  `ModuleError`), `modkit.cancelled()` and `modkit.sleep()` (wakes up on cancel) let a module
  stop when the user presses Ctrl-C or an MCP call times out; the shell sets the worker thread's
  cancel event in both cases. The bundled `fetch`, `ip2geo`, `queryDns` and `queryCert` check it
  between targets and retries. With the spinner off, Ctrl-C now interrupts at once instead of
  after the module finished.

- **API keys in the OS keyring:** with the optional `keyring` package (the `keyring` extra) and a
  working backend, `settings NAME VALUE` stores the key in Windows Credential Manager / macOS
  Keychain / Secret Service and `config.json` keeps only `@keyring`; startup reads it back, `reset`
  deletes it. The `keyring` setting (on) chooses; `settings migrate-keys` moves keys stored in plain
  text, and the `settings` table warns while any are left. Without keyring nothing changes, and the
  README now says plainly that `config.json` is plain text and mode 600 does nothing on Windows.

- **Your own modules folder, `~/.shellcraft/modules`,** loaded next to the bundled one (or
  `--modules DIR`). A module there shadows a bundled one of the same name; `modules` has a `from`
  column and `help` marks them `(user module)`; hot reload watches both folders; `_helpers` import
  across both (yours first). `ingest` inside the shell now writes there instead of into a checkout.

### Added

- **Subshells:** `sub` opens your own shell, `sub PROGRAM` hands the terminal to any program
  (`vim`, `htop`, `ssh`, `python`…), `sub shellcraft` nests a ShellCraft (its prompt shows the
  depth), and `sub COMMAND` runs a builtin or module in the background with its output collected.
  Each is a numbered job. At the prompt a **panel** under it lists them one per line (the line
  under the mouse lights up, a click switches, ↓ moves into the panel and Enter picks); inside a
  subshell the same panel sits at the bottom of the terminal (a scroll region keeps it in
  place), clickable too, with the ● on the shell you are in. **Ctrl-] N** switches from anywhere, **Ctrl-]**
  alone comes back. `jobs` lists, selects, stops (`-k N`) and clears (`-c`) them, finished jobs
  are announced at the next prompt, and the `footer` setting turns the strip off. Each program
  has its own terminal emulator (`pyte`), so switching to it redraws its screen exactly,
  scrollback included; subshells are shown on the alternate screen, so the prompt comes back
  untouched; the wheel scrolls a subshell's scrollback; on Windows programs run on ConPTY
  through `pywinpty`. Programs run on a pseudo-terminal (POSIX); on Windows
  `sub PROGRAM` runs in the foreground. A `jobs` prompt segment shows how many are running.
- **The completion menu follows the mouse:** the entry under the pointer is highlighted (and put
  on the line, as ↑ / ↓ do), so a click picks what you see.
- **`ingest` inside the shell and `shellcraft ingest` from a terminal.** The module wizard and
  its checker were only reachable as `python tools/ingest`. The `ingest` builtin runs them in
  place with the shell's own modules folder, and a new module works at the next prompt;
  `shellcraft ingest` does the same from a terminal (the wheel now ships `tools.ingest`).
- **Mouse everywhere it helps, in small steps.** `settings mouse_always on` keeps the mouse on
  at the prompt: click to place the cursor, click a prompt segment to act on it (`⚡` → `help`,
  path → `ls`, `⎇ branch` → `git status`, `● mcp` → `mcp log`, `[✗ N]` → the last error), click
  the toolbar for the man page of the command being typed, wheel through the history or through
  Ctrl-R matches. In the pager the status bar's keys are clickable, a scrollbar on the right can
  be clicked or dragged, and a click on a line marks it for `n` / `N`.
- **Clickable paths** (the `links` setting, on): `ls`, `tree`, `find`, `du` and `grep` print file
  names as terminal hyperlinks, so Ctrl-click opens them. Pipes and MCP still see plain names.
- **Mouse in the completion menu:** click an entry to pick it, wheel to scroll the list. The
  terminal is asked for the mouse only while a menu is open (and in the pager, as before), so
  text selection and wheel scrollback at the prompt are unchanged. The `mouse` setting turns it
  off. On Windows the console's mouse input is switched on with the menu and Quick Edit mode is
  restored when it closes.

### Added

- **Asking for a value:** `name = input("What is your name? ")`, `n = int(input("How many? "))` and
  `float(input(…))` (which ask again until the answer is a number) on a line of its own (at the prompt,
  in an rc file, in a sourced file) and `read [-p PROMPT] [-s] NAME` store the answer in a shell
  variable for later lines (`$name`). Interactive shell only; Ctrl-C cancels with status 130.
- **`~/.shellcraft/rc` and `source FILE`:** a startup file of ShellCraft command lines, run as if
  typed when the interactive shell starts (never in MCP mode): themes, `export`, `cd`, aliases,
  modules, `sub`, `alert`, multi-line `for` / `if`. A failing line is reported with its line number
  and the rest still run; `source` (or bash's `. FILE`) runs any such file by hand, so an rc file
  can pull in others (`if [ -f ~/.shellcraft/rc.work ]; then . ~/.shellcraft/rc.work; fi`).
- **Startup scripts (first version):** every `.py` in `~/.shellcraft/startup/` runs at startup in
  name order, before the first prompt. Scripts hand environment variables back through
  `$SHELLCRAFT_ENV_FILE` (`NAME=value` lines), which the shell, subshells and modules then inherit.
  Failures and timeouts (30 s) are reported without stopping the shell. `startup`, `startup run`,
  `settings startup`, `--no-startup`; an example in `templates/startup/`.
- **An aliases file:** aliases live in `~/.shellcraft/aliases`, one `alias NAME='COMMAND'` per line
  like `~/.bash_aliases`, with comments. Hand edits apply to the next command; `alias` and
  `unalias` rewrite only their own lines; `alias --file` shows the path; unusable lines are listed
  by `alias` with their line number. Aliases saved in `config.json` by older versions move into the
  file once, at startup.
- **Alerts in the status panel:** `alert [-l info|warning|critical] [-t SECONDS | -s] MESSAGE`
  shows a green, amber or red line at the top of the panel (three at most, the most severe
  first, timed out or dismissed with `alert -c`). Subshells get an `alert` command on their
  PATH, a nested ShellCraft forwards to the main one, `shellcraft-alert` works from any script,
  and AI clients call the new **`send_tui_alert`** MCP tool. Every alert names its sender
  (`sub 2 (bash)`, `mcp · claude-code`…). Senders drop a file into the owner-only spool folder
  `~/.shellcraft/alerts/`, which the shell watches: no port, no token.
- **What each file in `modules/` does:** every `name.py` is a command shown by `help`; one
  without `run()` is run as a plain Python script (`python name.py ARGS`, stdin piped, never
  imported). A `.skill` makes it an MCP tool (modules without one are local to the shell), a
  `.md` gives it a man page, and `_name.py` is a helper that modules and scripts import
  (`import _name`, `from . import _name`); editing a helper reloads the modules that use it.
- **Windows:** the prompt session is bound to `CONIN$` / `CONOUT$` explicitly, and Windows
  Terminal (`WT_SESSION`) now defaults to `SHELLCRAFT_CONSOLE=legacy-keys`, the setup that
  works there; the classic console keeps `direct`.

### Fixed

- The pager's ← / → had nothing to scroll for text output: Rich wrapped long lines at the
  window width before the pager saw them. Text now reaches the pager unwrapped (`w` wraps it).
- Inside a subshell on Windows, the arrow, Home / End, PgUp / PgDn, Insert and Delete keys were
  dropped in a console without VT input; they are now sent to the program as VT sequences.

### Changed

- The `links` setting (clickable file names in `ls`, `tree`, `find`, `du`, `grep`) is now off by
  default: accidental clicks in a listing opened files. `settings links on` brings it back.
- `clear_on_start` clears the scrollback as well (`ESC[3J`), so the shell starts on a fresh
  screen like a new program, instead of leaving the old lines above.
- The default `--mcp-http` / `mcp start` address is `127.0.0.1:38765` (was `:8765`), to stay
  clear of the ports other local tools commonly take.

### Fixed

- `help` printed `Paged(value=...)` instead of the command table.

## [0.2.0] — 2026-10-12

The fourth assessment (`UPGRADE-ASSESSMENT.md`, now in the git history) in one release: every crash
it found is fixed, the TUI got the polish it asked for, and the bash habits it listed now work.

### Fixed

- `main.py` cleared the screen before parsing arguments, in every mode: the MCP stdio stream
  started with an escape sequence and `--version` wiped the terminal. The interactive shell
  clears the screen now (the `clear_on_start` setting), nothing else does.
- Redirecting to `~nosuchuser` killed the shell with a traceback; a deleted working directory
  crashed the prompt. Both are clean errors, the prompt shows `(deleted)`, and `Shell.loop()`
  catches any unexpected exception as a last resort.
- A lone `&` was passed on as an argument (`rm -f a & ls` deleted both). It is rejected.
- `cp -r DIR DIR/sub` recursed until `RecursionError`; it refuses to copy a directory into itself.
- A malformed `config.json` section (`"env": [1]`) crashed startup in every mode; bad sections are
  dropped with a warning. Two open shells overwrote each other's config; saves now merge only the
  keys the session changed and write atomically through a temporary file.
- `grep -m 0` printed one line; a quoted `~` in a redirect was expanded; a redirect created
  missing directories; `sort -u -k N` deduped whole lines; `wc -c` counted characters (`-m` does
  now); history redaction missed `\settings`, `a ; settings KEY v` and aliases of `settings`;
  `mcp restart` truncated the previous log; slow builtins (`du /`, `find /`) showed no spinner.
- The version was defined twice; `pyproject.toml` reads it from `core.__version__`.

### Added

- **Pager:** `w` wraps long lines, `←`/`→` scroll sideways, `d`/`u` half pages, `?` lists the
  keys, the mouse wheel scrolls, the status bar names what is being paged, and search matches use
  the theme's accent color.
- **help:** `help NAME` (summary and usage), `help -k WORD`, section headers instead of a kind
  column, the usage column on wide terminals, a footer with the module count and warnings, and a
  plain layout under 80 columns. `for`/`if` are listed as keywords, not commands.
- **Prompt:** `⎇ branch` (read from `.git/HEAD`, no git needed), `⏱ 4.2s` after a slow command,
  a clock, and the `prompt` builtin to choose the segments (`prompt name path git status`,
  `prompt -r clock`). The `transient_prompt` setting collapses the prompt after Enter.
- **Input line:** syntax highlighting (known commands, operators, quotes, switches, variables;
  the `highlight` setting), a bottom toolbar with the usage of the command being typed (the
  `toolbar` setting), a styled Ctrl-R search, completion menus in the theme's colors, quote-aware
  path completion (names with spaces are quoted), positional values from `.skill` files
  (`queryCensys <Tab>` → `host cert search`), and completion for `help`, `theme preview` and `prompt`.
- **Themes:** `theme preview NAME`, `theme reset`, the light-terminal presets `solarized-light`
  and `catppuccin-latte`, `surface` / `surface_text` / `light` keys for custom themes, and theme
  warnings shown in the banner.
- **Feedback:** the spinner shows the elapsed time after 3 s and `Ctrl-C cancels` after 10 s;
  an unknown switch error shows the usage line and a "did you mean" (`grep -ignore` → `-i,
  --ignore-case`); `ls` colors archives, media, source and build leftovers by extension.
- **Commands:** `grep -r -A -B -C -w -x -o -e -L -h -H -E`, `ls -t -S -r -R -d`, `head -c`,
  `tail -c`, `tail -f`, `find -size -mtime -newer -empty -path -ipath -not/!`, `sort -h -V -s -k N,M`,
  `cat -s -A -E -T`, `tree -I --du`, `wc -m`, and the new builtins `printf`, `seq`, `xargs`,
  `basename`, `dirname`, `realpath`, `stat`, `whoami`, `hostname`, `uname`, `sleep`, `time`,
  `type`, `export`, `unset`.
- **Scripting:** shell variables (`x=1`, `$x`), `export` / `unset`, `$HOME` and other environment
  variables in the interactive shell (never over MCP), `$((…))` arithmetic, `$(…)` and globs on
  plain command lines, `while` / `until` (bash and `-py`), `case … esac`, piping or redirecting a
  whole loop (`done | sort`, `} > out`), and `!N` / `!-N` / `!prefix` / `!*` history expansion.
- **Redirections:** `< file`, `2> file`, `2>> file`, `2>&1` (a failure's message becomes the
  command's output, so `cmd 2>&1 | grep` works) and `&> file`.
- **MCP:** a per-call timeout (`SHELLCRAFT_MCP_TIMEOUT`, default 120 s) and caps on stdin and
  output; `expose = false` in a `.skill` keeps a module local (not a tool, and refused inside
  `shellcraft_pipeline`); `--expose NAME,...` narrows the tool list; `--mcp-http` requires a bearer
  token (`--token`, `$SHELLCRAFT_MCP_TOKEN`, or one generated and printed; `--no-token` opts out);
  `mcp start` generates a token per server and prints the `claude mcp add` line; the child server
  exits when the shell that started it dies, even after `kill -9`.
- **Startup:** modules are imported on first use (the `lazy_modules` setting), so a module's
  slow imports don't delay the prompt; `.md`-only edits are re-read without re-importing, and hot
  reload reloads only the changed modules; `to_text()` wraps at the terminal's width; `show()`
  renders once and sends very large plain text straight to the pager; a module's `run()` may
  return an iterator of lines.
- **Tooling:** `ruff` and `mypy` configuration in `pyproject.toml` (the `dev` extra installs
  them), CI for GitLab and GitHub (pytest on Linux and Windows for Python 3.11–3.14, lint, the
  module checker, and a packaging smoke test), and this changelog.

### Changed

- `wc -c` counts bytes, as GNU `wc` does; `wc -m` counts characters.
- `help` prints `name — summary` lines on terminals narrower than 80 columns.
- `grep` with `-r` prefixes every line with the file name, even for one directory.

## [0.1.0] — 2026-09-27

The first release: pipelines and redirection, the ported builtins, modules with `.md` manuals
and `.skill` descriptions, bash-style and `-py` loops and conditions, aliases, history, themes,
the pager, settings with API keys, the MCP server (stdio and HTTP), and the module authoring kit.
