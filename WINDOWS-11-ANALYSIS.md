# ShellCraft on Windows 11 — console analysis

> 2026-10-13, `main` @ GitLab `d039752` / GitHub `0a91322`. prompt_toolkit 3.0.53, Rich 15.0.0, Python 3.14.
> A code-level analysis: ShellCraft's Windows paths (`core/console.py`, `core/stdio.py`, `core/shell.py`,
> `core/jobs.py`, `core/footer.py`, `core/output.py`), the prompt_toolkit internals they rely on, and what
> Windows Terminal and the classic console (conhost) do with each. Nothing here was run on Windows; the
> items marked **(bug)** are certain from the code, the ones marked **(check)** need the checklist at the end.

ShellCraft has three console setups on Windows, chosen by `SHELLCRAFT_CONSOLE`:

| Mode | Keys | Output | Default when |
| --- | --- | --- | --- |
| `direct` | prompt_toolkit's `Vt100ConsoleInputReader` on `CONIN$`, with `ENABLE_VIRTUAL_TERMINAL_INPUT`: the console turns every key into a VT byte sequence (`ESC [ A`, `\x7f`…) that prompt_toolkit's vt100 parser decodes, as on Linux | `DirectOutput`: VT sequences written to `CONOUT$` through Python's console stream | the classic console (`WT_SESSION` not set) |
| `legacy-keys` | prompt_toolkit's `ConsoleInputReader` on `CONIN$`: `ReadConsoleInputW` key records, mapped by virtual-key code and character | the same `DirectOutput` | Windows Terminal (`WT_SESSION` set), since 2026-10-13 |
| `native` | prompt_toolkit's own `Win32Input` / `Windows10_Output` on the std handles | theirs | never; for comparison |

The two defaults differ only in the key reader. Everything below is organised by the four areas asked
about, each ending with its findings.

---

## 1. Encoding

### Output

- **The interactive shell writes to `CONOUT$` opened as a Python text stream with `encoding="utf-8"`.**
  Python opens `CONOUT$` as a `_WindowsConsoleIO`, whose writes go through `WriteConsoleW` (UTF-16 to the
  console); the UTF-8 the text layer produces is decoded back to UTF-16 on the way. So every character
  ShellCraft prints reaches the console whatever the active code page is (`chcp 437`, `1252` or `65001`
  make no difference). Box drawing, `⚡`, `⤷`, `●`, `▲`, `✖` are all safe at the encoding level.
- **Pipes and files**: `utf8_stdio()` reconfigures `sys.stdout` / `sys.stderr` to UTF-8 when they are not
  the console, so `shellcraft … > out.txt` from PowerShell and the MCP stdio stream are UTF-8 (Python
  would otherwise use the ANSI code page there, cp1252, which cannot encode `│` and raises).
- **OS programs** run with `capture_output=True` are decoded as UTF-8; `cmd.exe` builtins run with
  `cmd /u` and are decoded as UTF-16, because their output follows the OEM code page otherwise.
  A third-party program that writes cp1252 (older tools) is decoded with `errors="replace"` and shows
  `�` for accented characters. **(check)** `sub` is the better path for such programs: on ConPTY they
  write to a pseudo-console and the terminal does the decoding.
- **Plain scripts in `modules/`** run with `PYTHONIOENCODING=utf-8` set, so their `print()` is UTF-8 on
  both platforms.
- **Rich** is built with `legacy_windows=False` and writes to the `CONOUT$` stream directly, never
  through the legacy Win32 renderer, so colours and box drawing are VT sequences everywhere.

### Input

- Keys reach prompt_toolkit as UTF-16 code units in `KEY_EVENT_RECORD.uChar.UnicodeChar`.
  **`legacy-keys` merges surrogate pairs** (`_merge_paired_surrogates`), so an emoji typed or pasted
  at the prompt becomes one character. **`direct` does not**: `Vt100ConsoleInputReader._get_keys`
  yields each code unit to the vt100 parser, so a character outside the BMP (emoji, some CJK) becomes
  two lone surrogates in the line, which then print as `??` and break `encode()` in some commands.
  **(bug, upstream)** — an argument for `legacy-keys` on every Windows console, not only Windows
  Terminal (see §5).
