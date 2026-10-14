# ShellCraft — UI improvements

> UI polish only: how ShellCraft looks, reads and responds, on Linux and above all on Windows 11.
> 2026-10-14, ShellCraft 0.3.0+ (`main` @ `be71387`). Based on ShellCraft's current code, open-source
> terminal applications that set the bar today (fish, atuin, helix, starship, Textual, lazygit-style
> TUIs), and Microsoft's own documentation of what Windows Terminal supports. Sources at the end.
>
> Items are marked **Quick** (an hour or two), **Medium** (a day) or **Large** (several days), and
> **WT** when they depend on Windows Terminal, **both** when Windows Terminal and the classic
> console (conhost) behave the same.

---

## 1. Where things stand

ShellCraft's UI is already richer than most shells': themed two-line prompt with git branch,
status and duration, syntax highlighting, a usage toolbar, ghost text, a themed completion menu
that follows the mouse, a full pager (search, wrap, sideways scroll, scrollbar), error panels, a
subshell panel and footer, alerts with severities, and ten themes. The gaps are not in features
but in three places:

1. **It doesn't talk to the terminal.** ShellCraft emits no window title, no prompt marks, no
   current-directory report and no progress. Windows Terminal supports all four (and so do most
   Linux terminals); they are cheap and they make ShellCraft feel native in Windows 11.
2. **It assumes one kind of terminal.** The same glyphs and spinner are drawn everywhere. The
   classic Windows console (still what opens for some launches) has no font fallback: the braille
   spinner shows boxes, `⤷` is missing from Consolas, and `⚡` is one cell wide there while
   prompt_toolkit counts two, which misaligns the prompt.
3. **Discoverability stops at `help`.** There's no way to search all commands, settings and keys
   at once, the `Ctrl-]` prefix shows nothing while waiting for its second key, and Ctrl-R is a
   plain incremental search where atuin-style pickers are now the norm.

---

## 2. Windows 11 ground truth

What a UI decision has to account for on Windows 11 today:

| | Windows Terminal | Classic console (conhost) |
| --- | --- | --- |
| When users get it | The default terminal on Windows 11 | Old shortcuts, some IDE consoles, `conhost.exe` launches |
| Truecolor, VT sequences | yes | yes (Windows 10+ with VT on, which ShellCraft sets) |
| Font fallback (emoji, braille, symbols) | yes | **no**: missing glyphs are boxes |
| Emoji / combined characters width | grapheme clusters by default since 1.22 (`compatibility.textMeasurement`) | per code point: a family emoji takes 8 cells instead of 2 |
| Mouse as VT sequences | yes | no (Win32 mouse records only) |
| Progress ring + taskbar (OSC 9;4) | yes (1.6+) | no, ignored |
| Prompt marks (OSC 133) | yes (1.18+, with marks on the scrollbar) | ignored |
| Current directory (OSC 9;9) | yes: duplicate tab / split pane opens there | ignored |
| Synchronized output (mode 2026) | yes (1.25 preview, 1.24 stable) | ignored |
| Sixel images | yes (1.22+) | yes in 1.22's conhost |
| OSC 52 clipboard, bracketed paste | yes | no |

**Rule of thumb for ShellCraft:** detect the terminal once at startup (`WT_SESSION` set means
Windows Terminal; the existing console code already knows when it is on conhost), pick a
**capability tier**, and draw with the glyphs and sequences that tier supports. Every
terminal-integration sequence below is harmless where unsupported (terminals ignore unknown OSC),
but glyphs are not, so the glyph tier matters most.

---

## 3. Recommendations

### 3.1 Capability tiers and glyph fallbacks — **Medium, both** — do this first

Starship, the most widely used prompt, ships a "No Nerd Fonts" preset and a "Plain Text
Symbols" preset for terminals that can't draw icons, and plans to make the no-Nerd-Fonts one its
default. ShellCraft needs the same idea, chosen automatically:

| Tier | When | Prompt name | Spinner | Panel / alert marks |
| --- | --- | --- | --- | --- |
| `rich` | Windows Terminal, Linux terminals | `⚡shellcraft` | `dots12` (braille) | `● ○ ▲ ✖ ⤷` |
| `basic` | classic console, `TERM=linux` | `»shellcraft` (1 cell everywhere) | `line` (`- \ \| /`) | `* o ! x >` |
| `ascii` | `settings glyphs ascii`, screen readers, logs | `shellcraft` | none (static `...`) | `[*] [ ] [!] [x] >` |

