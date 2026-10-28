# Cheat sheet

← [Manual](README.md)

Copy, paste, change the names. Each block links to the page that explains it.

## Files you edit

```
~/.shellcraft/rc                     commands run at every start
~/.shellcraft/aliases                alias name='command'
~/.shellcraft/startup/NN-name.sc     more commands, run after rc
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

## A startup file

`~/.shellcraft/startup/10-project.sc`, run after rc:

```
project = input("Which project today? ")
env set PROJECT_ROOT=~/work/$project
cd $PROJECT_ROOT
sub tail -f app.log              # something that keeps running, in the background
```

[More](startup.md#startup-files)

## Scripting

For the rc file, startup files, funcs and the prompt:

```
name=Ada; echo "hi $name"                        # a variable
echo "${who:-stranger}" $((2 + 3))               # a default, arithmetic
name = input("Your name? ")                      # ask, Python style (anywhere, also in blocks)
read -p "Env? " env                              # ask, bash style
read -s -p "Token: " token                       # ask without showing it
echo -e "line 1\nline 2"                         # several lines
printf "%-10s %5.1f\n" cpu 42.5                  # columns and numbers

if [ $n -gt 3 ]; then echo big; elif [ $n -eq 3 ]; then echo three; else echo small; fi
[ -f app.log ] && echo found || echo missing
case $env in dev|test) echo safe ;; prod) echo careful ;; *) echo "?" ;; esac

for f in *.log; do echo $f; done                 # also {1..5}, $(cat list), "a b c"
for -py line in (cat hosts.txt) { echo $line }   # a file, line by line
n=0; while [ $n -lt 3 ]; do n=$((n + 1)); done
select env in dev prod; do echo $env; break; done   # a numbered menu

greet() { local who=${1:-friend}; echo "hi $who"; }
need() { [ -n "$1" ] || return 1; echo "got $1"; }
total=$(add 2 3)                                 # a function's output as a value

set -e                                           # stop at the first command that fails
set -euo pipefail                                # also: unset $vars are errors, pipes fail on any part
set -x                                           # show each command before it runs (set +x: off)
trap 'rm -f $tmp' EXIT                           # clean up when the function, file or func ends
trap 'echo "failed: $?"' ERR                     # after a command fails;  trap '…' INT on Ctrl-C
while getopts "vf:" o; do case $o in v) v=1;; f) f=$OPTARG;; esac; done   # -v -f FILE
shift $((OPTIND - 1))                            # then $1 is the first word after the options
```

The same in Python style: a `:` and an indented body (an empty line ends it at the prompt):

```
if -py int(n) > 3:                               # elif …:  else:
    echo big
for -py line in (cat hosts.txt):                 # a file, line by line
    if line.startswith("#"):
        continue
    echo $line
while -py int(n) < 3:                            # while -py True: … break
    n=$((n + 1))
case -py $env:                                   # "a" | "b":   _: (anything else)
    "prod":
        echo careful
    _:
        echo safe
select -py env in dev prod:                      # a numbered menu
    break
def greet(who, greeting="hi"):                   # named arguments, with defaults
    echo "$greeting $who"
for -py n in 1..5: echo $n                       # a short block on one line
```

[More](scripting/README.md)

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
shellcraft --no-startup          # skip rc and the startup files
shellcraft --timing              # why is it slow?
shellcraft --mcp                 # as an MCP server for an AI client
shellcraft --init                # write missing starter files
```

[More](command-line.md)