- **Inside an attached subshell** (`core/jobs.py::_windows_terminal`) ShellCraft reads the key records
  itself and encodes them with `"".join(chars).encode("utf-8", "surrogatepass")`. A surrogate pair
  therefore becomes two 3-byte sequences (CESU-8), not the 4-byte UTF-8 the program on the ConPTY
  expects: an emoji typed into `sub pwsh` arrives as garbage. **(bug)** Fix: re-pair before encoding
  (`text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")`, then UTF-8).
- Bracketed paste: Windows Terminal supports `?2004h` and prompt_toolkit's vt100 output enables it in
  `direct` mode, so a multi-line paste lands in the buffer as one edit. In `legacy-keys` the classic
  reader uses its own paste heuristic (many keys in one `ReadConsoleInputW` batch). conhost ignores
  `?2004h`; a multi-line paste there runs line by line, as in a Linux terminal without bracketed paste.
  **(check)** Paste a three-line snippet in each mode.
- The alert spool, the config file, history and job logs are all written and read as UTF-8 explicitly.

**Encoding verdict:** output is correct on every console. Input is correct for the BMP in both modes;
non-BMP characters are wrong in `direct` (prompt_toolkit) and inside subshells (ShellCraft).

---

## 2. Display

### Size and wrapping, the rule everything else follows

- prompt_toolkit's own `Windows10_Output` reports the visible window **minus its last column** ("we
  avoid the right margin, windows will wrap otherwise"), and writes each flush with the console in
  exactly `PROCESSED_OUTPUT | VIRTUAL_TERMINAL_PROCESSING`: no wrap at end of line. `DirectOutput`
  copies both, and the Rich console takes its width from the same output, so the prompt, the
  completion menu, the toolbar panel and every Rich table stop one column short of the edge. This is
  what made Windows Terminal stable after the 2026-10-09 rewrite, and it is right.
- Between prompts the console rests in `PROCESSED | WRAP_AT_EOL | VT`, with
  `DISABLE_NEWLINE_AUTO_RETURN` **cleared**: a character written in the last column moves the cursor to
  the next line at once (no "pending wrap" as in xterm). Fine for Rich output, which never reaches the
  last column.
- **The footer inside a subshell is drawn at the full width** (`Footer.size()` uses
  `os.get_terminal_size(CONOUT$)`, the whole window, and `panel_lines()` pads every line to it). On
  Windows that last cell triggers the immediate wrap: after each footer row the cursor drops a line,
  and the last row of the screen pushes the viewport up by one, so the footer creeps and the scroll
  region above it is one row off. On Linux the terminal's pending wrap hides this. **(bug)** Fix:
  take the footer's width from the app session's output like Rich does (`cols - 1`), or pad to
  `cols - 1` on Windows.
- **An attached program's output is written under the resting mode** too. A program on the ConPTY
  believes it has a normal terminal with pending wrap (bash, vim and PowerShell all print full-width
  lines and rely on it), but `CONOUT$` wraps immediately, so a full-width line is followed by a blank
  line and cursor-relative redraws (PSReadLine, vim) land one row low. **(bug)** Fix: while attached,
  put `CONOUT$` in `PROCESSED | WRAP_AT_EOL | VT | DISABLE_NEWLINE_AUTO_RETURN` (xterm semantics) and
  restore the resting mode afterwards; the pseudo-terminal should keep the full width it already gets.
- The pager (`core/output.py::page`) is a full-screen prompt_toolkit application on the same
  `DirectOutput`, so it has the same width rule and no wrap problem. Its `←` / `→` scrolling got real
  long lines on 2026-10-13 (text was wrapped before it reached the pager).

### Escape sequences the shell depends on, and who supports them