- A `glyphs` setting (`auto` / `rich` / `basic` / `ascii`), `auto` by default.
- Put every glyph the UI draws behind one table (`shellcraft/glyphs.py`), so the prompt, panel,
  footer, alerts, banner and error panels can't drift.
- Fixes the `⚡` width mismatch on conhost (the prompt frame stops one column short today) and
  `⏱`, which Windows Terminal may draw as a 2-cell emoji while prompt_toolkit counts 1: prefer
  text-presentation symbols (`◷`, or plain `4.2s`) in the prompt.

### 3.2 Talk to the terminal — **Quick each, WT and most Linux terminals**

All four are a few lines in the prompt / runner and are ignored by terminals that don't know them.

- **Window and tab title** (OSC 0): `shellcraft · ~/projects` at the prompt, `shellcraft · grep -r …`
  while a command runs, `shellcraft · sub 2: bash` while attached. Several ShellCraft tabs become
  tellable apart at a glance. A `title` setting to turn it off.
- **Current directory** (OSC 9;9 on Windows, OSC 7 on Linux): emitted with each prompt, so Windows
  Terminal's *Duplicate tab* and *Split pane* open in the same folder (Microsoft's tutorial shows
  exactly this for cmd, PowerShell and fish).
- **Prompt marks** (OSC 133 A/B/C/D, with the exit status on D): Windows Terminal 1.18+ then shows
  each command as a mark on the scrollbar (green / red by status), and *scroll to previous /
  next command* jumps between prompts. Linux terminals (kitty, WezTerm, foot, GNOME's VTE) use the
  same marks.
- **Progress** (OSC 9;4): while the spinner runs, send the indeterminate state (3), clear it (0)
  when done, the error state (2) when the command failed. Windows Terminal shows a ring on the tab
  and on the taskbar button, so a long `queryCensys` is visible from another window. Modules
  that know their progress (`modkit.progress(done, total)`, new) set a real percentage. Works in
  GNOME Terminal, Konsole, kitty and ghostty too.

### 3.3 Flicker-free redraws — **Quick, WT 1.24+ and modern Linux terminals**

Wrap every full redraw — the pager's frames, the footer's repaint inside a subshell, the subshell
page swap, `theme preview` — in synchronized output (`ESC[?2026h` … `ESC[?2026l`). Terminals that
support it show the frame at once instead of painting it line by line; the rest ignore it.
Windows Terminal added it in 1.25 preview / 1.24 stable, after years of the TUI-flicker complaints
that motivated it. The footer redraw after every chunk of subshell output is the most visible
beneficiary.

### 3.4 History search like atuin — **Medium, both**

atuin set the current expectation for Ctrl-R: a full list instead of one match at a time, filter
modes cycled with Ctrl-R (*global, host, session, directory, git repository*), search modes cycled
with Ctrl-S (*fuzzy, prefix, full text*), Enter runs, Tab inserts for editing, Alt-1…9 picks.
ShellCraft can do a lighter version without a database:

- Ctrl-R opens a list under the prompt (the same place as the subshell panel), newest first, the
  match highlighted in each line, with the **directory, exit status and duration** the history
  already has (or starts recording) on the right.
- Ctrl-R again cycles *all / this directory / this session*; a fuzzy match by default.
- Enter runs, Tab or → puts it on the line, Esc cancels. Mouse hover and click as in the panel.

### 3.5 A command palette — **Medium, both**

Textual ships a command palette in every app; VS Code and Windows Terminal itself (Ctrl-Shift-P)
made it the standard way to find things. **F1 or Ctrl-P** opens one search box over:

- every builtin and module (summary from `help`), every setting (with its current value, Enter
  toggles), every theme (Enter previews), the subshells (Enter switches), the man pages, and the
  key bindings.

It replaces three habits (scrolling `help`, guessing `settings` names, remembering `Ctrl-]`
keys) with one, and it is the best way to make new features findable.

### 3.6 Show the keys while a prefix waits — **Quick, both**

Helix and Emacs' which-key show a small box of what can follow a prefix key, built from the key
map itself. After **Ctrl-]**, ShellCraft waits 0.6 s for a digit with nothing on screen. Instead,
draw a one-line hint in the footer (or under the prompt) at once:

`Ctrl-] then:  1-9 switch  ·  0 back to the prompt  ·  x end this one  ·  Ctrl-] send Ctrl-]`

Generated from the same table the attach loop uses, so it can't drift.

### 3.7 Highlight mistakes as you type — **Quick, both**

fish marks errors red as you type: unknown commands, mismatched parentheses, bad redirects. ShellCraft
already colors unknown commands red. Add:

- an unclosed quote, `{` or `(` (the continuation prompt says it after Enter; say it before),
- a redirect to a folder that doesn't exist, `cd` to one that doesn't exist (one `stat`, cached),
- a switch the command doesn't have (the completer already knows them all),

underlined rather than red, so color-blind users and the ascii tier see it too.

### 3.8 Alerts as toasts — **Medium, both**

Textual's `notify()` gives every app three severities, a timeout and one consistent place. ShellCraft's
alerts already have the severities and timeouts. Two refinements:

- **Fade:** an alert's last 3 seconds in the muted color before it goes, so it doesn't vanish
  mid-read; and `Esc` on an empty line dismisses the newest one.
- **Route everything through it:** "job finished", "background script gave up", "config saved",
  theme warnings and hot-reload notices are printed as loose lines today; as info alerts they stop
  pushing the prompt down and are collected in one place (`alert --list`).

### 3.9 Errors that fit — **Quick, both**

The error panel (a bordered Rich panel) is good for real failures but heavy for small ones: a
mistyped command takes five lines. A compact form for one-line errors —
`✗ grpe: command not found · did you mean grep?` in the error color — with the panel kept for
multi-line messages and tracebacks. A `compact_errors` setting, on by default.

### 3.10 Colors that respect the user — **Quick to Medium, both**

- **Light or dark terminal:** ShellCraft ships light themes but never picks one. Ask the terminal
  for its background color (OSC 11) at startup and default to a light theme on a light background
  when no theme was chosen. (Windows Terminal's answer to OSC 11 should be confirmed; the
  classic console needs the Windows theme setting instead.)
- **`NO_COLOR`** turns off theme colors (prompt_toolkit already reads it; Rich's console and the
  panels should follow), and **`settings motion off`** stops spinners and fades for screen-reader
  users and slow remote sessions. Windows Narrator reads Windows Terminal through UI Automation, so
  a spinner redrawing every 80 ms is noise there.
- **Contrast check** for custom themes: warn in the banner when a theme's text on surface is below
  WCAG AA (4.5:1), as the theme validator already warns about invalid colors.

### 3.11 Pager and output — **Quick each, both**

- `F` follows the output (the pager equivalent of `tail -f`, for `jobs -f` and long logs), `#`
  toggles line numbers, the status bar shows `match 3/17` while searching.
- Tables (`ls -l`, `jobs`, `modules`, `settings`) zebra-striped with the theme's surface color on
  wide terminals; numbers right-aligned; long columns ellipsized in the middle (`/home/…/logs`)
  instead of wrapped.
- `help` grouped with the same section headers in the narrow layout too.

### 3.12 The prompt — **Quick, both**

- A **one-line prompt** option (`prompt layout single`) for small windows and split panes; the
  two-line frame costs a row per command.
- The right prompt (clock, duration) hidden automatically when the line gets long, as fish and
  zsh do, instead of overlapping what is typed.
- The `[✗ 127]` status replaced by a colored `❯` (green / red) in the compact layouts, the
  convention starship and fish users expect.

### 3.13 Startup — **Quick, both**

- The banner's height adapts: the full banner when the window is 30+ rows, a one-line
  `⚡ ShellCraft 0.3.0 · 7 modules · theme nord` otherwise, nothing with `--no-banner`.
- Startup scripts and the rc file print one summary line (`⚙ 3 startup scripts, rc: 6 commands`)
  instead of a line each, with details in `startup`; their own output still shows.

### 3.14 Images in the pager and `cat` — **Large, WT 1.22+ and Sixel terminals**

Windows Terminal 1.22 and its conhost support Sixel images, as do foot, WezTerm, Konsole and
xterm. `cat photo.png` and `man` pages with images could show them inline (through `chafa`, or a
small Python encoder) when the terminal answers the Sixel query, and a text placeholder
otherwise. Nice, not necessary: last on the list.

---

## 4. Windows 11 checklist for every UI change

1. **Both consoles:** test in Windows Terminal and in `conhost.exe`. The classic console has no
   font fallback and no VT mouse, and counts emoji per code point.
