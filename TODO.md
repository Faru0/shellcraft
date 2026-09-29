# ShellCraft — TODO

> First assessment: v0.1.0 (`main` @ 72a9897), when there were 42 tests. Checked items have been done since then.
> **Current state:** 32 portable builtins, a settings system (OS commands off by default) with API keys for modules (`ENV_SETTINGS`), modules `fetch`, `filter`, `myip`, `ip2geo`, `queryDns`, `queryCert` and `queryCensys`, the module authoring kit (`templates/`, `tools/modtest.py`, `tools/mkprompt.py`), and switch completion. 236 tests pass.

## Assessment

**What's solid**
- The layering is clean. The parser, the pipeline executor and the UI are separate. `run_line()` is UI-agnostic and shared by the REPL, `-c` mode, the MCP server and the tests.
- The three-file module contract (`.py` / `.md` / `.skill`) is simple and works well. A broken module becomes a warning, not a crash.
- The MCP integration is safe by default: the pipeline tool can't redirect to files, can't use shell-state builtins, and can't run system commands.
- The UX feels good: themes, ghost text, delayed spinner, auto-pager and error panels that point at the failing segment.

**Biggest risks**
1. **Security of the MCP surface.** `fetch` can request *any* URL, including loopback and private addresses. *(Reading any file the user can read is intended: see Won't do.)*
2. **Shell-grammar gaps fail silently.** `2>` is misparsed, and `<`, `;` and `&&` become plain arguments. Users expecting POSIX behavior can get wrong results with no error. *(Fixed: they're now rejected with a parse error.)*
3. **The system-command fallback is capture-only.** Interactive programs misbehave, and "no match" exit codes abort the pipeline. *(Now off by default, and the ported builtins cover the common commands. Accepted: see Won't do.)*
4. **Only tested on Linux.** None of the Windows paths have been run yet.

Items marked **(verified)** were reproduced during the assessment.

---

## 🐞 Bugs — P0

- [x] **Invalid custom theme color crashes startup**: `all_themes()` now checks each color against both Rich and prompt_toolkit, keeps the base preset's color, and the CLI prints a warning on stderr. — `core/themes.py`
- [x] **Unsupported operators were passed on as arguments**: `rm a ; ls` also deleted a file named `ls`, and `a 2> err.txt` sent stdout to `err.txt`. `;`, `&&`, `||`, `<`, `2>`, `2>>`, `2>&1`, `&>` and `>&` now raise a `ParseError` until they are implemented (see Features). — `core/parser.py`
- [ ] **A non-editable `pip install .` ships no modules**: `pyproject.toml` packages only `core`, and `DEFAULT_MODULES_DIR` points outside the package. Fix: include the bundled modules as package data, and also load the user directory `~/.shellcraft/modules`. — `pyproject.toml`, `core/cli.py`
- [x] **Pager search `n` got stuck on the last screen**: the last match is now tracked apart from the scroll position, so `n`/`N` step through every match, and matches are highlighted in reverse video. — `core/output.py::page`

## 🔒 Security — P0

- [ ] **MCP `fetch` requests arbitrary URLs (SSRF)**, including loopback, private and cloud-metadata addresses. Add:
  - blocking of loopback, private and link-local targets (re-checked on redirects), with an optional URL allowlist
  - a max response size

  — `modules/fetch.py`, `core/mcp_server.py`
- [ ] **Per-module MCP exposure switch**: `expose = false` in `.skill`, and a `--expose name,...` CLI flag, so sensitive modules stay local-only. — `core/loader.py`, `core/mcp_server.py`
- [ ] **No timeout on MCP tool calls**: a hung module holds a worker thread forever. Add a per-call timeout (`anyio.fail_after`) and caps on stdin and output size. — `core/mcp_server.py`
- [x] **History records everything**: a line typed with a leading space is no longer saved (bash's `ignorespace`). *(Values in `settings NAME VALUE` are still redacted.)* — `core/shell.py`
- [ ] **API keys are stored in plain text** in `config.json` (mode 600, which doesn't protect them on Windows). Offer the OS keyring (`keyring` package) as an optional backend. — `core/settings.py`
- [ ] **Network modules spend the user's quota through MCP**: `queryDns` and `queryCensys` use the stored keys whenever an AI calls them. Add a per-module MCP opt-out (see *Per-module MCP exposure switch*) and mention it in the `.skill` notes. — `core/mcp_server.py`

## 🔧 Bugs / polish — P1

- [ ] **Ctrl-C abandons the module worker thread**, which keeps running. Add a cooperative cancel flag in `modkit` that modules can check, or at least document the behavior. — `core/output.py::make_spinner_runner`
- [ ] **`to_text()` flattens at a fixed width of 100**, so `help | filter` ignores the real terminal width. Pass the console width when there is one. — `core/context.py`
- [ ] **`show()` renders twice** (a capture pass, then a print pass) and loads huge outputs fully into Rich. Reuse the captured ANSI, and cap or stream very large outputs into the pager. — `core/output.py`
- [x] **Windows: the prompt's `~` shortening was case-sensitive**: it now compares with `os.path.normcase`. — `core/shell.py`
- [x] **`cd` inside a pipeline changed the real cwd** (`cd x | pwd`): `cd` is now refused unless it's alone on the line (no pipe, no redirect). — `core/pipeline.py`
- [ ] **Loader hygiene**: stale `sys.modules["shellcraft_modules.*"]` entries survive `reload`, and there is no parent package, so relative imports inside modules fail. — `core/loader.py`
- [x] **Switch completion**: Tab completes each command's options and option values from its `.skill` `[[args]]` (including the new `values` key) and its `.md` options table. It is modular, with no per-module code.
- [ ] **Completer ignores quotes**: paths containing spaces complete wrongly. — `core/completer.py`
- [ ] **Positional values aren't completed**: `queryCensys <Tab>` offers file names instead of `host` / `cert` / `search`, although the `.skill` `[[args]]` entry `COMMAND` lists them in `values`. Complete the first positional from such an entry. — `core/options.py`, `core/completer.py`
- [x] **Dead code**: `ShellContext.interactive` is now read by `settings NAME` to choose the hidden prompt.
- [ ] **Version is defined twice** (`pyproject.toml` and `core/__init__.py`). Use a single dynamic version. — `pyproject.toml`

## ⬆️ Upgrades — P1

- [x] **API-key precedence**: at startup a variable already in the environment (the user's shell, an MCP client's `env` block) now wins over the stored key, and `settings` shows `from environment (stored ••••ab12 unused)`. `settings NAME VALUE` during a session still replaces it. — `core/settings.py`
- [ ] **`queryCensys`**: check the table layouts against a real token (only the error paths have been live-tested), and add `--at-time` for host history plus `web HOSTNAME:PORT` lookups (the SDK has `get_web_property`). — `modules/queryCensys.py`
- [ ] **`queryDns`**: the CNAME record shape is undocumented (the example is empty). Confirm it with a domain that has CNAMEs. Also offer the Plus-only `?map=1` domain map. — `modules/queryDns.py`
- [ ] **`queryCert`**: crt.sh often needs more than modtest's 10 s per call; add a per-test `timeout` key to `[[tests]]`. — `tools/modtest.py`
- [ ] **`ip2geo`**: use the ip-api.com batch endpoint (`POST /batch`, up to 100 IPs per request) for long lists. — `modules/ip2geo.py`

- [ ] **Streaming pipelines**: allow `run()` to return an iterator of lines so that large inputs don't sit in memory, while plain `str` returns keep working. — `core/pipeline.py`, `core/loader.py`
- [ ] **MCP improvements:**
  - [ ] Expose each `.md` man page as an MCP **resource**.
  - [ ] Add a streamable-HTTP transport option (`--mcp-http 127.0.0.1:8765`).
  - [ ] Send `tools/list_changed` when modules reload.
  - [ ] Let `.skill` declare a structured `input_schema` (named params) instead of the raw `args` array.
- [ ] **Hot reload**: watch `modules/` and reload on change, in both the shell and the MCP server.
- [ ] **`fetch`**: size limit, `--header`, `--method` / `--data`, `--retry`, and detection and rejection of binary content.
- [ ] **`myip`**:
  - a short-lived result cache (the API asks clients to cache)
  - parallel lookups when there are many IPs
  - comma-separated `--field` lists
  - a clear message for private-range IPs (`ip2geo` already does this)
- [ ] **Themes**:
  - Dracula, Gruvbox, Catppuccin and Tokyo Night presets
  - `theme preview NAME` (no save)
  - light-terminal variants
  - theme validation warnings shown in the banner
- [ ] **Exit status**:
  - a `status` builtin or `$?`
  - show the code in the prompt as `[✗ 1]`

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
- [ ] **More builtins**: `alias` / `unalias`, `history`, `source`, `sleep`, `time`, `type`, `basename` / `dirname`, `diff`, `du`.
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
- [ ] **`.gitlab-ci.yml`**: run pytest on Linux and Windows runners, Python 3.11–3.13.
- [ ] **Tooling**: ruff (lint and format) and mypy config in `pyproject.toml`, plus a pre-commit hook.
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

---

**Legend:** **P0** = fix soon (correctness / security) · **P1** = next iteration · **P2** = nice to have · **Won't do** = out of scope, recorded on purpose
