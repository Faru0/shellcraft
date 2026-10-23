# Cheat sheet

← [Manual](README.md)

Copy, paste, change the names. Each block links to the page that explains it.

## Files you edit

```
~/.shellcraft/rc                     commands run at every start
~/.shellcraft/aliases                alias name='command'
~/.shellcraft/startup/NN-name.py     Python run at every start
~/.shellcraft/funcs/NAME.func        a func
~/.shellcraft/modules/NAME.py        a command of your own (+ NAME.md, NAME.skill)
~/.shellcraft/profiles/NAME/         a profile: profile.json, rc, aliases, funcs/
~/.shellcraft/config.json            settings, theme, API keys, custom themes
```

Missing starter files: `shellcraft --init`. [More](your-shellcraft-folder.md)

## An alias

`~/.shellcraft/aliases`, or `alias errs='grep -i error'` at the prompt:

```
alias ll='ls -l'
alias errs='grep -i error'
alias api='curl -s -H "Accept: application/json"'
```

[More](aliases-and-history.md)

## The rc file

`~/.shellcraft/rc`:

```
theme nord
cd ~/projects
env set PROJECT_ROOT=~/projects
env set -s GITHUB_TOKEN
backup() { cp -r "$1" "$1.bak" && echo "saved $1.bak"; }
[ -f ~/.shellcraft/rc.work ] && source ~/.shellcraft/rc.work
```

[More](startup.md)

## A startup script

`~/.shellcraft/startup/10-env.py`:

```python
import os

with open(os.environ["SHELLCRAFT_ENV_FILE"], "a") as f:
    f.write("PROJECT_ROOT=/srv/app\n")
```

Keep it running in the background: first line `# shellcraft: background, restart`. [More](startup.md#startup-scripts)

## A func

`~/.shellcraft/funcs/recon.func`, then `recon example.com`:

```
# func: recon
# description: Subdomains and DNS for a domain
# command: yes
# usage: recon DOMAIN
queryCert -s $1 > subs-$1.txt
queryDns $1
```

[More](funcs.md)

## A profile

`~/.shellcraft/profiles/acme/profile.json`, then `profile use acme`:

```json
{
  "schema": 1,
  "description": "Acme engagement",
  "color": "#05d9e8",
  "theme": "nord",
  "settings": {"links": true},
  "modules": {"disable": ["queryCensys"]},
  "env": {"ACME_ROOT": "~/work/acme"},
  "history": "own"
}
```

Or `profile new acme --template bugbounty`. [More](profiles.md)

## A module, bare minimum

`~/.shellcraft/modules/hello.py`, then `hello`:

```python
def run(args, stdin):
    return "hello\n"
```

## A module with switches, piped input and errors

```python
from shellcraft.modkit import ArgParser, ModuleError

SUMMARY = "One line for help"


def run(args, stdin):
    parser = ArgParser("mycmd")
    parser.add_argument("items", nargs="*")
    parser.add_argument("-n", "--count", type=int, default=10)
    opts = parser.parse_args(args)

    items = opts.items or stdin.split()
    if not items:
        raise ModuleError("mycmd: nothing to do")
    return "".join(f"{item}\n" for item in items[: opts.count])
```

## A module that calls an API with a key

```python
import os

import requests

from shellcraft.modkit import ArgParser, EnvSetting, ModuleError

SPINNER_TEXT = "asking the API…"
ENV_SETTINGS = [EnvSetting("MYAPI_KEY", "MyAPI key", "From https://myapi.example/account")]


def run(args, stdin):
    parser = ArgParser("mycmd")
    parser.add_argument("query")
    opts = parser.parse_args(args)
    key = os.environ.get("MYAPI_KEY", "").strip()
    if not key:
        raise ModuleError("mycmd: no API key; run: settings MYAPI_KEY")
    try:
        reply = requests.get("https://myapi.example/search", params={"q": opts.query},
                             headers={"Authorization": f"Bearer {key}"}, timeout=10)
    except requests.RequestException as exc:
        raise ModuleError(f"mycmd: can't reach the API: {exc}") from None
    if reply.status_code != 200:
        raise ModuleError(f"mycmd: the API answered {reply.status_code}")
    return "".join(f"{row['name']}\n" for row in reply.json()["results"])
```

[More](modules/first-module.md) · [Every rule](modules/reference.md)

## Ingest a script

```bash
ingest myscript.py                              # inside ShellCraft
shellcraft ingest myscript.py --name mycmd      # from a terminal
printf 'auto\ny\nn\ny\n' | shellcraft ingest myscript.py --name mycmd    # no questions
```

[More](modules/ingest.md)

## Check modules

```bash
ingest check ~/.shellcraft/modules/mycmd.py
ingest check --all --strict
```

## Settings you'll want

```
settings system_commands on      # run git, python, docker… from your PATH
settings links on                # Ctrl-click file names in ls, grep, find
settings editor code             # func edit and profile edit open VS Code
settings loader spinner          # a spinner instead of a bar while starting
settings MY_API_KEY              # store a key, typed hidden
theme nord
prompt name path git status
```

[More](settings-and-themes.md)

## Starting ShellCraft

```bash
shellcraft --profile acme        # in a profile
shellcraft --no-startup          # skip rc and startup scripts
shellcraft --timing              # why is it slow?
shellcraft --mcp                 # as an MCP server for an AI client
shellcraft --init                # write missing starter files
```

[More](command-line.md)
