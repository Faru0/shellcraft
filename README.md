# ⚡ ShellCraft

```
███████╗██╗  ██╗███████╗██╗     ██╗      ██████╗██████╗  █████╗ ███████╗████████╗
██╔════╝██║  ██║██╔════╝██║     ██║     ██╔════╝██╔══██╗██╔══██╗██╔════╝╚══██╔══╝
███████╗███████║█████╗  ██║     ██║     ██║     ██████╔╝███████║█████╗     ██║
╚════██║██╔══██║██╔══╝  ██║     ██║     ██║     ██╔══██╗██╔══██║██╔══╝     ██║
███████║██║  ██║███████╗███████╗███████╗╚██████╗██║  ██║██║  ██║██║        ██║
╚══════╝╚═╝  ╚═╝╚══════╝╚══════╝╚══════╝ ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝        ╚═╝
```

A modular, pipe-friendly terminal shell in Python that behaves the same on **Linux and Windows**.
It follows the Unix idea: *input → process → pipe/redirect out*.

- **Portable built-in commands:** `ls`, `cat`, `grep`, `find`, `sort`, `cut`, `tr` and more, written in Python.
- **Pluggable modules:** drop a module into `modules/` and it becomes a command.
- **Comfortable input:** ghost-text suggestions from history, and Tab completion for commands, switches, switch values and paths.
- **Rich output:** themes, live spinners, `man` pages, a built-in pager, and clear error panels.
- **Built-in MCP server:** AI clients such as Claude Desktop or Claude Code can use your modules as tools.

---

## Installation

You need **Python 3.11 or newer**. Clone or download the project, then run these commands from its folder.

**Linux / macOS**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Windows (PowerShell)**