2. **Width:** never place anything right after a glyph whose width prompt_toolkit and the terminal
   might disagree on (emoji, `⚡`, `⏱`, flags). Use the glyph tier.
3. **Last column:** the Windows console wraps the moment the last column is written. Anything
   drawn full-width (footer, panel, banner) stops one column short, as the prompt already does.
4. **Fonts:** Cascadia Mono (Windows Terminal's default) and Consolas (conhost's) are the targets;
   don't depend on Nerd Font icons.
5. **Sequences:** OSC and DEC private modes are safe everywhere; check Windows Terminal's version
   only for features where a fallback matters (Sixel, synchronized output).
6. **Keys:** Alt-N and Ctrl-Shift combinations belong to Windows Terminal (tabs, panes); prefer
   F-keys, Ctrl-letter and the `Ctrl-]` prefix.

---

## 5. Suggested order

| Step | What | Effort | Where it shows |
| --- | --- | --- | --- |
| 1 | Capability tiers and the glyph table (3.1) | Medium | conhost stops showing boxes; prompt aligned everywhere |
| 2 | Title, current directory, prompt marks, progress (3.2) | Quick ×4 | Windows Terminal tabs, taskbar, scrollbar, duplicate tab |
| 3 | Synchronized output around redraws (3.3) | Quick | no flicker in the pager and subshell footer |
| 4 | Ctrl-] key hint (3.6), compact errors (3.9), typing errors (3.7) | Quick ×3 | everyday typing |
| 5 | Command palette (3.5) | Medium | finding anything |
| 6 | atuin-style history search (3.4) | Medium | Ctrl-R |
| 7 | Alerts as toasts, routed messages (3.8) | Medium | less noise above the prompt |
| 8 | Colors: background detection, `NO_COLOR`, motion, contrast (3.10) | Quick–Medium | light terminals, accessibility |
| 9 | Pager, tables, prompt and startup polish (3.11–3.13) | Quick each | — |
| 10 | Sixel images (3.14) | Large | optional |

---

## Sources

- Microsoft: [Progress bar sequences in Windows Terminal (OSC 9;4)](https://learn.microsoft.com/en-us/windows/terminal/tutorials/progress-bar-sequences)
- Microsoft: [Shell integration in Windows Terminal (OSC 133)](https://learn.microsoft.com/en-us/windows/terminal/tutorials/shell-integration) · [devblog, May 2023](https://devblogs.microsoft.com/commandline/shell-integration-in-the-windows-terminal)
- Microsoft: [Opening a tab or pane in the same directory (OSC 9;9)](https://learn.microsoft.com/en-us/windows/terminal/tutorials/new-tab-same-directory)
- Microsoft: [Windows Terminal Preview 1.22: Sixel, grapheme clusters](https://devblogs.microsoft.com/commandline/windows-terminal-preview-1-22-release) · [release notes](https://github.com/Microsoft/console/releases/)
- Microsoft: [Windows Terminal Preview 1.25: synchronized output](https://devblogs.microsoft.com/commandline/windows-terminal-preview-1-25-release/)
- [Synchronized output specification (mode 2026)](https://github.com/contour-terminal/vt-extensions/blob/master/synchronized-output.md)
- [Windows Terminal width measurements (ucs-detect)](https://ucs-detect.readthedocs.io/_sources/sw_results/windowsterminal.rst.txt) · [conhost vs Windows Terminal cell widths (wtmux 4.0.0 notes)](https://newreleases.io/project/github/fukuyori/wtmux/release/4.0.0)
- [OSC 9;4 adoption across terminals (pv issue #202)](https://zff.dev/ivarch/pv/issues/202)
- atuin: [Basic usage](https://docs.atuin.sh/main/guide/basic-usage/) · [Advanced usage: filter and search modes](https://docs.atuin.sh/main/guide/advanced-usage/)
- fish: [Interactive use: autosuggestions and syntax highlighting](https://fishshell.com/docs/4.0/interactive.html)
- starship: [Presets (No Nerd Fonts, Plain Text Symbols)](https://starship.rs/presets/)
- Textual: [Toast / notify](https://textual.textualize.io/widgets/toast/) · [LWN overview](https://lwn.net/Articles/929123)
- helix: [key map info box source (`helix-view/src/info.rs`)](https://code.querie.cc/mirrors/helix/atom/helix-view/src/info.rs?h=25.07)