| Sequence | Used by | Windows Terminal | Classic console (conhost, Windows 11) |
| --- | --- | --- | --- |
| SGR colours, 24-bit | everything | yes | yes |
| `ESC[H ESC[2J ESC[3J` at start | `clear_on_start` | yes (3J clears scrollback) | 2J yes; 3J accepted |
| `ESC[?1049h/l` alternate screen | subshell pages, pager | yes | yes |
| `ESC[1;Nr` scroll region | footer | yes | yes |
| `ESC 7` / `ESC 8` save/restore cursor | footer | yes | yes |
| `ESC[?1000h ESC[?1006h` SGR mouse | menus, panel, footer clicks | yes, as VT input sequences | **no**: conhost reports the mouse only as `MOUSE_EVENT` records |
| `ESC[?2004h` bracketed paste | prompt | yes | ignored |
| OSC 8 hyperlinks (`links` setting, off) | `ls`… | yes | ignored, plain text |
| OSC 0 window title | none yet | yes | yes |

### Glyphs and width

- `⚡` (U+26A1) is **two cells wide** for prompt_toolkit's `get_cwidth` and in Windows Terminal, but
  the classic console draws it in one cell: the prompt's first line is one column short there and the
  `╭─ … ─╮` frame does not close. Cosmetic, long known (TODO). `⤷` (U+2937) is one cell everywhere
  but **missing from Consolas**; conhost shows a box unless the font is Cascadia Mono. `●`, `○`, `▲`,
  `✖`, `│`, `┃` are in both fonts.
- Truecolor: Windows Terminal and Windows 11 conhost both render 24-bit colour; Rich and the prompt are
  forced to truecolor on `CONOUT$` (`ColorDepth.TRUE_COLOR` unless `PROMPT_TOOLKIT_COLOR_DEPTH` or
  `NO_COLOR` says otherwise).
- `--diag` / `settings diagnostics on` prints the window, the buffer, the cursor, both console modes
  before and after, the code pages and where the std handles point: the first thing to read when a
  Windows report says "garbled".

**Display verdict:** the prompt, Rich output, the pager and the panel under the prompt are sound. The
two full-width cases — the footer inside a subshell and an attached program's own output — are wrong
on Windows because of the console's immediate wrap, and both have small fixes.

---

## 3. Backspace

- **At the prompt, `direct`:** Windows Terminal sends `\x7f` for Backspace under VT input; conhost sends
  `\x7f` too in that mode. prompt_toolkit's vt100 parser maps `\x7f` and `\x08` both to `ControlH`, and
  `Keys.Backspace` *is* `ControlH`, bound to delete-before-cursor. Works.
- **At the prompt, `legacy-keys`:** the key record carries `\x08`; the classic reader maps `b"\x08"` to
  `ControlH`. Works. Ctrl+Backspace: Windows Terminal sends `\x7f` with the Ctrl flag; the classic reader
  turns it into `ControlH` as well (prompt_toolkit has no Ctrl-Backspace key), so **Ctrl+Backspace
  deletes one character, not a word**, in both modes. Alt+Backspace (`escape backspace`) deletes a
  word. **(check)** Minor.
- **The old "Backspace does nothing in Windows Terminal" report** came from the delayed-wrap output
  setup (see the module docstring of `core/console.py`): the key worked, the redraw drifted. Fixed by
  the Windows10-style flush mode. It is the reason the output side must not change back.
- **Inside an attached subshell:** ShellCraft forwards whatever the console gives: `\x7f` under VT
  input (the reader sets `ENABLE_VIRTUAL_TERMINAL_INPUT` while attached, on both consoles), and under
  the classic path the record's `\x08`. PowerShell and cmd handle both. Git Bash / MSYS bash expect
  `^?` (`stty erase`) and treat `\x08` as a cursor move in some setups. Since the attached reader always
  turns VT input on, it gets `\x7f` and bash is happy. **(check)** `sub bash` from Git Bash: Backspace
  must erase.
