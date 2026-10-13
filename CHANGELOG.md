# Changelog

All notable changes to ShellCraft. The format follows [Keep a Changelog](https://keepachangelog.com/),
and the project uses [semantic versioning](https://semver.org/).

## [Unreleased]

### Added

- **Subshells:** `sub` opens your own shell, `sub PROGRAM` hands the terminal to any program
  (`vim`, `htop`, `ssh`, `python`…), `sub shellcraft` nests a ShellCraft (its prompt shows the
  depth), and `sub COMMAND` runs a builtin or module in the background with its output collected.
  Each is a numbered job. At the prompt a **panel** under it lists them one per line (the line
  under the mouse lights up, a click switches, ↓ moves into the panel and Enter picks); inside a
  subshell a one-row **footer** at the bottom of the terminal shows the same shells as tabs (a
  scroll region keeps it in place), clickable too. **Ctrl-] N** switches from anywhere, **Ctrl-]**
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

### Changed

- `clear_on_start` clears the scrollback as well (`ESC[3J`), so the shell starts on a fresh
  screen like a new program, instead of leaving the old lines above.
- The default `--mcp-http` / `mcp start` address is `127.0.0.1:38765` (was `:8765`), to stay
  clear of the ports other local tools commonly take.

### Fixed

- `help` printed `Paged(value=...)` instead of the command table.

## [0.2.0] — 2026-10-12

The fourth assessment ([UPGRADE-ASSESSMENT.md](UPGRADE-ASSESSMENT.md)) in one release: every crash
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
