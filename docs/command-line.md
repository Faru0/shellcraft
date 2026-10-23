# Command line

← [Manual](README.md)

The options you can give when you start ShellCraft, and the environment variables it reads. From
a checkout, `python main.py` takes the same options as the installed `shellcraft` command.

## Starting the shell

```bash
shellcraft                       # the interactive shell
shellcraft --profile recon       # start in a profile
shellcraft --theme matrix        # a theme for this session only
shellcraft --no-startup          # skip the startup scripts and the rc file
shellcraft --timing              # show how long each part of the start took
```

| Option | What it does |
| --- | --- |
| `--profile NAME` | Start in a [profile](profiles.md). Without it, every start is your main setup |
| `--theme NAME` | Use a theme for this session only |
| `--no-banner` | Skip the banner |
| `--no-startup` | Skip `~/.shellcraft/startup/` and the rc file |
| `--allow-system` | Let commands run your system's programs, for this session only |
| `--modules DIR` | Load the bundled modules from DIR instead. Your own folder, `~/.shellcraft/modules`, is still loaded |
| `--timing` | Print how long each startup step took, just before the first prompt |
| `--diag` | Turn on the hidden `diag` command and print its report at startup |
| `--init` | Write the [starter files](your-shellcraft-folder.md#the-starter-files) that are missing, then exit |
| `--init --force` | Also refresh the `.sample` and README starter files |
| `--version`, `-h` | The version, or the help text |

## Running as an MCP server

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

## Turning scripts into modules

```bash
shellcraft ingest myscript.py             # the wizard
shellcraft ingest check --all             # check every module
```

See [Ingest a script](modules/ingest.md).

## Environment variables

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
