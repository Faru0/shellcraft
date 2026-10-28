# Command line

← [Manual](README.md)

The options you can give when you start ShellCraft from your normal terminal, and the environment
variables it reads. Use this page when you're starting ShellCraft in some special way: in a
profile, without your rc file, as a server for an AI client, or from a script or cron job. From a
checkout, `python main.py` takes the same options as the installed `shellcraft` command.

## Starting the shell

These change how the interactive shell starts. You can combine them: `shellcraft --profile recon --no-banner`.

```bash
shellcraft                       # the interactive shell
shellcraft --profile recon       # start in a profile
shellcraft --theme matrix        # a theme for this session only
shellcraft --no-startup          # skip the rc file and the startup files
shellcraft --timing              # show how long each part of the start took
```

| Option | What it does |
| --- | --- |
| `--profile NAME` | Start in a [profile](profiles.md). Without it, every start is your main setup |
| `--theme NAME` | Use a theme for this session only |
| `--no-banner` | Skip the banner |
| `--no-startup` | Skip the rc file and the startup files in `~/.shellcraft/startup/`. Use it when a line in your rc file breaks the start, or ends it with `exit` |
| `--allow-system` | Let commands run your system's programs, for this session only |
| `--modules DIR` | Load the bundled modules from DIR instead. Your own folder, `~/.shellcraft/modules`, is still loaded |
| `--timing` | Print how long each startup step took, right before the first prompt |
| `--diag` | Turn on the hidden `diag` command and print its report at startup (`diag --save` writes it to a file) |
| `--init` | Write the [starter files](your-shellcraft-folder.md#the-starter-files) that are missing, then exit |
| `--init --force` | Also refresh the `.sample` and README starter files |
| `--version`, `-h` | The version, or the help text |

## Running as an MCP server

Use these when an AI client should use ShellCraft's modules as tools, instead of you typing at a prompt.

```bash
shellcraft --mcp                          # over stdio, for an AI client to start
shellcraft --mcp-http 127.0.0.1:38765     # over HTTP
```

| Option | What it does |
| --- | --- |
| `--mcp` | Run as an MCP server over stdio instead of the shell |
| `--mcp-http HOST:PORT` | Run as an MCP server over HTTP at `http://HOST:PORT/mcp`. Only addresses on this machine are allowed |
| `--expose NAME,...` | Offer only these modules as tools |
| `--token TOKEN` | With `--mcp-http`: the token clients must send. Without it, a random one is printed at startup |
| `--no-token` | With `--mcp-http`: no token at all |

[MCP and the AI co-pilot](mcp-and-ai.md) explains how to connect a client.

## Running a func

Use this to run a [func](funcs.md) from bash, another script or cron, without opening the shell.

```bash
shellcraft func run backup notes                          # run a func and exit
shellcraft --profile work --no-startup func run deploy    # options go before func
shellcraft func run -v backup notes                       # also the ✓ / ✗ summary line, on stderr
shellcraft func list                                      # also: func show NAME, func check NAME, func path NAME
```

```
0 2 * * * shellcraft --no-startup func run backup notes >> ~/backup.log 2>&1   # a crontab line
```

ShellCraft runs the [func](funcs.md#run-a-func-from-outside-shellcraft) with no banner and no
prompt, after the rc file and startup files (unless `--no-startup`), and exits with the func's
status. Its output goes to stdout and its errors to stderr.

| Exit code | Means |
| --- | --- |
| `0` | Every step worked |
| The failing step's status | A step failed, and the steps after it didn't run |
| `N` | `exit N` ran in the func, the rc file or a startup file |
| `127` | No func by that name |
| `0`, `1`, `2` | For `func check`: clean, warnings only, errors |

`-q` and `-v` go after `run`. `-b` doesn't work here; put `&` after the command in bash instead.

## Turning scripts into modules

Use these to turn a Python script into a ShellCraft command, or to check the modules you have.

```bash
shellcraft ingest myscript.py             # the wizard
shellcraft ingest check --all             # check every module
```

See [Ingest a script](modules/ingest.md).

## Environment variables

Set these in your normal shell (for example in `~/.bashrc`) before you start ShellCraft. An option on the command line wins over the matching variable.

| Variable | What it does |
| --- | --- |
| `SHELLCRAFT_HOME` | Where your settings and files live. The default is `~/.shellcraft` |
| `SHELLCRAFT_MODULES` | Where the bundled modules are loaded from. `--modules` wins |
| `SHELLCRAFT_MCP_TOKEN` | The token for `--mcp-http`. `--token` wins |
| `SHELLCRAFT_MCP_TIMEOUT` | How many seconds an AI client's tool call may take. The default is 120 |
| `SHELLCRAFT_TIMING` | Set to 1 for the same report as `--timing` |
| `SHELLCRAFT_AI_MODEL` | The model ingest's AI option uses, whichever provider you pick |
| `SHELLCRAFT_CLAUDE_MODEL`, `SHELLCRAFT_OPENAI_MODEL` | The same, for one provider only |
| `SHELLCRAFT_CONSOLE` | Windows only: `direct` (the default), `legacy-keys` or `native`, to compare console setups when something looks wrong. See [Troubleshooting](troubleshooting.md) |
