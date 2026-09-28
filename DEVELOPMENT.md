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
│   ├── cli.py              # argument parsing; chooses interactive / -c / --mcp mode
│   ├── shell.py            # REPL: PromptSession, prompt, history, ghost text
│   ├── parser.py           # quote-aware tokenizer → Pipeline(segments, redirect)
│   ├── pipeline.py         # executor: resolves commands, chains |, handles > / >>
│   ├── context.py          # ShellContext (state + permission flags), Styled, to_text()
│   ├── builtins.py         # builtin registry (@builtin) + shell builtins: cd, help, man, theme, settings…
│   ├── commands/           # portable ports registered as builtins
│   │   ├── text.py         #   echo cat grep head tail wc sort uniq cut tr tee
│   │   ├── files.py        #   ls find tree touch mkdir cp mv rm
│   │   ├── info.py         #   date which env
│   │   └── _io.py          #   shared file/stdin helpers
│   ├── loader.py           # module discovery; ModuleSpec; SkillInfo (.skill parsing)
│   ├── options.py          # switch metadata from .skill [[args]] and .md tables (Tab completion)
│   ├── completer.py        # Tab completion: commands, switches, values, paths
│   ├── output.py           # delayed spinner, error panels, auto-pager
│   ├── settings.py         # on/off settings registry; API keys (ENV_SETTINGS) storage/export/masking
│   ├── themes.py           # theme presets → Rich theme + prompt_toolkit style
│   ├── config.py           # ~/.shellcraft/config.json (mode 600 once it holds keys) and history paths
│   ├── banner.py           # startup banner
│   ├── modkit.py           # helpers for module authors: ArgParser, ModuleError, EnvSetting
│   └── mcp_server.py       # MCP server (stdio): one tool per module + shellcraft_pipeline
├── modules/                # bundled modules: fetch, filter, myip, ip2geo, queryDns, queryCert, QueryCensys
├── templates/              # module authoring kit: guide, reference module, AI prompt
├── tools/
│   ├── modtest.py          # module tester
│   └── mkprompt.py         # builds the AI prompt for writing a module's .md/.skill
└── tests/                  # pytest suite
```

## How a command line runs

1. `parser.parse()` splits the line into segments and an optional redirect. Quotes are respected, and backslashes stay literal, which keeps Windows paths intact.
2. `pipeline.run_pipeline()` feeds each segment the previous segment's output as `stdin`. Each command name is resolved in this order:
   1. a **builtin** (`core/builtins.py`, `core/commands/`)
   2. a **module** from `modules/`
   3. an **OS program**, only when `ctx.allow_system` is set (the `system_commands` setting or `--allow-system`)
3. A `CommandError` / `ModuleError`, or any exception, stops the pipeline as a `PipelineError` that names the failing segment.
4. The final output goes to the redirect file, or to `output.show()`. `show()` renders Rich output and pages it when it's taller than the terminal.

`run_line()` doesn't depend on the UI. The REPL, `-c` mode, the MCP server and the tests all share it.

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
| `core/cli.py` | Calls `export_env()` right after loading the config, before choosing the mode, so `-c`, the REPL and `--mcp` all see the keys. |
| `core/builtins.py` | The `settings` table rows, the hidden prompt (`prompt_toolkit.prompt(is_password=True)`, or `getpass` outside the REPL), set and reset. |
| `core/shell.py` | `RedactingFileHistory` stores `settings NAME ••••`. |
| `core/config.py` | `save_config()` writes with mode 600 while `"env"` is non-empty. |
| `tools/modtest.py` | `check_env_settings()` checks that every variable is named in the `.md` and the `.skill`. |

`settings` is `stateful`, so MCP clients can never read or change keys through it.

## Tests

| File | Covers |
| --- | --- |
| `test_parser.py` | tokenizer, quoting, redirects, parse errors |
| `test_pipeline.py` | chaining, failures, redirection, `cd`, restricted contexts |
| `test_commands.py` | every ported builtin |
| `test_settings.py` | settings, the OS-command gate, `--allow-system` |
| `test_env_settings.py` | API keys: listing, masking, prompt, set/reset, export at startup, history redaction, completion, loader validation |
| `test_options.py` | switch parsing and Tab completion |
| `test_loader.py` | module discovery, `.skill` parsing |
| `test_mcp.py` | the MCP tools, run in-process |
| `test_myip.py`, `test_ip2geo.py`, `test_querydns.py`, `test_querycert.py`, `test_querycensys.py` | the network modules, with the APIs stubbed (offline): the `http` fixture fakes `urlopen`, and `QueryCensys` gets a fake SDK client |
| `test_modtest.py` | the module tester and prompt builder |

Tests that need a real OS program use `tests.conftest.OS_UPPER`, which runs the current Python
interpreter, so they work on every platform. Tests that touch API keys use the `clean_env` fixture,
which removes the keys from the environment for the test and restores them afterwards.

For the `QueryCensys` module and its tests against the real SDK types, install the extra:
`pip install -e ".[dev,censys]"`. Without it, those few checks are skipped.
