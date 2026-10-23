# Funcs

← [Manual](README.md)

A func is a named list of ShellCraft commands that runs when you call it. Think of it as a small
script, or as an rc file you run on demand. Use one for any routine you type the same way every
time: a recon pass on a domain, a release checklist, setting up a project folder.

```
func run recon example.com
```

The steps run one after another in your shell, as if you typed them, so a `cd` or `env set` in a
func stays in effect afterwards. `$1`, `$2`… are the arguments, `$@` is all of them and `$#` how
many. The func stops at the first step that fails and tells you which one.

## Write one by hand

A func is a text file in `~/.shellcraft/funcs/`, named after the func. ShellCraft put an example
there for you. Copy it to see how it works:

```bash
cd ~/.shellcraft/funcs
cp example.func.sample recon.func
```

Here's what `recon.func` contains:

```
# func: recon
# description: Quick look at a domain: its subdomains and DNS records
# system: no
# output: show
# command: yes
# usage: recon DOMAIN
queryCert -s $1 > subs-$1.txt
queryDns $1
@ cat subs-$1.txt | wc -l
echo "recon of $1 done, subdomains in subs-$1.txt"
```

The `# key: value` lines at the top are the header. Every line after them is one step. Save the
file and the func works at once:

```
func run recon example.com
recon example.com            # the same, because the header says command: yes
```

### The header

| Line | What it means |
| --- | --- |
| `# func: NAME` | Its name. The file name wins if they differ |
| `# description: TEXT` | One line, shown by `func list` |
| `# system: yes` | It runs OS programs, so it needs `settings system_commands on` |
| `# output: show` | Every step's output is printed. `quiet` prints only `echo`, `printf` and steps starting with `@` |
| `# command: yes` | Typing its name runs it, like a command. A builtin or module with the same name still wins |
| `# usage: TEXT` | How to call it, shown by `func list` and `func show` |

All of them are optional. Other lines starting with `#` after the header are comments, and so is a
` # note` after a step (see [Comments](scripting.md#comments)). A `for … done` loop written over
several lines counts as one step.

### Another example

A release checklist that stops at the first problem:

```
# func: release
# description: Test, tag and push a release
# system: yes
# output: quiet
# usage: release VERSION
@ echo "releasing $1"
pytest -q
git tag v$1
git push origin v$1
@ echo "v$1 is out"
```

With `output: quiet`, the test output stays hidden unless something fails, and the `@` lines
report progress.

## What you see

ShellCraft doesn't print the commands themselves, the same as a bash script. When the func
finishes you get one muted line:

```
✓ recon: 4 steps, 2.3 s
```

or, if a step failed:

```
✗ recon: step 2 failed (queryDns example.com): …
```

## Run it other ways

| Command | What it does |
| --- | --- |
| `func run NAME ARGS` | Run it in your shell, as if typed |
| `func run -i NAME ARGS` | Isolated: afterwards your folder, variables, aliases, theme and settings are put back |
| `func run -b NAME ARGS` | In the background, as a job in the footer. Steps that change the shell, like `cd`, are refused there |
| `func run -q NAME ARGS` | Quiet this time, whatever the header says (`-v` shows everything) |

## Make and change funcs from the prompt

If you'd rather not edit files, `func` does it all from the prompt:

| Command | What it does |
| --- | --- |
| `func new NAME` | Asks a few questions, then the steps one per line. An empty line ends |
| `func new NAME -e` | Opens your editor instead (see [the editor](#the-editor)) |
| `func new NAME 'CMD' 'CMD'` | All at once. Add `-d DESC`, `--system`, `--quiet`, `--command` |
| `--save` | Keep it on disk. Without it, the func lasts until you leave ShellCraft |
| `-g` | With `--save`: keep it with your main setup instead of the active profile |
| `func` or `func list` | The funcs you can run |
| `func show NAME` | Its header and numbered steps |
| `func edit NAME` | Open it in your editor |
| `func add NAME 'CMD'`, `insert NAME N 'CMD'`, `rm NAME N`, `move NAME FROM TO` | Change the steps |
| `func set NAME FIELD VALUE` | Change a header line: `description`, `system`, `output`, `command`, `usage` |
| `func rename OLD NEW`, `copy SRC DST`, `delete NAME` | Manage them |
| `func export NAME FILE`, `func import FILE` | Share them as one JSON file |
| `func path NAME` | Where its file is |

## Where funcs live

- `~/.shellcraft/funcs/` holds funcs that work in every profile.
- `~/.shellcraft/profiles/NAME/funcs/` holds a profile's own. While that profile is active, one of
  its funcs wins over a main one with the same name, and `func list` says `(hides default)`.
- A func made at the prompt without `--save` lives only in the session.

AI clients can't run funcs.

## The editor

`func edit`, `func new -e` and `profile edit` open the editor you choose:

```
settings editor                 # the one in use, and the editors found on this machine
settings editor code            # VS Code. Also subl, vim, nvim, nano, micro, or any command
settings reset editor           # back to $VISUAL, then $EDITOR, then nano (notepad on Windows)
```

A terminal editor like vim or nano opens in a subshell with the whole screen. A window editor like
VS Code opens its own window, and ShellCraft waits until you close the tab, then reads the file
back.
