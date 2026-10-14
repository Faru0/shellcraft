# Developing ShellCraft

This guide is for working on ShellCraft itself. To *use* the shell, see [README.md](README.md). To
write a module, see [templates/README.md](templates/README.md).

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"                                 # editable install + pytest, ruff, mypy, build
pytest                                                  # full test suite
ruff check core tools tests main.py                     # lint (CI gates on it)
mypy core tools                                         # types (informational for now)
shellcraft ingest check --all --strict                  # check every bundled module (or: python tools/ingest check ...)
python -m build --wheel                                 # the packaging smoke test installs this into a clean venv
```

CI (`.gitlab-ci.yml`, mirrored in `.github/workflows/ci.yml`) runs the lint, the module checker,
pytest on Linux and Windows for Python 3.11–3.14, and the packaging smoke test. Releases are
listed in [CHANGELOG.md](CHANGELOG.md); the version lives in `shellcraft/__init__.py` only.

## Project layout

```
shellcraft/
├── main.py                 # launcher → shellcraft.cli.main
├── core/                   # 0.2 compatibility shim: `core.X` is `shellcraft.X` (removed in 0.4)
├── shellcraft/             # the package (was `core/` before 0.3.0)
│   ├── cli.py              # argument parsing; chooses interactive / --mcp / --mcp-http mode
│   ├── shell.py            # REPL: PromptSession, prompt, history, ghost text
│   ├── console.py          # the terminal: Windows CONIN$/CONOUT$ (Win32Input + VT output), the Rich console
│   ├── stdio.py            # is_console() (not isatty) and UTF-8 stdout/stderr for pipes and files
│   ├── parser.py           # quote-aware tokenizer ($name, $(( )), $( ), globs) → Pipeline(segments, redirects)
│   ├── arith.py            # $(( … )) integer arithmetic, evaluated from the AST (no eval)
│   ├── script.py           # scripts: bash for/while/until/if/case/&&/|| and -py blocks; piped blocks; conditions
│   ├── histexpand.py       # !!, !$, !*, !N, !prefix (interactive shell only)
│   ├── highlight.py        # syntax highlighting of the input line (a prompt_toolkit Lexer)
│   ├── gitinfo.py          # the git branch for the prompt, read from .git/HEAD
│   ├── jobs.py             # subshells (`sub`): programs on a pty / ConPTY with a pyte emulator each, attach / detach / switch, the alternate screen, commands in threads
│   ├── footer.py           # the subshell panel at the bottom while attached (scroll region), clickable, Ctrl-] N
│   ├── ask.py              # asking for a value: `name = input("…")` lines and the `read` builtin's prompt
│   ├── startup.py          # startup: ~/.shellcraft/startup/*.py (the $SHELLCRAFT_ENV_FILE hand-back), then ~/.shellcraft/rc via source()
│   ├── alerts.py           # status alerts: the bus, the spool folder it watches, the `alert` clients, the subshell shim
│   ├── aliases.py          # aliases: the ~/.shellcraft/aliases file (parsed, cached by mtime, edited line by line), validation, expansion
│   ├── pipeline.py         # executor: resolves commands, chains |, handles > / >>
│   ├── context.py          # ShellContext (state + permission flags), Styled, to_text()
│   ├── builtins.py         # builtin registry (@builtin) + shell builtins: cd, help, man, theme, settings…
│   ├── commands/           # portable ports registered as builtins
│   │   ├── text.py         #   echo cat grep head tail wc sort uniq cut tr tee
│   │   ├── files.py        #   ls find tree du touch mkdir cp mv rm
│   │   ├── diff.py         #   diff (classic + unified, directories)
│   │   ├── info.py         #   date which env
│   │   ├── misc.py         #   printf seq xargs basename dirname realpath stat whoami hostname uname sleep time type export unset
│   │   ├── cond.py         #   test [ [[ true false (exit status only)
│   │   └── _io.py          #   shared file/stdin helpers
│   ├── loader.py           # module discovery, enable/disable; ModuleSpec; SkillInfo (.skill parsing)
│   ├── params.py           # .skill [[params]]: validation, MCP input schema, named values → CLI args
│   ├── watch.py            # ModuleWatcher: polls the module folders (main + ~/.shellcraft/modules) for hot reload
│   ├── options.py          # switch metadata from .skill [[params]]/[[args]] and .md tables (Tab completion)
│   ├── completer.py        # Tab completion: commands, switches, values, paths
│   ├── output.py           # delayed spinner (with elapsed time), error panels, the pager
│   ├── settings.py         # on/off settings registry; API keys (ENV_SETTINGS) storage/export/masking
│   ├── themes.py           # theme presets → Rich theme + prompt_toolkit style
│   ├── config.py           # ~/.shellcraft/config.json (mode 600 once it holds keys) and history paths
│   ├── banner.py           # startup banner
│   ├── modkit.py           # helpers for module authors: ArgParser, ModuleError, EnvSetting, @script
│   ├── mcp_server.py       # MCP server (stdio or HTTP): tools, man-page resources, list_changed on reload
│   └── mcp_child.py        # `mcp start/stop`: the HTTP server as a child process of the shell
├── modules/                # bundled modules: fetch, filter, myip, ip2geo, queryDns, queryCert, queryCensys
├── templates/              # module guide + the reference module (example.py/.md/.skill)
├── tools/
│   ├── ingest/             # the module wizard and checker: the `ingest` builtin, `shellcraft ingest`, `python tools/ingest`
│   │   ├── __main__.py     # the wizard and the `check` sub-command
│   │   ├── analyze.py      # static analysis: what moves into run(), edits, conflicts, options
│   │   ├── convert.py      # line-based minimal rewrite, manual steps, the extra-change guard
│   │   ├── docs.py         # offline .md / .skill drafts
│   │   ├── ai.py           # Claude / OpenAI over requests; prompts/ holds the prompt texts
│   │   └── check.py        # the module checker
│   └── winconsole_probe.py # live key / console-mode / size check of the shell's console setup
├── tests/                  # pytest suite
├── .gitlab-ci.yml          # CI: lint, module checker, pytest (Linux + Windows, 3.11–3.14), packaging smoke test
└── .github/workflows/      # the same CI for the GitHub mirror
```

## How a command line runs

1. The REPL first joins continuation lines (while `script.incomplete()` says a `{` or `(` is open) and expands `!!` / `!$` (`histexpand.expand()`), then saves the result to the history itself (the prompt's own history append is switched off).
2. `run_line()` calls `script.parse_script()`. A single plain pipeline (the usual case) goes on exactly as before; anything else (`;`, `for`, `if`) is run by `script.execute()`, which runs each statement as in steps 3–5 with the loop variables, prints its output through `ctx.emit` (or collects it when there is none, as in MCP), reports a failing statement and goes on.
3. `parser.parse(line, variables, …)` splits the line into segments and redirections (`>`, `>>`, `<`, `2>`, `2>>`, `2>&1`, `&>`), expanding `$?`, `$name` (shell, loop and, in the interactive shell, environment variables; never split into several words on a plain line), `$(( … ))` (`shellcraft/arith.py`), `$( … )` and globs. Quotes are respected, and backslashes stay literal, which keeps Windows paths intact. A lone `&` and `>&` (and `;`, `&&`, `||`, which only `script.py` splits on) raise a `ParseError` instead of becoming arguments. `x=1` on its own sets a shell variable (`ctx.variables`).
4. `pipeline.run_pipeline()` first expands the user's aliases (`aliases.expand()`, unless `ctx.allow_aliases` is off, as in MCP). It then feeds each segment the previous segment's output as `stdin`. Each command name is resolved in this order:
   1. a **builtin** (`shellcraft/builtins.py`, `shellcraft/commands/`)
   2. a **module** from `modules/`
   3. an **OS program**, only when `ctx.allow_system` is set (the `system_commands` setting or `--allow-system`)
5. A `CommandError` / `ModuleError`, or any exception, stops the pipeline as a `PipelineError` that names the failing segment and carries its exit status (`CommandError(msg, status)`; 127 for not found). A command returns `WithStatus(value, n)` for a nonzero status that isn't an error (`grep` with no match). `ctx.last_status` (`$?`) is set from the result.
6. The final output goes to the redirect file, or to `output.show()`. `show()` renders Rich output and pages it when it's taller than the terminal.

### Terminal I/O on Windows and Linux

- **Interactive shell on Windows:** `Shell` calls `shellcraft.console.create_prompt_toolkit_console()`, which opens `CONIN$` and `CONOUT$` directly. Keys come from prompt_toolkit's `Win32Input`, bound to the `CONIN$` handle for reading, raw/cooked mode and the VT-input check (prompt_toolkit would otherwise use `GetStdHandle`). Output is `DirectOutput`, prompt_toolkit's `Windows10_Output` bound to `CONOUT$`: each flush writes with the screen buffer in exactly `PROCESSED_OUTPUT | VIRTUAL_TERMINAL_PROCESSING` (no wrap, no delayed wrap) and restores the mode; between prompts it rests in `PROCESSED | WRAP_AT_EOL | VT`. CPR is off and the rows below the cursor come from the screen buffer, so the completion menu gets its room. `Shell` then rebuilds Rich with `shellcraft.console.build_console(file=<CONOUT$ stream>)`. The `Win32Input` internals this relies on are checked up front and prompt_toolkit is pinned to 3.0.x; if the console can't be opened, the shell says why on stderr (and in `--diag`) and falls back to prompt_toolkit's own console handling. The pager, the hidden API-key prompt and Rich all use the console, however stdin and stdout are redirected; the process's std handles are never changed. Both console modes are restored on exit.
  - **Why not delayed wrap:** until 2026-10-09 `CONOUT$` was kept in `DISABLE_NEWLINE_AUTO_RETURN` with the full window width. In Windows Terminal (ConPTY) the shell then showed a garbled prompt, and Backspace and Tab completion seemed to do nothing. The likely cause is the cursor drifting from prompt_toolkit's picture of the screen (ConPTY has known delayed-wrap quirks); it is the one setting that every earlier attempt shared and that stock prompt_toolkit never uses, so it's gone. Confirm with the probe below.
  - **`SHELLCRAFT_CONSOLE`** compares setups on a machine where something is wrong: `direct` (the default in the classic console), `legacy-keys` (direct, with the classic key reader and no VT input: the default in Windows Terminal, where `WT_SESSION` is set) or `native` (prompt_toolkit's own console handling). The `PromptSession` is bound to `CONIN$` / `CONOUT$` explicitly as well as through the app session. `python tools/winconsole_probe.py` runs a shell-like prompt on the same setup and shows every key received, the console modes and the size, live.
- **Window size:** `shellcraft.console.build_console()` makes Rich ask the current prompt_toolkit output for the size, so Rich and the prompt always agree. On Windows that is prompt_toolkit's `Win32Output` size: the visible window of `CONOUT$` without its last column, so a full-width line never wraps early. `--diag` or the `diagnostics` setting prints every size source side by side.
- **Console detection:** use `shellcraft.stdio.is_console()`, never `isatty()`. On Windows `isatty()` is True for `NUL`.
- **Paging:** `show()` pages output taller than the window. A command can override that by returning `Paged(value, page=True/False)` (`shellcraft.context`): `man` always pages (`man -p` prints), `help` always prints. Pipes, files and MCP see only `value`.
- **Encoding:** `utf8_stdio()` makes stdout/stderr UTF-8 when they aren't already (Windows pipes and files default to cp1252), unless `PYTHONIOENCODING` is set. The MCP stdio transport wraps the binary streams as UTF-8 itself.
- **OS commands:** output is decoded as UTF-8 with `\r\n` → `\n`. `cmd.exe` internal commands run with `/u`, so their output arrives as UTF-16.

`run_line()` doesn't depend on the UI. The REPL, the MCP server and the tests all share it.

### Permission flags (`ShellContext`)

| Flag | Blocks | Off in MCP |
| --- | --- | --- |
| `allow_redirect` | `>` / `>>` | yes |
| `allow_stateful` | builtins marked `stateful` (`cd`, `theme`, `prompt`, `settings`, `exit`, `reload`, `clear`, `banner`, `alias`, `unalias`, `history`, `mcp`, `sub`, `jobs`, `ingest`, `time`, `export`, `unset`), and `modules enable` / `disable` | yes |
| `allow_writes` | builtins marked `writes` (`tee`, `mkdir`, `cp`, `mv`, `rm`, `touch`) | yes |
| `allow_sensitive` | builtins marked `sensitive` (`env`, `history`), and `$NAME` reading the environment | yes |
| `allow_aliases` | the user's aliases | yes |
| `allow_system` | the OS program fallback | unless `--allow-system` |
| `module_filter` | modules the context may run (MCP: those a `.skill` doesn't mark `expose = false`, narrowed by `--expose`) | set |

### Two-faced output (`Styled`)

A command can return `Styled(renderable, text)`: Rich output for the screen, and plain text for
pipes, files and MCP. `ls`, `tree` and `grep` use this. `to_text()` flattens any value (a `str`,
a `Styled` or a Rich renderable) into plain text.

## Adding a builtin

Builtins live in `shellcraft/commands/*.py` (or `shellcraft/builtins.py` for shell-level commands) and register
themselves with a decorator:

```python
@builtin("wc", "Count lines, words and characters", "wc [-l] [-w] [-c] [FILE...]",
         category="text",          # help grouping: shell | text | files | info
         writes=False,             # True if it changes files  → blocked in MCP
         sensitive=False,          # True if it may reveal secrets → blocked in MCP
         doc="""# wc ... | `-l` | Lines only. | ...""")   # the man page (Markdown)
def wc(ctx, args, stdin):
    ...
```

- Parse arguments with `shellcraft.modkit.ArgParser`, and raise `ModuleError` for user errors.
- The `doc` Markdown is the `man` page, **and** its options table (``| `-x`, `--long N` | … |``) is what Tab completion reads. Keep every switch in that table.
- New files in `shellcraft/commands/` must be imported in `shellcraft/commands/__init__.py`.
- Add tests to `tests/test_commands.py`.

### Scripts (`shellcraft/script.py`)

- **Two syntaxes.** Bash (default): `for NAME in WORD…; do …; done`, `while` / `until LIST; do …; done`, `if LIST; then …; [elif …; then …;] [else …;] fi`, `case WORD in PAT) …;; esac`. `-py`: `for -py NAME in ITEM… { … }`, `while -py COND { … }`, `if -py COND { … } [elif …] [else …]`. The parser tracks a mode ("top", "sh", "py"); a `-py` block's nested `for`/`if` inherit "py". `parse_list(end)` reads statements up to the end of the text, a `}`, `;;` or a set of bash keywords, and returns which one ended it. `a && b || c` is a `Chain`; `! cmd` sets `Simple.negate`. A block followed by `| …` or a redirection becomes a `Piped` statement: the block runs with its output collected, which then feeds the tail pipeline (`parse(..., allow_leading_pipe=True)`).
- **Pipelines are kept as raw text** and syntax-checked with `parser.parse()` before anything runs (including the commands inside `$( … )`), so a typo in the tenth statement stops the script before the first one runs. Each `Simple` carries its mode: "top" statements (a plain line, or `a ; b` outside any block) parse exactly as before; "sh" ones get `parse(split=True, substitute=runner.substitute)`, i.e. bash word splitting and `$( … )` (not inside `[[ ]]`); "py" ones never split.
- **Bash `for` lists** go through `expand_sh_word()`: `brace_expand()`, then `$x` / `$( … )` (split when unquoted), quote removal, globbing of unquoted `*?[`. Undefined variables stay as typed. Variables resolve through `pipeline.Variables`: `$?`, loop variables, `ctx.variables` (`x=1`, which the top-level `_Runner` writes straight to), then the environment only when `ctx.allow_sensitive` (so MCP can't print secrets with `$NAME`).
- **`-py` conditions:** `( … )` is Python, vetted by `_vet()` (an allow-list of AST nodes, functions in `FUNCTIONS` and methods in `STR_METHODS`) and evaluated by `_eval()`, a tree walker: never `eval`/`compile`. `*` is capped (`MAX_SEQUENCE`) and `**` isn't allowed. Anything else is a pipeline, true when its status is 0.
- **Errors:** a failing statement is reported (`ctx.emit_error`, or a `✗ name: message` line) and the script goes on; a `-py` condition that can't be evaluated stops the script (`_Abort`). `IncompleteError` (a `ParseError`) tells the REPL to read another line; `join_continuation()` decides whether the lines need a `;` between them.
- **Permissions:** every statement, and every `$( … )`, runs through `run_pipeline()` with the same `ShellContext`, so MCP restrictions hold inside loops.
- **Functions:** `name() { … }` / `function name { … }` parse into a `Function` statement (`_Parser._function`; the body is "sh" mode, `return` is a `Jump` allowed only inside one). Running the definition stores it in `ctx.functions`. `pipeline._run_command` checks `ctx.functions` before builtins (except `SPECIAL_BUILTINS`, and not when `Command.bypass` is set by `\name`) and calls `script.call_function()`: a `_Runner` that collects the output, reads the call's stdin (`_Runner.stdin`), pushes the arguments on `ctx.positional` (read by `Variables` for `$1`/`$#`/`$@` and by `arith` for `$1` in `$(( ))`), and keeps a `frame` of the values `local` replaced, restored in `finally`. Depth is capped by `MAX_FUNCTION_DEPTH`. Dynamic scoping: non-local variables are `ctx.variables`.
- **`[[ … ]]` with `&&` / `||`:** `_Parser._simple` counts `[[` / `]]` words and doesn't end a statement at an `&&` / `||` inside them; `parser.tokenize` passes `&&` / `||` through as plain words when the first word is `[[`; `cond._Evaluator` (extended mode) treats them as and / or with `&&` binding tighter.
- **`>&2`:** the parser sets `Command.to_stderr` for `>&2` / `1>&2` (also on the empty leading segment of a block's tail, `done >&2`). After the segment runs, `pipeline._to_stderr` sends its output to its own `2> file`, keeps it with `2>&1`, or calls `pipeline.write_stderr(ctx, text)`: the innermost `ctx.stderr_sinks` entry, else `ctx.emit_stderr` (the REPL prints it in `sc.error`), else `sys.stderr`. `script.call_function` and `_Runner.run_piped` push a sink for a call's or block's `2> file` (`pipeline.stderr_file_sink`) or `2>&1` (the collected output), and `_Runner.report` sends the body's errors to the same sink.
- **`select`:** parsed like a bash `for` (`_Parser._select` reuses `_for_sh`) into a `Select`; `_Runner.run_select` prints the menu on the console directly (not into the collected output, like bash's stderr), asks through `core.ask.ask()` and loops until `_Break`, Ctrl-D or Ctrl-C.

## Adding a setting

Add a `Setting(...)` to `SETTINGS` in `shellcraft/settings.py`. Read it with `settings.get(ctx.config, "key")`.
If the setting has to change the running session immediately, give it an `apply` callback. The
`settings` builtin, its completion and its man page pick up new settings automatically.

### API keys (`ENV_SETTINGS`)

Modules declare the environment variables they need as `ENV_SETTINGS = [EnvSetting(NAME, label,
description)]` (`shellcraft/modkit.py`). The pieces:

| Where | What |
| --- | --- |
| `shellcraft/loader.py` | `_env_settings()` validates the declaration (UPPER_CASE names, non-empty text, no duplicates). A bad one makes the module fail to load with a warning. |
| `shellcraft/settings.py` | `env_settings(registry)` collects them, labelled `module · label`. `env_set` / `env_reset` / `export_env` keep `config["env"]` and `os.environ` in sync. `_ORIGINAL_ENV` remembers the shell's own values, so a reset can restore them. `mask()` and `env_status()` produce the display text, and `redact_line()` hides values from history. |
| `shellcraft/cli.py` | Calls `export_env()` right after loading the config, before choosing the mode, so the REPL and `--mcp` both see the keys. |
| `shellcraft/builtins.py` | The `settings` table rows, the hidden prompt (`prompt_toolkit.prompt(is_password=True)`, or `getpass` outside the REPL), set and reset. |
| `shellcraft/shell.py` | `RedactingFileHistory` stores `settings NAME ••••`. |
| `shellcraft/config.py` | `save_config()` writes with mode 600 while `"env"` is non-empty. |
| `tools/ingest/check.py` | `check_env_settings()` checks that every variable is named in the `.md` and the `.skill`. |

`settings` is `stateful`, so MCP clients can never read or change keys through it.

## MCP server notes

- `ShellcraftMCP` (`shellcraft/mcp_server.py`) owns the lowlevel `Server`. `build_server()` returns just the `Server` for tests and simple embedding.
- **Tools:** a module with `.skill` `[[params]]` gets `params.input_schema()`, and a call's named values go through `ModuleSpec.argv()` (`params.to_argv()`) before `run(args, stdin)`. Other modules keep `MODULE_INPUT_SCHEMA` (a raw `args` array).
- **Change notifications reach two kinds of clients:**
  - 2026-07-28+ clients open `subscriptions/listen`. `notify_changed()` publishes `ToolsListChanged` / `ResourcesListChanged` on the `InMemorySubscriptionBus`.
  - Older clients get `notifications/*/list_changed` on their `Connection`. The server remembers each connection (weakly) from its requests. mcp 2.x hands handlers only a per-request `ServerSession`, so the connection is read from its private `_connection`. Recheck this when upgrading `mcp` past 2.x; `test_reload_notifies_legacy_clients` catches a break.
- `_Server.create_initialization_options()` always advertises `listChanged`, because the streamable-HTTP manager builds its own init options.
- **Hot reload** is a 1-second poll (`ModuleWatcher.changes()`). It's cheap for a few dozen files and needs no dependency. The shell polls before each command instead. `ModuleRegistry.reload(changed)` reloads only the changed modules and re-reads a `.md` without re-importing.
- **Lazy loading:** with `ModuleRegistry(lazy=True)` (the `lazy_modules` setting, on by default) `load()` reads only the `.skill` / `.md` files and a `ModuleSpec.loader` imports the `.py` on first use (`spec.load()`); `settings.env_settings()` loads every module so API keys are listed before first use. Tests build registries eagerly.
- **Limits and exposure:** every tool call runs under `anyio.fail_after(CALL_TIMEOUT)` (`SHELLCRAFT_MCP_TIMEOUT`); stdin and output are capped (`MAX_STDIN`, `MAX_OUTPUT`). A `.skill` with `expose = false` keeps a module out of the tool list, the man-page resources and `shellcraft_pipeline` (`ctx.module_filter`); `--expose NAME,...` narrows further.
- **`--mcp-http` authentication:** `require_bearer()` wraps the ASGI app; every request must send `Authorization: Bearer <token>` (`--token`, `$SHELLCRAFT_MCP_TOKEN`, or a generated token logged at startup; `--no-token` turns it off). `mcp start` generates one per server, prints it, and passes `--parent-pid` so the child exits when the shell dies (`mcp_child.watch_parent()`).
- **`--mcp-http`** runs uvicorn (an mcp dependency) in the same anyio task group as the watcher. `parse_http_address()` only accepts loopback addresses, and DNS-rebinding protection is always configured.
- **`mcp start`** (`shellcraft/mcp_child.py`) runs `python -m shellcraft.cli --mcp-http` as a child process in its own process group (a new session on POSIX, `CREATE_NEW_PROCESS_GROUP` on Windows), so Ctrl-C at the prompt doesn't reach it. Its output goes to `~/.shellcraft/mcp-http.log`. `start()` hands the process to the shell as soon as it is spawned (`ctx.mcp_http`), before waiting for the port, so an interrupted start is still tracked; `Shell.close()` stops it.

## Two copies: GitLab and GitHub

ShellCraft lives in two repositories with the same code but separate histories (so the commit IDs
differ):

| Copy | Where | What is different |
| --- | --- | --- |
| GitLab (private) | `~/Documents/local/shellcraft` | **where commits are made** |
| GitHub (public mirror) | `~/Downloads/togithub/shellcraft` | also carries `Install/` (the offline bundle) and the `findtime` module |

A change is committed in the GitLab copy first, then carried over as a patch and pushed:

```bash
cd ~/Documents/local/shellcraft && git push origin main
git format-patch -1 -o /tmp/sc-patch                    # -N for the last N commits
cd ~/Downloads/togithub/shellcraft && git am /tmp/sc-patch/*.patch && pytest -q && git push origin main
```

Never commit the same change by hand in both copies; a commit made only in the GitHub copy
(`Install/`, `findtime`) stays there. To compare the two trees:
`diff <(git -C A ls-tree -r HEAD --name-only) <(git -C B ls-tree -r HEAD --name-only)`.
`Install/` is meant to leave git for a GitHub Release asset (see TODO.md).

## Tests

| File | Covers |
| --- | --- |
| `test_parser.py` | tokenizer, quoting, redirects, parse errors |
| `test_script.py` | `$?`/`$name` expansion, exit statuses, `;`, `-py` loops (words, ranges, globs, command output), `if -py` with commands and Python conditions, the condition sandbox, break/continue, syntax errors, continuation detection |
| `test_bash_script.py` | bash-syntax `for`/`if`, word splitting, `$( … )`, brace expansion, `&&`/`||`/`!`, `test`/`[`/`[[`, syntax hints, continuation joining |
| `test_histexpand.py` | `!!` / `!$` rules, and the REPL loop: printing, saving the expanded line, joined continuation lines, `[✗ N]` |
| `test_modules_cmd.py` | `modules`, `-a`, `enable`/`disable`: never importing disabled modules, saving to config, startup, MCP tool list, `man`/`which`, completion |
| `test_pipeline.py` | chaining, failures, redirection, `cd`, restricted contexts |
| `test_commands.py` | every ported builtin |
| `test_settings.py` | settings, the OS-command gate, `--allow-system`, no `-c` mode |
| `test_env_settings.py` | API keys: listing, masking, prompt, set/reset, export at startup, history redaction, completion, loader validation |
| `test_options.py` | switch parsing and Tab completion |
| `test_loader.py` | module discovery, `.skill` parsing |
| `test_aliases.py` | alias/unalias: expansion, appended args, chains without loops, `\` bypass, persistence, which, completion, off for MCP |
| `test_diff_du_history.py` | `diff` formats and flags (compared with GNU diff when installed), `du` sizes/depth/sorting (compared with GNU du), `history` |
| `test_themes.py` | presets and custom themes: every preset color is valid; invalid custom colors fall back to the base preset |
| `test_shell.py` | the prompt's `~` shortening (POSIX and Windows), pager search stepping and match highlighting, hot reload in the shell, the console session wiring and its restore order, the diagnostics report, `help` printing and `man` paging |
| `test_robustness.py` | the fourth assessment's crashes: `main.py --version`, the screen clear, `~nosuchuser`, a quoted `~`, missing redirect directories, `&`, `cp -r` into itself, `grep -m 0`, `sort -u -k`, `wc -c/-m`, a deleted cwd, usage lines in switch errors, `help NAME` / `-k`, config validation, merging and atomic writes, history redaction, the loop's last-resort catch |
| `test_scripting_more.py` | shell variables, `export` / `unset`, `$(( ))`, `while` / `until` / `case`, piped and redirected blocks, `<` / `2>` / `2>&1` / `&>`, globs on plain lines, `!N` / `!prefix` / `!*` |
| `test_commands_more.py` | the new switches (`grep -r/-A/-B/-C/-w/-x/-o`, `ls -t/-S/-R/-d`, `find -size/-mtime/-newer/-empty/-not`, `sort -h/-V/-s`, `cat -s/-A`, `head/tail -c`, `tail -f`, `tree -I/--du`) and builtins (`printf`, `seq`, `xargs`, `basename`, `dirname`, `realpath`, `stat`, `whoami`, `hostname`, `uname`, `sleep`, `time`, `type`) |
| `test_mcp_boundary.py` | `expose = false`, `--expose`, the per-call timeout, stdin/output caps, the bearer token middleware, lazy module loading |
| `test_tui.py` | the input-line lexer, the toolbar, prompt segments, the pager's key handling (pipe input), `show()`'s paging threshold, theme persistence and preview, the banner's narrow fallback, the completer (quotes, positionals) |
| `test_jobs.py` | subshells: a program job on a pty (output, status, notifications), attach / detach / switch through a fake terminal, command jobs, `sub` / `jobs` (background commands, refusals, exit status), nested ShellCraft argv, the footer (tabs, spans, hover, clicks, Ctrl-] N, margin clamping), the completion menu following the mouse |
| `test_cancel.py` | cooperative cancellation: the modkit API, a real Ctrl-C during the spinner wait reaching the module, an MCP timeout cancelling the abandoned thread |
| `test_keyring.py` | API keys in the OS keyring (fake backend): set / get / export / reset, the setting off, an unreachable keyring, `settings migrate-keys`, a refusing backend |
| `test_core_shim.py` | the `core` compatibility shim: same module objects, old modules load, `python -m core.cli` / `core.alerts` run |
| `test_user_modules.py` | the user modules folder: shadowing, origin, helpers across folders, hot reload of both, `ingest` writing there, the CLI wiring |
| `test_ask.py` | `name = input("…")` parsing and variables, `read` (prompt, hidden), refusal without a terminal and over MCP, asking from an rc file |
| `test_startup.py` | startup scripts: order, `_helpers`, variables handed back (and unset), failures and timeouts changing nothing, the `startup` builtin, `--no-startup` and the setting |
| `test_alerts.py` | alerts: severity ordering, the cap, expiry and dismissal, the spool folder (delivery, sender labels, junk and stale files), the command-line client and the subshell shim, the `alert` builtin (local, forwarded from a nested shell), alert lines in the panel with their source, the `send_tui_alert` MCP tool |
| `test_console.py` | `shellcraft.console`: `SHELLCRAFT_CONSOLE`, the Win32Output size, rows below the cursor, the flush mode, `Win32Input` bound to `CONIN$` (fake win32 module), the Rich console's size and options, the truecolor prompt on Linux |
| `test_stdio.py` | console detection, UTF-8 stdout/stderr on non-UTF-8 pipes, `cmd.exe` builtins read as UTF-16 |
| `test_mcp_child.py` | `mcp start/stop/restart/status/log` against a real child server: the HTTP endpoint, process group, port in use, bad addresses, a server that dies, stopping on shell exit |
| `test_mcp.py` | the MCP server, run in-process: tools, typed params, man-page resources, list_changed notifications (legacy and `subscriptions/listen`), the module watcher, HTTP address checks |
| `test_fetch.py` | `fetch`: headers, methods, request bodies, retries, size limit, binary rejection (HTTP stubbed) |
| `test_myip.py`, `test_ip2geo.py`, `test_querydns.py`, `test_querycert.py`, `test_querycensys.py` | the network modules, with the APIs stubbed (offline): the `http` fixture fakes `urlopen`, and `queryCensys` gets a fake SDK client |
| `test_ingest.py` | the ingest wizard: analysis, conversion, docs drafts, checker, AI (mocked), `@script` |

Tests that need a real OS program use `tests.conftest.OS_UPPER`, which runs the current Python
interpreter, so they work on every platform. Tests that touch API keys use the `clean_env` fixture,
which removes the keys from the environment for the test and restores them afterwards.

For the `queryCensys` module and its tests against the real SDK types, install the extra:
`pip install -e ".[dev,censys]"`. Without it, those few checks are skipped.
