# ⚡ ShellCraft

```
███████╗██╗  ██╗███████╗██╗     ██╗      ██████╗██████╗  █████╗ ███████╗████████╗
██╔════╝██║  ██║██╔════╝██║     ██║     ██╔════╝██╔══██╗██╔══██╗██╔════╝╚══██╔══╝
███████╗███████║█████╗  ██║     ██║     ██║     ██████╔╝███████║█████╗     ██║
╚════██║██╔══██║██╔══╝  ██║     ██║     ██║     ██╔══██╗██╔══██║██╔══╝     ██║
███████║██║  ██║███████╗███████╗███████╗╚██████╗██║  ██║██║  ██║██║        ██║
╚══════╝╚═╝  ╚═╝╚══════╝╚══════╝╚══════╝ ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝        ╚═╝
```

A modular, pipe-friendly terminal shell in Python for **Linux and Windows**. It follows the Unix
idea: *input → process → pipe/redirect out*.

- **prompt_toolkit** handles input: fish-style ghost-text suggestions from history, and Tab completion for commands, paths, `man` topics and theme names.
- **Rich** handles output: a gradient banner, themed error panels, live spinners, `man` pages rendered from Markdown, and a built-in scrollable pager.
- **MCP** is built in: every module is also an MCP tool, and the tool's description comes from the module's `.skill` file.

## Project layout

```
shellcraft/
├── main.py              # launcher: interactive | -c "<line>" | --mcp
├── core/
│   ├── cli.py           # argument parsing, mode selection
│   ├── shell.py         # REPL (PromptSession, prompt, history, ghost text)
│   ├── completer.py     # command / argument / path completion
│   ├── parser.py        # quote-aware tokenizer → Pipeline(segments, redirect)
│   ├── pipeline.py      # executor: builtins → modules → system fallback, |, >, >>
│   ├── builtins.py      # cd pwd echo exit clear help man theme modules reload
│   ├── output.py        # delayed spinner, error panels, auto-pager
│   ├── themes.py        # presets + Rich/prompt_toolkit style generation
│   ├── config.py        # ~/.shellcraft/config.json
│   ├── banner.py        # startup banner
│   ├── loader.py        # module discovery, .skill parsing
│   ├── modkit.py        # helpers for module authors (ArgParser, ModuleError)
│   └── mcp_server.py    # MCP server (stdio)
├── modules/
│   ├── fetch.py  fetch.md  fetch.skill     # data fetcher (URL / file / stdin, --json, --head)
│   └── filter.py filter.md filter.skill    # grep-style line filter
└── tests/
```

## Install

You need Python **3.11+**.

**Linux / macOS**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt        # or: pip install -e ".[dev]"
```

**Windows (PowerShell)**

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt        # or: pip install -e ".[dev]"
```

## Run the interactive shell

```bash
python main.py                      # banner + REPL
python main.py --theme nord         # theme for this session only
python main.py -c "fetch app.log | filter -i error > errors.txt"   # one-shot, script-friendly
```

If you installed with `pip install -e .`, the `shellcraft` command also works.

### Things to try

```
help                                    # all builtins + modules
man fetch                               # Markdown manual in the pager
fetch README.md | filter -n -i shell    # pipeline
fetch README.md | filter -c GitLab > count.txt
echo another line >> count.txt
fetch https://api.github.com/repos/python/cpython --json stargazers_count   # spinner while fetching
theme                                   # list themes with swatches
theme matrix                            # switch now and save as the default
cd -  /  pwd  /  exit
```

- **Ghost text:** type the start of a command you've run before and a faint suggestion appears. Press **→** to accept it.
- **Tab** completes commands in command position (the first word, or the first word after `|`), `man`/`theme` arguments, and file paths everywhere else, including after `>` and `>>`.
- **Unknown commands** fall back to executables on your `PATH`, such as `sort`, `git` or `tr`. On Windows, `cmd` built-ins such as `dir` and `type` also work. These commands run non-interactively: their output is captured and passed down the pipe.
- **Long output** that is taller than the terminal opens the pager:
  - `↑↓`/`j k` scroll by line, and `PgUp`/`PgDn`/`space` scroll by page
  - `g`/`G` jump to the top or end
  - `/` searches, `n`/`N` find the next or previous match, and `q` quits
- **Errors** stop the pipeline and show a panel naming the failing segment. Nothing is written to the redirect target.

## Themes

The presets are `cyberpunk` (Cyberpunk Neon), `matrix` (Matrix Green), `nord` and `solarized`.
The active theme is stored in `~/.shellcraft/config.json`; set `SHELLCRAFT_HOME` to use a different directory.
You can add your own themes there. Any color you don't set is inherited from `base`:

```json
{
  "theme": "sunset",
  "themes": {
    "sunset": {
      "base": "cyberpunk",
      "label": "Sunset",
      "prompt": "#ff7b00",
      "accent": "#ff0059",
      "gradient_from": "#ff7b00",
      "gradient_to": "#ff0059"
    }
  }
}
```

These are the semantic colors a theme can set: `prompt`, `path`, `accent`, `muted`, `error`, `success`, `warning`, `gradient_from`, `gradient_to`.

## Run the MCP server

```bash
python main.py --mcp                    # stdio transport
python main.py --mcp --modules ./modules --allow-system
```

- Each module becomes a tool that takes `{"args": [..], "stdin": "..."}`. Its `.skill` content is the tool description.
- An extra tool, `shellcraft_pipeline`, takes `{"command": "fetch x | filter y"}`. For safety, it cannot use redirection or state-changing builtins (`cd`, `theme`, `exit`, …). It also cannot fall back to system executables unless you pass `--allow-system`.
- Logs go to stderr, because stdout carries the protocol.

**Claude Desktop / Claude Code** (`mcpServers` config) — use absolute paths:

```json
{
  "mcpServers": {
    "shellcraft": {
      "command": "/path/to/shellcraft/.venv/bin/python",
      "args": ["/path/to/shellcraft/main.py", "--mcp"]
    }
  }
}
```

On Windows, use `C:\\path\\to\\shellcraft\\.venv\\Scripts\\python.exe` for `command`. With Claude Code you can instead run:
`claude mcp add shellcraft -- /path/to/.venv/bin/python /path/to/main.py --mcp`.

To inspect the server interactively: `npx @modelcontextprotocol/inspector python main.py --mcp`.

## Writing a module

A tool is made of three files that share one base name in `modules/`:

| File | Purpose |
| --- | --- |
| `<name>.py` | `run(args: list[str], stdin: str) -> str`: gets text in and returns text out. Optional `SUMMARY` (one line) and `SPINNER_TEXT`. |
| `<name>.md` | Manual page shown by `man <name>` (rendered Markdown). |
| `<name>.skill` | Guidance for AI agents, used as the MCP tool description. TOML fields: `summary`, `when_to_use`, `usage`, `[[args]]` (`name`, `description`), `examples`, `notes`. Plain free-form text also works. |

Minimal example, `modules/upper.py`:

```python
"""upper — uppercase the input stream."""

def run(args, stdin):
    return stdin.upper()
```

- **Errors:** raise `core.modkit.ModuleError("message")` to show a clean error. Any other exception is shown with its type name.
- **Arguments:** `core.modkit.ArgParser` is an `argparse` that reports bad arguments as errors instead of exiting.
- **Loading:** run `reload` in the shell to pick up new modules without restarting. A broken module is skipped with a warning; it doesn't stop the shell.

## Tests

```bash
pip install -e ".[dev]"
pytest
```
