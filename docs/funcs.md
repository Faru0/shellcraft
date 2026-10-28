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
many. The func stops at the first step that fails and tells you which one. `set -e` gives
functions, `source`d files and your rc file the same behaviour (see
[Stop on errors](scripting/functions.md#stop-on-errors-with-set--e)). Inside a step that is a
whole block, such as a `for` loop, `set -e` also stops the block at its first failing command.

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
several lines counts as one step, and so does a Python-style block (a line ending in `:` and the
indented lines under it).

A func's steps can be anything from the [scripting guide](scripting/README.md), in bash style or
Python style: questions with `read` or `input()`, `if` and `case`, loops and menus. See [a menu func](scripting/examples.md#a-menu-func) and
[a setup func with arguments](scripting/examples.md#a-setup-func-with-arguments).

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

### Clean up when a step fails

A func stops at the first step that fails, so a cleanup step at the end may never run. Set a
`trap` as the first step instead. Its command runs when the func ends, whether it finished,
failed or you pressed Ctrl-C:

```
# func: bundle
# description: Pack the notes into one file
# usage: bundle NAME
trap 'rm -f bundle.tmp; echo "cleaned up"' EXIT
cat notes/*.txt > bundle.tmp
! grep -q "TODO" bundle.tmp
cp bundle.tmp $1.txt
```

When a TODO is left, the `! grep` step fails and the func stops there, and `bundle.tmp` is still
removed. The trap belongs to the func and
goes away when it ends. `trap '…' ERR` runs after a step fails, and `trap '…' INT` on Ctrl-C. See
[Clean up with trap](scripting/functions.md#clean-up-with-trap) and `man trap`.

### Options

A func can take options like a command, with `getopts`. This one has `-n NAME` and `-l` for
loud:

```
# func: greet
# description: Say hello
# usage: greet [-l] [-n NAME] [MESSAGE...]
name=you; loud=no
while getopts "ln:" opt; do
  case $opt in
    l) loud=yes ;;
    n) name=$OPTARG ;;
  esac
done
shift $((OPTIND - 1))
echo "hello $name! $@"
if [ $loud = yes ]; then echo "HELLO $name!"; fi
```

`greet -l -n Ana nice day` prints `hello Ana! nice day` and `HELLO Ana!`. For an option it
doesn't know, `getopts` prints `getopts: illegal option -- x` and the loop goes on. Each func run starts
with its own `$OPTIND` at 1. See
[Options like -v and -f FILE](scripting/functions.md#options-like--v-and--f-file) and
`man getopts`.

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
| `func run -i NAME ARGS` | Isolated: afterwards your folder, variables, aliases, theme, settings and `set` options are put back |
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