```powershell
py -m venv .venv
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Optionally, run `pip install -e .` (with `-e`) to get a `shellcraft` command that works from any
folder. Always use `-e`: a plain `pip install .` doesn't include the bundled modules yet.

## Quick start

```bash
python main.py                                        # start the interactive shell
python main.py -c "cat notes.txt | grep -i todo"      # run one command line and exit
python main.py --mcp                                  # run as an MCP server (see below)
```

Inside the shell, try:

```
help                                  # every command, grouped
man grep                              # manual pages, in a scrollable viewer
ls -l | grep py | sort -r             # pipelines
cat README.md | grep -c Shell > count.txt
echo another line >> count.txt       # > overwrites, >> appends
tree -L 1                             # directory tree
find . -name "*.md" | wc -l
myip                                  # your public IP
myip 8.8.8.8 -f country               # look up any IP address
ip2geo google.com -f country,isp      # geolocate an IP or domain name
queryCert -s example.com -o names     # subdomains from certificate logs
queryDns example.com -t mx            # DNS records (needs a free API key: see "API keys")
fetch https://example.com --head 5    # download text (with a spinner)
theme matrix                          # switch the color theme
settings                              # on/off settings and API keys
exit                                  # or Ctrl-D
```

### Command-line options

| Option | Meaning |
| --- | --- |
| `-c "LINE"` | Run one command line and exit. The exit code is 1 if it fails. Output is plain text when piped, so it works in scripts. |
| `--mcp` | Run as an MCP server over stdio instead of the interactive shell. |
| `--theme NAME` | Use a theme for this session only. |
| `--no-banner` | Skip the startup banner. |
| `--allow-system` | Allow OS commands for this session (see [OS commands](#os-commands)). |
| `--modules DIR` | Load modules from DIR instead of `./modules`. |
| `--version`, `-h` | Show the version, or the help text. |

| Environment variable | Meaning |
| --- | --- |
| `SHELLCRAFT_HOME` | Where config and history are stored (default `~/.shellcraft`). |
| `SHELLCRAFT_MODULES` | The default modules folder (overridden by `--modules`). |

---

## Using the shell

### Pipes and redirection

- `a | b | c` passes each command's output to the next command as input.
- `> file` writes the final output to a file, and `>> file` appends to it.
- Quote arguments that contain spaces or operators: `grep "a | b" notes.txt`. Backslashes are kept as typed, so Windows paths like `C:\data\log.txt` work.
- If any command fails, the pipeline stops, an error panel names the failing step, and nothing is written to the redirect file.
- `cd` only works on its own line. `cd x | pwd` is refused, because it would change the shell's directory from inside a pipeline.

These shell features aren't supported yet: `2>`, `<`, `;`, `&&`, `||`, `$VAR` and `*` globbing (see [TODO.md](TODO.md)). The operators are rejected with a parse error rather than passed on as arguments, so `rm a ; ls` never deletes a file named `ls`. Quote them to use them as text.

### Typing helpers

- **Ghost text:** start typing a command you've used before, and a faint suggestion appears. Press **→** to accept it.
- **Tab** completes:
  - command names (the first word, or the first word after `|`)
  - **switches** for every command: `myip -<Tab>`, `grep --<Tab>`, `ls -<Tab>`. The menu shows what each one does, and switches already on the line aren't offered again.
  - **switch values**: `myip -f <Tab>` lists the field names, `queryDns -o <Tab>` offers the output formats, `find . -type <Tab>` offers `f`, `d` and `l`
  - arguments for `man`, `theme` and `settings`, including API-key names
  - file and folder paths everywhere else, including after `>` and `>>`
- **Ctrl-C** cancels the current line or a running command. **Ctrl-D** or `exit` leaves the shell.
- The prompt shows `[✗]` after a command fails.

### Long output

Output taller than the window opens a scrollable viewer:
- `↑` / `↓` or `j` / `k` scroll by line
- `PgUp` / `PgDn` / `space` scroll by page
- `g` / `G` jump to the top or end
- `/` searches, `n` / `N` jump to the next or previous match
- `q` closes the viewer

Turn it off with `settings pager off`.

---

## Built-in commands

These are built into ShellCraft (written in Python), so they work the same on every operating
system. Each one has a manual: `man ls`, `man grep`, …

| Group | Commands |
| --- | --- |
| Shell | `cd`, `pwd`, `exit`, `clear`, `help`, `man`, `theme`, `settings`, `modules`, `reload` |
| Text | `echo`, `cat`, `grep`, `head`, `tail`, `wc`, `sort`, `uniq`, `cut`, `tr`, `tee` |
| Files | `ls`, `find`, `tree`, `touch`, `mkdir`, `cp`, `mv`, `rm` |
| Info | `date`, `which`, `env` |

- **Colored on screen, plain when piped:** `ls` shows colored columns, `tree` draws a colored tree, and `grep` highlights matches. When piped or redirected, they output plain text, one item per line.
- **`which NAME`** tells you whether a name runs a builtin, a module or an OS program. `which -a` also shows OS programs that a builtin hides.
- **`rm` safety:** there is no trash can. For safety, `rm` refuses a filesystem root, your home folder, and the current folder or its parents.
- **`modules`** lists the loaded modules. **`reload`** picks up new or changed modules without restarting.

## Bundled modules

| Module | What it does |
| --- | --- |
| `fetch` | Reads text from a URL, a file or stdin. `--head N` keeps the first N lines, and `--json PATH` extracts a field from JSON. |
| `filter` | Keeps lines matching a regular expression (like `grep`, for stdin). |
| `myip` | Shows your public IP, or the location/network owner of any IP (via ipconfig.io). `-p PORT` checks whether a port is reachable. |
| `ip2geo` | Geolocates IPs or domain names: country, city, ISP, ASN, and mobile/proxy/hosting flags (via ip-api.com, no key). |
| `queryDns` | A domain's DNS records (A, MX, NS, TXT, CNAME) with each IP's owner, country and netblock (DnsDumpster API; **needs a free key**). |
| `queryCert` | TLS certificates and subdomains from Certificate Transparency logs (via crt.sh, no key). `-s` finds subdomains. |
| `queryCensys` | Open ports and software on a host, certificate details, or Censys searches (Censys Platform; **needs a token** and `pip install censys-platform`). |

See `man <module>` for details, e.g. `man queryDns`. To add your own modules, see
[Creating modules](#creating-modules).

## Settings

`settings` shows every setting, and then the API keys modules need (see [API keys](#api-keys)).
`settings KEY on|off|toggle` changes a setting immediately, and `settings reset KEY` restores its
default. Settings are saved in `~/.shellcraft/config.json`.

| Key | Default | Meaning |
| --- | --- | --- |
| `system_commands` | off | Allow OS programs from your `PATH` (see below). |
| `pager` | on | Open output taller than the window in the scrollable viewer. |
| `spinner` | on | Show a spinner while slow commands run. |
| `banner` | on | Show the startup banner. |

### API keys

Some modules call services that need an API key. The module declares the key, and `settings`
lists it after the on/off settings, labelled with the module's name, for example
`queryDns · DnsDumpster API key`. The value is never shown in full. It says `set ••••ab12`,
`from environment` (exported by your own shell), or `not set`. When both exist it says
`from environment (stored ••••ab12 unused)`.

```
settings DNSDUMPSTER_API_KEY          # asks for the key; typing is hidden
settings DNSDUMPSTER_API_KEY VALUE    # or set it in one go
settings reset DNSDUMPSTER_API_KEY    # forget it
```

| Variable | Module | Where to get it |
| --- | --- | --- |
| `DNSDUMPSTER_API_KEY` | `queryDns` | Free account at [dnsdumpster.com](https://dnsdumpster.com): the key is on your dashboard. |
| `CENSYS_API_TOKEN` | `queryCensys` | Censys Platform → your user icon → **API Access** → *Create New Token*. |
| `CENSYS_ORG_ID` | `queryCensys` | Paid plans only (needed for `search`): the *Current Organization* box on the same page. |

`queryCensys` also needs the Censys SDK: `pip install censys-platform`, or
`pip install -e ".[censys]"`.

- **Where keys are stored:** in plain text under `"env"` in `~/.shellcraft/config.json`. The file is made readable by you only (mode 600).
- **How modules get them:** ShellCraft exports the stored keys as environment variables when it starts, in every mode including `--mcp`, and right after you set one. A variable already set in your shell (or in an MCP client's `env` block) wins over the stored key at startup. Setting a key with `settings NAME` during a session replaces it for that session. After `settings reset`, your shell's own value applies again.
- **Command history:** `settings NAME VALUE` is saved as `settings NAME ••••`. Still, prefer the prompt form, `settings NAME`. Start any line with a space to keep it out of the history entirely.
- **Other ways to set them:** you can export the variables in your own shell instead, for example `export DNSDUMPSTER_API_KEY=…`, or in an MCP client's `env` config.

### OS commands

By default, ShellCraft runs **only** its built-in commands and modules. That way, a command line
does the same thing on Windows and Linux. If you type the name of a program that exists on your
system, such as `git`, the error tells you how to allow it.

- `settings system_commands on` allows OS programs permanently. `--allow-system` allows them for one session.
- When allowed, unknown commands run programs from your `PATH`. On Windows, `cmd` built-ins such as `dir` also work.
- Built-in commands still win over OS programs with the same name. Use `which -a NAME` to see both.
- OS programs run with their output captured, so full-screen programs such as `vim`, `top` or `ssh` don't work inside ShellCraft yet.

## Themes

The presets are `cyberpunk` (Cyberpunk Neon, the default), `matrix` (Matrix Green), `nord` and `solarized`.
`theme` lists them with color swatches, and `theme NAME` switches immediately and saves your choice.

You can add your own themes to `~/.shellcraft/config.json`. Any color you leave out is taken from `base`:

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

These are the colors a theme can set: `prompt`, `path`, `accent`, `muted`, `error`, `success`,
`warning`, `gradient_from`, `gradient_to`. Use `#rrggbb` hex values; an invalid color currently
stops the shell from starting.

