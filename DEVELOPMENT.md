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
│   ├── settings.py         # on/off settings registry
│   ├── themes.py           # theme presets → Rich theme + prompt_toolkit style
│   ├── config.py           # ~/.shellcraft/config.json and history paths
│   ├── banner.py           # startup banner
│   ├── modkit.py           # helpers for module authors: ArgParser, ModuleError
│   └── mcp_server.py       # MCP server (stdio): one tool per module + shellcraft_pipeline
├── modules/                # bundled modules: fetch, filter, myip (.py/.md/.skill each)
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

## Tests

| File | Covers |
| --- | --- |
| `test_parser.py` | tokenizer, quoting, redirects, parse errors |
| `test_pipeline.py` | chaining, failures, redirection, `cd`, restricted contexts |
| `test_commands.py` | every ported builtin |
| `test_settings.py` | settings, the OS-command gate, `--allow-system` |
| `test_options.py` | switch parsing and Tab completion |
| `test_loader.py` | module discovery, `.skill` parsing |
| `test_mcp.py` | the MCP tools, run in-process |
| `test_myip.py` | `myip`, with the API stubbed (offline) |
| `test_modtest.py` | the module tester and prompt builder |

Tests that need a real OS program use `tests.conftest.OS_UPPER`, which runs the current Python
interpreter, so they work on every platform.
