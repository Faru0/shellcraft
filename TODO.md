# ShellCraft — TODO

> Assessment of **v0.1.0** (`main` @ 72a9897): core shell, MCP server, modules `fetch`, `filter`, `myip`. 42 tests pass.

## Assessment

**What's solid**
- The layering is clean. The parser, the pipeline executor and the UI are separate. `run_line()` is UI-agnostic and shared by the REPL, `-c` mode, the MCP server and the tests.
- The three-file module contract (`.py` / `.md` / `.skill`) is simple and works well. A broken module becomes a warning, not a crash.
- The MCP integration is safe by default: the pipeline tool can't redirect to files, can't use shell-state builtins, and can't run system commands.
- The UX feels good: themes, ghost text, delayed spinner, auto-pager and error panels that point at the failing segment.

**Biggest risks**
1. **Security of the MCP surface.** `fetch` can read *any* file the server user can read and request *any* URL. That matters as soon as an AI client is connected.
2. **Shell-grammar gaps fail silently.** `2>` is misparsed, and `<`, `;` and `&&` become plain arguments. Users expecting POSIX behavior can get wrong results with no error.
3. **The system-command fallback is capture-only.** Interactive programs misbehave, and "no match" exit codes abort the pipeline.
4. **Only tested on Linux.** None of the Windows paths have been run yet.

Items marked **(verified)** were reproduced during the assessment.

---

## 🐞 Bugs — P0

- [ ] **Invalid custom theme color crashes startup** *(verified)*: a bad hex such as `"prompt": "not-a-color"` in `config.json` raises `ValueError: Wrong color format` from `UI()`. Fix: validate in `all_themes()`, warn, and fall back to the base preset's color. — `core/themes.py`
- [ ] **`2> file` is silently misparsed** *(verified)*: `a 2> err.txt` becomes arg `"2"` plus a *stdout* redirect to `err.txt`, which overwrites the file with the wrong stream. `<`, `;` and `&&` also pass through as plain args. Fix: reject them with a clear `ParseError` until they are implemented (see Features). — `core/parser.py`
- [ ] **Nonzero exit from a system command always aborts the pipeline** *(verified; only when `system_commands` is on; the built-in `grep` already treats no match as empty output)*: `echo abc | grep zzz` shows a "failed" panel, but for grep a status of 1 just means no match. Fix: treat `returncode == 1` with empty stderr as an empty result, or add a configurable ok-codes policy. — `core/pipeline.py::_run_system`
- [ ] **A non-editable `pip install .` ships no modules**: `pyproject.toml` packages only `core`, and `DEFAULT_MODULES_DIR` points outside the package. Fix: include the bundled modules as package data, and also load the user directory `~/.shellcraft/modules`. — `pyproject.toml`, `core/cli.py`
- [ ] **Pager search `n` gets stuck on the last screen**: when a match is inside the final page, `top` is clamped to `max_top`, so the next `n` finds the same line again. Matches are also not highlighted. Fix: track `match_index` separately from `top` and highlight the match. — `core/output.py::page`
- [ ] **Interactive system programs misbehave** (`vim`, `top`, `ssh`, `python`), *only when `system_commands` is on*: their stdin and stdout are pipes. Fix: when a system command is the only segment and has no redirect, run it attached to the terminal (no capture, no spinner). — `core/pipeline.py`

## 🔒 Security — P0

- [ ] **MCP `fetch` reads arbitrary host files** *(verified: `/etc/hostname`)* **and arbitrary URLs (SSRF)**. The read-only builtins (`cat`, `grep`, `head`, `tail`, `ls`, `find`, `tree`, `cut`) in `shellcraft_pipeline` can read any file too, so the `--root` sandbox must cover them as well. Add:
  - `--root DIR`, which confines file reads for MCP
  - blocking of loopback, private and link-local targets, with an optional URL allowlist
  - a max response size

  — `modules/fetch.py`, `core/mcp_server.py`
- [ ] **Per-module MCP exposure switch**: `expose = false` in `.skill`, and a `--expose name,...` CLI flag, so sensitive modules stay local-only. — `core/loader.py`, `core/mcp_server.py`
- [ ] **No timeout on MCP tool calls**: a hung module holds a worker thread forever. Add a per-call timeout (`anyio.fail_after`) and caps on stdin and output size. — `core/mcp_server.py`
- [ ] **History records everything**, including secrets typed inline. Support the "leading space = not saved" convention. — `core/shell.py`

## 🔧 Bugs / polish — P1

- [ ] **Ctrl-C abandons the module worker thread**, which keeps running. Add a cooperative cancel flag in `modkit` that modules can check, or at least document the behavior. — `core/output.py::make_spinner_runner`
- [ ] **`to_text()` flattens at a fixed width of 100**, so `help | filter` ignores the real terminal width. Pass the console width when there is one. — `core/context.py`
- [ ] **`show()` renders twice** (a capture pass, then a print pass) and loads huge outputs fully into Rich. Reuse the captured ANSI, and cap or stream very large outputs into the pager. — `core/output.py`
- [ ] **System command stderr is dropped on success**, so warnings are lost. Print it dimmed. — `core/pipeline.py`
- [ ] **Captured system output loses colors**: set `FORCE_COLOR` / `CLICOLOR_FORCE` when the output will be displayed and not redirected. — `core/pipeline.py`
- [ ] **Windows: the prompt's `~` shortening is case-sensitive.** Use `os.path.normcase`. — `core/shell.py`
- [ ] **`cd` inside a pipeline changes the real cwd** (`cd x | pwd`), unlike POSIX subshells. Disallow it outside a single-segment line, or document it. — `core/builtins.py`
- [ ] **Loader hygiene**: stale `sys.modules["shellcraft_modules.*"]` entries survive `reload`, and there is no parent package, so relative imports inside modules fail. — `core/loader.py`
- [ ] **Completer ignores quotes**: paths containing spaces complete wrongly. — `core/completer.py`
- [ ] **Dead code**: `ShellContext.interactive` is set but never read. — `core/context.py`
- [ ] **Version is defined twice** (`pyproject.toml` and `core/__init__.py`). Use a single dynamic version. — `pyproject.toml`

## ⬆️ Upgrades — P1

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
  - a clear message for private-range IPs
- [ ] **Themes**:
  - Dracula, Gruvbox, Catppuccin and Tokyo Night presets
  - `theme preview NAME` (no save)
  - light-terminal variants
  - theme validation warnings shown in the banner
- [ ] **Exit status**:
  - a `status` builtin or `$?`
  - show the code in the prompt as `[✗ 1]`
  - make `-c` return the failing system command's real exit code

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
- [ ] **More modules**: `json` (pretty-print/query), `hash`, `dns`, `weather`.

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
- [ ] **Docs**: a LICENSE, a README screenshot or asciinema recording, a CHANGELOG, and a `.skill` authoring guide with good and bad examples.
- [ ] **Windows verification pass**: covering prompt rendering, the pager, `cmd` builtins, paths and the MCP stdio server.

---

**Legend:** **P0** = fix soon (correctness / security) · **P1** = next iteration · **P2** = nice to have