- **In the pager, the panel and the completion menu:** Backspace is a prompt_toolkit key like any other;
  nothing Windows-specific.

**Backspace verdict:** correct in both modes and in subshells; Ctrl+Backspace deleting one character
is the only gap, and it is prompt_toolkit's.

---

## 4. Arrow movement

### At the prompt

- **`direct`:** the console sends `ESC [ A/B/C/D` (and `ESC [ 1;5 C` for Ctrl+→, `ESC [ 1;2 C` for
  Shift+→); the vt100 parser maps them exactly as on Linux: history up/down, cursor left/right, word
  moves with Ctrl, selection with Shift. Home/End arrive as `ESC [ H` / `ESC [ F`, Delete as
  `ESC [ 3 ~`. All in prompt_toolkit's table. The one subtlety is the Escape key itself: the parser
  waits briefly after a lone `ESC` to see whether a sequence follows (`prompt_toolkit`'s flush
  timeout), so Esc to leave the subshell panel reacts after that delay; the panel binding is `eager`
  so it is not swallowed.
- **`legacy-keys`:** the record has no character for an arrow, and `ConsoleInputReader.keycodes` maps
  the virtual-key code (`0x25`–`0x28`) to `Keys.Left` … `Keys.Up`, then applies the Ctrl and Shift
  flags to produce `ControlLeft`, `ShiftRight` and so on. Same bindings. Works.
- **Numpad arrows with NumLock off** produce the same virtual-key codes: fine in both modes.
- **The subshell panel's ↓ / ↑ / Enter / Esc** are ordinary bindings on top of these keys.

### Inside an attached subshell (`core/jobs.py`)

- The attached reader turns on `ENABLE_VIRTUAL_TERMINAL_INPUT`, so on Windows Terminal and on
  Windows 11 conhost the arrows arrive as `ESC [ A`… characters and are forwarded as typed. For a
  console that does not honour VT input, `win_key()` (added 2026-10-13) maps the bare virtual-key
  codes of ↑ ↓ → ←, Home, End, PgUp, PgDn, Insert and Delete to their VT sequences. In that fallback
  the modifier flags are dropped: Ctrl+→ becomes a plain → for the program. **(check)**, minor.
- The Escape prefix for switching, `Ctrl-]` then a digit, is independent of the arrows; and the
  0.6 s wait after `Ctrl-]` alone is what detaches.

### In the pager

- ↑ ↓ ← → are pager bindings on the same `DirectOutput` / reader pair; nothing Windows-specific.
  `←` / `→` scroll sideways through long lines since 2026-10-13.

**Arrows verdict:** correct in both modes, in the panel and in the pager; inside subshells correct
with VT input and acceptable without it.

---

## 5. Mouse, and why `legacy-keys` should probably be the default everywhere

- `direct` on **conhost**: the `Vt100ConsoleInputReader` only reads `KEY_EVENT` records, and conhost
  never turns the mouse into VT sequences, so clicks in the completion menu and the panel do nothing
  there. **(bug, by design of the mode)**
- `legacy-keys` on both consoles: `ConsoleInputReader._handle_mouse` turns `MOUSE_EVENT` records into
  `WindowsMouseEvent` key presses, which prompt_toolkit's mouse bindings and ShellCraft's panel
  handlers understand; `DirectOutput.enable_mouse_support` sets `ENABLE_MOUSE_INPUT` and clears Quick
  Edit while a menu or the panel shows, and restores exactly those bits after. Works on both.
- Inside an attached subshell, ShellCraft's own reader takes only key events, so footer clicks need
  the SGR sequences that Windows Terminal provides under VT input; in conhost the footer is not
  clickable (Ctrl-] N works). **(check)**

