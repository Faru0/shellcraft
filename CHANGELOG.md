# Changelog

All notable changes to ShellCraft. The format follows [Keep a Changelog](https://keepachangelog.com/),
and the project uses [semantic versioning](https://semver.org/).

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

### Added (shell grammar)

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