---

## Using ShellCraft from an AI client (MCP server)

`python main.py --mcp` starts an MCP server over stdio. AI applications can then use these tools:

- **One tool per module** (`fetch`, `filter`, `myip`, `ip2geo`, `queryDns`, `queryCert`, `queryCensys`, and any you add). Each takes `{"args": ["..."], "stdin": "..."}`, and its description comes from the module's `.skill` file.
- **`shellcraft_pipeline`** runs a whole command line such as `{"command": "cat notes.txt | grep -i todo | sort"}`. It can use the read-only built-in commands. For safety, it **cannot**:
  - redirect output to files
  - change files (`tee`, `cp`, `mv`, `rm`, `mkdir`, `touch`)
  - read environment variables (`env`)
  - change the shell (`cd`, `theme`, `settings`, `exit`), so an AI client can't read or change your API keys through `settings`
  - run OS programs, unless you start the server with `--allow-system`

> ⚠️ **Security note:** by design, connected AI clients can read any file your user account can read
> (through `fetch`, `cat`, `grep`, …). They can't change files. `fetch` can also request any URL. Modules that use your API keys
> (`queryDns`, `queryCensys`) spend your quota or credits when an AI calls them. Only connect AI clients you trust.

### Claude Code

```bash
claude mcp add shellcraft -- /path/to/shellcraft/.venv/bin/python /path/to/shellcraft/main.py --mcp
```

### Claude Desktop (or any client that uses an `mcpServers` config)

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

Use absolute paths. On Windows, the Python path is `C:\\path\\to\\shellcraft\\.venv\\Scripts\\python.exe`.

To try the server by hand, use the MCP Inspector:
`npx @modelcontextprotocol/inspector .venv/bin/python main.py --mcp`.

---

## Creating modules

A module is three files in `modules/`: the code (`.py`), its manual (`.md`) and its AI
description (`.skill`). The **module guide** covers the rules, a ready-to-copy template, the
module tester, and a prompt that lets an AI write the `.md` and `.skill` for you:

**→ [templates/README.md](templates/README.md)**

## More documentation

| Document | For |
| --- | --- |
| [templates/README.md](templates/README.md) | Writing and testing your own modules |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Working on ShellCraft itself: project layout, architecture, running the tests |
| [TODO.md](TODO.md) | Known issues and the roadmap |