Put together: `legacy-keys` has no gap that `direct` fills — it merges surrogates, delivers the mouse
on both consoles, and handles arrows and Backspace identically — while `direct` loses non-BMP input and
the mouse on conhost. The 2026-10-13 default (`legacy-keys` under `WT_SESSION`, `direct` elsewhere)
should become **`legacy-keys` on every Windows console**, with `direct` kept as an opt-in for
comparison. The ShellCraft side changes in one line of `console_mode()`.

---

## 6. Findings, ranked

| # | What | Where | Severity | Fix |
| --- | --- | --- | --- | --- |
| 1 | The footer inside a subshell is padded to the full width; the console's immediate wrap pushes it off by a row | `core/footer.py` `size()` / `panel_lines()` | high (bug) | width from the app session's output (`cols - 1`), as Rich uses |
| 2 | An attached program's output is written without pending wrap; full-width lines and cursor-relative redraws drift | `core/jobs.py` `_windows_terminal()` | high (bug) | set `PROCESSED \| WRAP \| VT \| DISABLE_NEWLINE_AUTO_RETURN` on `CONOUT$` while attached, restore after |
| 3 | Surrogate pairs typed into a subshell are sent as CESU-8 | `core/jobs.py` reader | medium (bug) | re-pair before UTF-8 (`encode("utf-16","surrogatepass").decode("utf-16","replace")`) |
| 4 | `direct` mode loses non-BMP input and the mouse on conhost | prompt_toolkit's VT reader | medium | default to `legacy-keys` everywhere on Windows |
| 5 | `alert` shim is `alert.cmd` only; Git Bash / MSYS inside `sub bash` will not find `alert` | `core/alerts.py` `shim_dir()` | low | also write the POSIX `alert` script on Windows |
| 6 | Ctrl+Backspace deletes one character | prompt_toolkit | low | bind `c-h` with the Ctrl flag… not available; document Alt+Backspace |
| 7 | `⚡` one cell wide in conhost, `⤷` missing in Consolas | fonts | cosmetic | note in README; `prompt name` without the icon is the workaround |
| 8 | Ctrl/Shift+arrow modifiers dropped in the no-VT-input subshell fallback | `core/jobs.py` `win_key()` | low | map the `ControlKeyState` flags to `ESC [ 1;5 C` style |

Items 1–3 and 5 are a few lines each and can be done without a Windows machine; 4 is a one-line
default change. None of them changes the Linux behaviour.

---

## 7. The Windows 11 checklist (30 minutes, both consoles)

Run each in Windows Terminal (PowerShell profile) and in the classic console
(`conhost.exe` → `python main.py`), first with the default mode, then with
`$env:SHELLCRAFT_CONSOLE = "direct"` and `"legacy-keys"`:

1. `python tools\winconsole_probe.py` — type, Backspace in the middle, Tab twice near the bottom of
   the window, the four arrows, Ctrl+←, Shift+→, resize. The toolbar shows every key received.
2. `python main.py --diag` — keep the report; `window` and `buffer` sizes and both console modes.
3. `help`, `man grep` (↑ ↓ ← → `w` `q`), `ls -l` in a full window: no blank line after full-width rows.
4. Type `é`, `ü`, then an emoji from the Windows emoji picker (Win+.) at the prompt: one character
   each, `echo` prints them back.
5. `h`, then click an entry in the completion menu; `settings mouse_always on`, click `⚡`, scroll
   the wheel over the input line. Then `settings mouse_always off`.
6. `sub pwsh`: `Get-ChildItem`, `dir` (long lines), ↑ for PSReadLine history, ← → editing, Backspace,
   an emoji, `Ctrl-]`, `Ctrl-] 1` back, `exit`. Watch the footer stay on the last rows.
7. `sub cmd`, `sub bash` (if Git is installed): the same, plus `alert -l warning hello` inside each.
8. `alert` from the prompt, `alert --list`, `alert -c`.
9. `python main.py --mcp < NUL` and `> NUL` — no traceback (the console fixes exist for these).
10. Paste three lines into the prompt.

Record the mode, the console and the step for anything that misbehaves; `--diag` plus the probe's
report are enough to fix most of it from Linux.
