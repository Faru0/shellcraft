# Developing ShellCraft

This guide is for working on ShellCraft itself. To *use* the shell, see [README.md](README.md). To
write a module, see [templates/README.md](templates/README.md).

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"                                 # editable install + pytest
pytest                                                  # full test suite
python tools/modtest.py --all                           # check every bundled module
```

## Project layout

```
shellcraft/
├── main.py                 # launcher → core.cli.main
├── core/
│   ├── cli.py              # argument parsing; chooses interactive / --mcp / --mcp-http mode
│   ├── shell.py            # REPL: PromptSession, prompt, history, ghost text; Windows console (CONIN$/CONOUT$)
│   ├── stdio.py            # is_console() (not isatty) and UTF-8 stdout/stderr for pipes and files
│   ├── parser.py           # quote-aware tokenizer → Pipeline(segments, redirect)
│   ├── aliases.py          # alias storage, validation and expansion (run_pipeline calls expand())
│   ├── pipeline.py         # executor: resolves commands, chains |, handles > / >>
│   ├── context.py          # ShellContext (state + permission flags), Styled, to_text()
│   ├── builtins.py         # builtin registry (@builtin) + shell builtins: cd, help, man, theme, settings…
│   ├── commands/           # portable ports registered as builtins
│   │   ├── text.py         #   echo cat grep head tail wc sort uniq cut tr tee
│   │   ├── files.py        #   ls find tree du touch mkdir cp mv rm
│   │   ├── diff.py         #   diff (classic + unified, directories)
│   │   ├── info.py         #   date which env
│   │   └── _io.py          #   shared file/stdin helpers
│   ├── loader.py           # module discovery; ModuleSpec; SkillInfo (.skill parsing)
│   ├── params.py           # .skill [[params]]: validation, MCP input schema, named values → CLI args
│   ├── watch.py            # ModuleWatcher: polls the modules folder for hot reload
│   ├── options.py          # switch metadata from .skill [[params]]/[[args]] and .md tables (Tab completion)
│   ├── completer.py        # Tab completion: commands, switches, values, paths
│   ├── output.py           # delayed spinner, error panels, auto-pager
│   ├── settings.py         # on/off settings registry; API keys (ENV_SETTINGS) storage/export/masking
│   ├── themes.py           # theme presets → Rich theme + prompt_toolkit style
│   ├── config.py           # ~/.shellcraft/config.json (mode 600 once it holds keys) and history paths
│   ├── banner.py           # startup banner
│   ├── modkit.py           # helpers for module authors: ArgParser, ModuleError, EnvSetting
│   └── mcp_server.py       # MCP server (stdio or HTTP): tools, man-page resources, list_changed on reload
├── modules/                # bundled modules: fetch, filter, myip, ip2geo, queryDns, queryCert, queryCensys
├── templates/              # module authoring kit: guide, reference module, AI prompt
├── tools/
│   ├── modtest.py          # module tester
│   └── mkprompt.py         # builds the AI prompt for writing a module's .md/.skill
└── tests/                  # pytest suite
```

## How a command line runs

1. `parser.parse()` splits the line into segments and an optional redirect. Quotes are respected, and backslashes stay literal, which keeps Windows paths intact. Unsupported shell operators (`;`, `&&`, `||`, `<`, `2>`…) raise a `ParseError` instead of becoming arguments.
2. `pipeline.run_pipeline()` first expands the user's aliases (`aliases.expand()`, unless `ctx.allow_aliases` is off, as in MCP). It then feeds each segment the previous segment's output as `stdin`. Each command name is resolved in this order:
   1. a **builtin** (`core/builtins.py`, `core/commands/`)
   2. a **module** from `modules/`
   3. an **OS program**, only when `ctx.allow_system` is set (the `system_commands` setting or `--allow-system`)
3. A `CommandError` / `ModuleError`, or any exception, stops the pipeline as a `PipelineError` that names the failing segment.
4. The final output goes to the redirect file, or to `output.show()`. `show()` renders Rich output and pages it when it's taller than the terminal.

### Terminal I/O on Windows and Linux

- **Interactive shell on Windows:** `core.shell.WindowsConsole` opens `CONIN$` and `CONOUT$`, turns on VT processing, and points the process's std handles at them. prompt_toolkit (`Win32Input` / a `Vt100_Output`), the pager, the hidden API-key prompt and Rich all use the console, however stdin and stdout are redirected. prompt_toolkit's raw mode looks the console up with `GetStdHandle`, which is why the std handles are redirected too. `Shell` owns this session and restores it on exit.
- **Window size:** `core.shell.TerminalConsole` makes Rich ask the current prompt_toolkit output for the size, so Rich and the prompt always agree (on Windows: the visible window of `CONOUT$`, with delayed wrap). `--diag` or the `diagnostics` setting prints every size source side by side.
- **Console detection:** use `core.stdio.is_console()`, never `isatty()`. On Windows `isatty()` is True for `NUL`.
- **Paging:** `show()` pages output taller than the window. A command can override that by returning `Paged(value, page=True/False)` (`core.context`): `man` always pages (`man -p` prints), `help` always prints. Pipes, files and MCP see only `value`.
- **Encoding:** `utf8_stdio()` makes stdout/stderr UTF-8 when they aren't already (Windows pipes and files default to cp1252), unless `PYTHONIOENCODING` is set. The MCP stdio transport wraps the binary streams as UTF-8 itself.
- **OS commands:** output is decoded as UTF-8 with `\r\n` → `\n`. `cmd.exe` internal commands run with `/u`, so their output arrives as UTF-16.

`run_line()` doesn't depend on the UI. The REPL, the MCP server and the tests all share it.

### Permission flags (`ShellContext`)

| Flag | Blocks | Off in MCP |
| --- | --- | --- |
| `allow_redirect` | `>` / `>>` | yes |
| `allow_stateful` | builtins marked `stateful` (`cd`, `theme`, `settings`, `exit`, `reload`, `clear`) | yes |
| `allow_writes` | builtins marked `writes` (`tee`, `mkdir`, `cp`, `mv`, `rm`, `touch`) | yes |
| `allow_sensitive` | builtins marked `sensitive` (`env`) | yes |
| `allow_system` | the OS program fallback | unless `--allow-system` |

### Two-faced output (`Styled`)

A command can return `Styled(renderable, text)`: Rich output for the screen, and plain text for
pipes, files and MCP. `ls`, `tree` and `grep` use this. `to_text()` flattens any value (a `str`,
a `Styled` or a Rich renderable) into plain text.

## Adding a builtin

Builtins live in `core/commands/*.py` (or `core/builtins.py` for shell-level commands) and register
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

- Parse arguments with `core.modkit.ArgParser`, and raise `ModuleError` for user errors.
- The `doc` Markdown is the `man` page, **and** its options table (``| `-x`, `--long N` | … |``) is what Tab completion reads. Keep every switch in that table.
- New files in `core/commands/` must be imported in `core/commands/__init__.py`.
- Add tests to `tests/test_commands.py`.

## Adding a setting

Add a `Setting(...)` to `SETTINGS` in `core/settings.py`. Read it with `settings.get(ctx.config, "key")`.
If the setting has to change the running session immediately, give it an `apply` callback. The
`settings` builtin, its completion and its man page pick up new settings automatically.

### API keys (`ENV_SETTINGS`)

Modules declare the environment variables they need as `ENV_SETTINGS = [EnvSetting(NAME, label,
description)]` (`core/modkit.py`). The pieces:

| Where | What |
| --- | --- |
| `core/loader.py` | `_env_settings()` validates the declaration (UPPER_CASE names, non-empty text, no duplicates). A bad one makes the module fail to load with a warning. |
| `core/settings.py` | `env_settings(registry)` collects them, labelled `module · label`. `env_set` / `env_reset` / `export_env` keep `config["env"]` and `os.environ` in sync. `_ORIGINAL_ENV` remembers the shell's own values, so a reset can restore them. `mask()` and `env_status()` produce the display text, and `redact_line()` hides values from history. |
| `core/cli.py` | Calls `export_env()` right after loading the config, before choosing the mode, so the REPL and `--mcp` both see the keys. |
| `core/builtins.py` | The `settings` table rows, the hidden prompt (`prompt_toolkit.prompt(is_password=True)`, or `getpass` outside the REPL), set and reset. |
| `core/shell.py` | `RedactingFileHistory` stores `settings NAME ••••`. |
| `core/config.py` | `save_config()` writes with mode 600 while `"env"` is non-empty. |
| `tools/modtest.py` | `check_env_settings()` checks that every variable is named in the `.md` and the `.skill`. |

`settings` is `stateful`, so MCP clients can never read or change keys through it.

## MCP server notes

- `ShellcraftMCP` (`core/mcp_server.py`) owns the lowlevel `Server`. `build_server()` returns just the `Server` for tests and simple embedding.
- **Tools:** a module with `.skill` `[[params]]` gets `params.input_schema()`, and a call's named values go through `ModuleSpec.argv()` (`params.to_argv()`) before `run(args, stdin)`. Other modules keep `MODULE_INPUT_SCHEMA` (a raw `args` array).
- **Change notifications reach two kinds of clients:**
  - 2026-07-28+ clients open `subscriptions/listen`. `notify_changed()` publishes `ToolsListChanged` / `ResourcesListChanged` on the `InMemorySubscriptionBus`.
  - Older clients get `notifications/*/list_changed` on their `Connection`. The server remembers each connection (weakly) from its requests. mcp 2.x hands handlers only a per-request `ServerSession`, so the connection is read from its private `_connection`. Recheck this when upgrading `mcp` past 2.x; `test_reload_notifies_legacy_clients` catches a break.
- `_Server.create_initialization_options()` always advertises `listChanged`, because the streamable-HTTP manager builds its own init options.
- **Hot reload** is a 1-second poll (`ModuleWatcher.changes()`). It's cheap for a few dozen files and needs no dependency. The shell polls before each command instead.
- **`--mcp-http`** runs uvicorn (an mcp dependency) in the same anyio task group as the watcher. `parse_http_address()` only accepts loopback addresses, and DNS-rebinding protection is always configured.

## Tests

| File | Covers |
| --- | --- |
| `test_parser.py` | tokenizer, quoting, redirects, parse errors |
| `test_pipeline.py` | chaining, failures, redirection, `cd`, restricted contexts |
| `test_commands.py` | every ported builtin |
| `test_settings.py` | settings, the OS-command gate, `--allow-system`, no `-c` mode |
| `test_env_settings.py` | API keys: listing, masking, prompt, set/reset, export at startup, history redaction, completion, loader validation |
| `test_options.py` | switch parsing and Tab completion |
| `test_loader.py` | module discovery, `.skill` parsing |
| `test_aliases.py` | alias/unalias: expansion, appended args, chains without loops, `\` bypass, persistence, which, completion, off for MCP |
| `test_diff_du_history.py` | `diff` formats and flags (compared with GNU diff when installed), `du` sizes/depth/sorting (compared with GNU du), `history` |
| `test_themes.py` | presets and custom themes: every preset color is valid; invalid custom colors fall back to the base preset |
| `test_shell.py` | the prompt's `~` shortening (POSIX and Windows), pager search stepping and match highlighting, hot reload in the shell, the Windows console wiring, terminal size and truecolor, the diagnostics report, `help` printing and `man` paging |
| `test_stdio.py` | console detection, UTF-8 stdout/stderr on non-UTF-8 pipes, `cmd.exe` builtins read as UTF-16 |
| `test_mcp.py` | the MCP server, run in-process: tools, typed params, man-page resources, list_changed notifications (legacy and `subscriptions/listen`), the module watcher, HTTP address checks |
| `test_fetch.py` | `fetch`: headers, methods, request bodies, retries, size limit, binary rejection (HTTP stubbed) |
| `test_myip.py`, `test_ip2geo.py`, `test_querydns.py`, `test_querycert.py`, `test_querycensys.py` | the network modules, with the APIs stubbed (offline): the `http` fixture fakes `urlopen`, and `queryCensys` gets a fake SDK client |
| `test_modtest.py` | the module tester and prompt builder |

Tests that need a real OS program use `tests.conftest.OS_UPPER`, which runs the current Python
interpreter, so they work on every platform. Tests that touch API keys use the `clean_env` fixture,
which removes the keys from the environment for the test and restores them afterwards.

For the `queryCensys` module and its tests against the real SDK types, install the extra:
`pip install -e ".[dev,censys]"`. Without it, those few checks are skipped.
