# Cheat sheet

← [Manual](README.md)

Copy, paste, change the names. This page is for when you know what you want and only need the
exact lines. Each block says when you'd use it and links to the page that explains it.

## Files you edit

Everything you set up lives in one folder. Use this to find the right file.

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

For a shorter name for one command you type with the same options every time.
Put it in `~/.shellcraft/aliases`, or `alias errs='grep -i error'` at the prompt:

```
alias ll='ls -l'
alias errs='grep -i error'
alias api='curl -s -H "Accept: application/json"'
```

[More](aliases-and-history.md)

## The rc file

For anything you want set up every time the shell starts. `~/.shellcraft/rc`:

```
theme nord
cd ~/projects
env set PROJECT_ROOT=~/projects
env set -s GITHUB_TOKEN
backup() { cp -r "$1" "$1.bak" && echo "saved $1.bak"; }
[ -f ~/.shellcraft/rc.work ] && source ~/.shellcraft/rc.work
trap 'echo bye' EXIT                     # runs when you leave the shell
```

Check it after an edit, and switch on stopping at the first error when later lines depend on
earlier ones:

```
source -n ~/.shellcraft/rc               # nothing runs; problems as rc:LINE: …
set -e                                   # at the top: a failing line skips the rest of the file
set +e                                   # at the end: so it doesn't stay on at the prompt
exit 1                                   # ends ShellCraft right there (start with --no-startup to fix)
```

[More](startup.md)

## A startup file

For one setup job kept in its own file, once the rc file gets long.
`~/.shellcraft/startup/10-project.sc`, run after rc:

```
project = input("Which project today? ")
env set PROJECT_ROOT=~/work/$project
cd $PROJECT_ROOT
sub tail -f app.log              # something that keeps running, in the background
```

[More](startup.md#startup-files)

## Scripting

The lines you use in the rc file, startup files, funcs and at the prompt. Bash style first:

```
name=Ada; echo "hi $name"                        # a variable
echo "${who:-stranger}" $((2 + 3))               # a default, arithmetic
echo ${f%.txt} ${f##*/} ${#f} ${f^^} ${f/a/b}    # trim, length, capitals, replace
name = input("Your name? ")                      # ask, Python style (anywhere, also in blocks); input(f"…") too
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
try { cp a b } except as rc { echo "failed $rc" }  # catch the first failure;  finally { … } always runs
while getopts "vf:" o; do case $o in v) v=1;; f) f=$OPTARG;; esac; done   # -v -f FILE
shift $((OPTIND - 1))                            # then $1 is the first word after the options
source -n deploy.sc                              # check a file without running it (func check NAME: a func)
```

The same in Python style, for when quoting gets in the way: a `:` and an indented body (an empty
line ends it at the prompt):

```
if -py int(n) > 3:                               # elif …:  else:
    echo big
for -py line in (cat hosts.txt):                 # a file, line by line
    if line.startswith("#"):
        continue
    echo $line
while -py int(n) < 3:                            # while -py True: … break
    n += 1                                       # also = -= *= /= //= %= (in a block or def)
    msg = f"{n:>3} of {total}"                   # f-strings and "{}-{}".format(a, b)
    ok = n > 1                                   # numbers and True/False keep their type; $ok is text
    print(msg, "done", sep=" | ")                # print (in a block or def): like echo
match env:                                       # case "a" | "b":   case _: (anything else)
    case "prod":
        echo careful
    case _:
        echo safe
case -py $env:                                   # the same, spelled "prod": and _:
    "prod": echo careful
select -py env in dev prod:                      # a numbered menu
    break
def greet(who, greeting="hi"):                   # named arguments, with defaults
    echo "$greeting $who"
def add(a, b):                                   # add 1 2: status 3; add 300 4 prints 304
    return int(a) + int(b)
def is_weekend(day):                             # return True / False for a check
    return day in ("Sat", "Sun")
if -py is_weekend("Sun"): echo rest              # call a function from Python, with brackets
try:                                             # the first failing command jumps to except
    ./deploy.sh
except as rc:                                    # rc and $?: the failing status
    echo "deploy failed: $rc"
finally:                                         # always runs
    rm -f deploy.lock
for -py n in 1..5: echo $n                       # a short block on one line
while -py input("More? ") == "y":                # input() anywhere in Python; int(input()) asks till a number
```

[More](scripting/README.md) · [Quick reference](scripting.md)

## A func

For a routine you run on demand that should stop at the first step that fails.
`~/.shellcraft/funcs/recon.func`, then `recon example.com`:

```
# func: recon
# description: Subdomains and DNS for a domain
# command: yes
# usage: recon DOMAIN
queryCert -s $1 > subs-$1.txt
queryDns $1
```

From bash or cron, without opening ShellCraft (the exit code is the func's status):

```bash
shellcraft func run recon example.com
```

Check it, run it every night, give it options and clean up:

```
func check recon                                  # nothing runs; problems as recon:LINE: …
0 2 * * * shellcraft --no-startup func run backup notes >> ~/backup.log 2>&1   # a crontab line
while getopts "vf:" o; do case $o in v) v=1;; f) f=$OPTARG;; esac; done   # a step: -v -f FILE
trap 'rm -f bundle.tmp' EXIT                      # a first step: runs however the func ends
```

[More](funcs.md)

## A profile

For a separate setup per client or project, with its own theme, settings and history.
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

For a command of your own written in Python. `~/.shellcraft/modules/hello.py`, then `hello`:

```python
def run(args, stdin):
    return "hello\n"
```

## A module with switches, piped input and errors

When your command takes options, reads what's piped into it, and should fail with a clear message:

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

When your command talks to a web service that needs an API key:

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

To turn a Python script you already have into a ShellCraft command:

```bash
ingest myscript.py                              # inside ShellCraft
shellcraft ingest myscript.py --name mycmd      # from a terminal
printf 'auto\ny\nn\ny\n' | shellcraft ingest myscript.py --name mycmd    # no questions
```

[More](modules/ingest.md)

## Check modules

After you write or change a module, before you rely on it:

```bash
ingest check ~/.shellcraft/modules/mycmd.py
ingest check --all --strict
```

## Settings you'll want

The settings most people change in the first week:

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

From your normal terminal, when the default start isn't what you need:

```bash
shellcraft --profile acme        # in a profile
shellcraft --no-startup          # skip rc and the startup files
shellcraft --timing              # why is it slow?
shellcraft --mcp                 # as an MCP server for an AI client
shellcraft --init                # write missing starter files
```

[More](command-line.md)
