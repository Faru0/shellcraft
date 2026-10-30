# Funcs

← [Manual](README.md)

This page is for you if you keep typing the same handful of commands in the same order: a recon
pass on a domain, a release checklist, setting up a project folder. After reading it you'll be able
to save those steps as a func, run it with one word, give it arguments and options, have it clean
up after itself, check it for mistakes, and run it from bash or cron without opening ShellCraft.

## What a func is

A func is a named list of ShellCraft commands, kept in a text file, that runs when you call it.
Think of it as a small script, or as an rc file you run on demand:

```
func run recon example.com
```

The steps run one after another in your shell, as if you typed them. That means a `cd` or
`env set` in a func stays in effect afterwards, the same as if you'd typed it yourself. `$1`, `$2`…
are the arguments, `$@` is all of them and `$#` how many.

A func stops at the first step that fails and tells you which one. That's what you want for a
checklist: if the tests fail, you don't want the release tagged anyway.

## Func, function, alias or startup file?

ShellCraft has four ways to give a name to something you type often. They overlap, so here's how
to choose:

- An [alias](aliases-and-history.md) is a shorter name for one command with its options, like
  `alias ll='ls -l'`. Use one when the only thing that changes is what you add at the end.
- A [function](scripting.md#functions) is a command of your own, with `if`, loops and arguments.
  Use one for something small you call often, especially inside other commands or pipes:
  `backup notes.txt`, `countdown 3 | sort`. It lives in memory, so put it in your rc file to keep it.
- A func is a routine you run on demand: several steps, saved in its own file, which stops at the
  first step that fails. Use one for a checklist or a job you'd also want to run from cron. A func
  isn't a pipe stage, it runs on its own line.
- A [startup file](startup.md) runs by itself every time the shell starts. Use one for setup you
  want every session, never for something you run now and then.

A good rule: if you'd write it as a shell script in bash, make it a func. If you'd write it as a
bash function in your `.bashrc`, make it a function.

## Write your first func

A func is a text file in `~/.shellcraft/funcs/`, named after the func with `.func` on the end.
ShellCraft put an example there for you. Copy it to see how it works:

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

The `# key: value` lines at the top are the header. They describe the func. Every line after them
is one step, and the steps run top to bottom.

Walking through the steps: `queryCert -s $1` looks up the subdomains of the domain you gave, and
`> subs-$1.txt` saves them in a file named after it, so `recon example.com` writes
`subs-example.com.txt`. `queryDns $1` prints the domain's DNS records. The `@` in front of the third
step means "always show this step's output", which matters when the func is quiet (see the header
below). The last step tells you where the results went.

Save the file and the func works at once. There's nothing to reload:

```
func run recon example.com
recon example.com            # the same, because the header says command: yes
```

### The header

Every header line is optional. Leave one out and you get the default.

| Line | What it means |
| --- | --- |
| `# func: NAME` | Its name. The file name wins if they differ |
| `# description: TEXT` | One line, shown by `func list` |
| `# system: yes` | It runs OS programs, so it needs `settings system_commands on` |
| `# output: show` | Every step's output is printed. `quiet` prints only `echo`, `printf` and steps starting with `@`. Errors always print |
| `# command: yes` | Typing its name runs it, like a command. A builtin or module with the same name still wins |
| `# usage: TEXT` | How to call it, shown by `func list` and `func show` |

If a func has `system: yes` and OS commands are off, it won't start. You'll see
`recon runs OS commands (system: yes): settings system_commands on (or start with --allow-system) first`.
Turn them on with `settings system_commands on`, or start ShellCraft with `--allow-system`.

### What a step can be

Each line is one step, but a block counts as one step too: a `for … done` loop written over several
lines, or a Python-style block (a line ending in `:` and the indented lines under it).

Other lines starting with `#` after the header are comments, and so is a ` # note` after a step
(see [Comments](scripting.md#comments)). Use them to say why a step is there.

A step can be anything from the [scripting guide](scripting/README.md), in bash style or Python
style: questions with `read` or `input()`, `if` and `case`, loops and menus. See
[a menu func](scripting/examples.md#a-menu-func) and
[a setup func with arguments](scripting/examples.md#a-setup-func-with-arguments).

## Run it

At the prompt, `func run NAME` followed by the arguments runs it. With `command: yes` in the header
you can drop the `func run`.

ShellCraft doesn't print the commands themselves, the same as a bash script. You see each step's
output, and when the func finishes you get one muted line:

```
✓ recon: 4 steps, 2.3 s
```

or, if a step failed:

```
✗ recon: step 2 failed (queryDns example.com): …
```

The `…` is the failing command's own error message.

### Other ways to run it

| Command | What it does |
| --- | --- |
| `func run NAME ARGS` | Run it in your shell, as if typed |
| `func run -i NAME ARGS` | Isolated: afterwards your folder, variables, environment variables, aliases, theme, settings and `set` options are put back |
| `func run -b NAME ARGS` | In the background, as a job in the footer, always isolated. `jobs N` shows its output. Steps that change the shell, like `cd`, are refused there |
| `func run -q NAME ARGS` | Quiet this time, whatever the header says (`-v` shows everything) |
| `shellcraft func run NAME ARGS` | From bash or cron, without opening ShellCraft (see [below](#run-a-func-from-outside-shellcraft)) |

Use `-i` when you want to try a func that does `cd` or `env set` without it changing your session.
Use `-b` for a long func you want to keep running while you do something else.

## When a step fails

A func stops at the first step that fails, prints the `✗` line naming that step, and the func's
status (`$?` afterwards) is the failing step's status. The steps after it don't run.

A step fails when its command ends with a status other than 0. A loop is one step, and like in
bash its status is its last command's. So a `cat` that fails in the middle of a `for` loop shows its
error, the loop goes on, and the func carries on too. Put `set -e` in the func (or have it on in
your session) and the loop stops at that `cat`, which fails the step. See
[Stop on errors](scripting/errors.md#stop-on-errors-with-set--e).

Sometimes a failure is fine. To let one step fail without stopping the func, give it a fallback:

```
cat old-notes.txt || echo "no old notes, carrying on"
```

To handle a failure your own way, wrap the step in `try`. See
[Catch failures with try](scripting/errors.md#catch-failures-with-try).

### Stop early on purpose

To stop when something is wrong, such as a missing argument, make a step fail. A test that comes
out false does it:

```
# func: need
# usage: need DOMAIN
if [ -z "$1" ]; then echo "usage: need DOMAIN" >&2; false; fi
echo "checking $1"
```

`func run need` with no domain prints the usage line and
`✗ need: step 1 failed (…): exit status 1`, and stops there. `func run need example.com` goes on to
the next step.

For a missing argument there's a shorter way, as in bash. `${1:?MESSAGE}` stops with your message
when `$1` is empty, and the `:` command does nothing but read it:

```
: ${1:?usage: need DOMAIN}
echo "checking $1"
```

`func run need` then stops at step 1 with `1: usage: need DOMAIN`. See
[Defaults, lengths and trimming](scripting/variables-and-input.md#defaults-lengths-and-trimming).

### Common mistakes

`return` doesn't work in a func, because a func isn't a function. You'll see
`'return' outside a function`, and the func stops. Use a failing step as above.

`exit` ends ShellCraft itself, not only the func. At the prompt that closes your shell. It's useful
when you run the func from outside with `shellcraft func run`, where `exit 3` makes the exit code 3.

## Check it first

`func check NAME` reads the func and runs nothing. Run it after you edit a func, so you find typos
before the func is halfway through a job. It reports each line that won't parse (a `for` with no
`done`, a colon block indented wrongly) as an error, and each command that nothing answers to as a
warning:

```
recon:8: warning: unknown command 'queryDsn' (did you mean 'queryDns'?)
1 problem (1 warning)
```

The line number is the line in the func's file, header lines included, so it matches what you see
in `func edit`. A loop that never ends looks like this:

```
bad:2: missing 'done': the block that starts here never ends
```

A clean func says something like `ok: 13 lines, no problems`. The status is 0 when the func is
clean, 1 for warnings only and 2 for errors, so a script can check a func before it runs it.
`source -n FILE` does the same for any other file.

## Options

A func can take options like a command, with `getopts`. Use them when a func has a setting you
change only sometimes: a name, a "be loud" switch. This one has `-n NAME` and `-l` for loud:

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

Here's what each part does. The first step sets the defaults, so the func works with no options at
all. `"ln:"` lists the letters the func accepts; the `:` after `n` means `-n` takes a value. Each
time round the loop, `getopts` puts the next option letter in `$opt`, and the value of `-n` in
`$OPTARG`. When the options run out, the loop ends. `shift $((OPTIND - 1))` then drops the options,
so `$@` is only the words after them.

`greet -l -n Ana nice day` prints `hello Ana! nice day` and `HELLO Ana!`. For an option it
doesn't know, `getopts` prints `getopts: illegal option -- x` and the loop goes on. Each func run
starts with its own `$OPTIND` at 1. See
[Options like -v and -f FILE](scripting/functions.md#options-like--v-and--f-file) and
`man getopts`.

## Clean up when a step fails

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

The trap's command is in single quotes, so nothing in it runs until the func ends. The `!` in front
of `grep -q` turns "found a TODO" into a failure. So when a TODO is left, that step fails and the
func stops there, and `bundle.tmp` is still removed. You'll see `cleaned up` either way.

The trap belongs to the func and goes away when it ends. `trap '…' ERR` runs after a step fails,
and `trap '…' INT` on Ctrl-C. See [Clean up with trap](scripting/errors.md#clean-up-with-trap) and
`man trap`.

## Another example

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

It needs `system: yes` because `pytest` and `git` are programs on your computer, not ShellCraft
commands. With `output: quiet`, the test output stays hidden unless something fails, and the `@`
lines report progress. If `pytest` fails, the tag is never made.

## Run a func from outside ShellCraft

You can run a func from bash, a script or cron, without opening ShellCraft. That way one func does
the job both when you're at the prompt and when nobody is:

```bash
shellcraft func run backup notes
echo $?
```

ShellCraft starts with no banner and no prompt. It loads your rc file and startup files first, as
it does when you open it, then runs the func and exits. The func's output goes to stdout and its
errors to stderr, so `2> errors.txt` works as for any program.

The exit code is the func's status. `echo $?` shows `0` when every step worked and the failing
step's status otherwise. `exit N` in the func, the rc file or a startup file ends it with N. A
`trap '…' EXIT` in your rc file runs before ShellCraft exits, as it does when you leave the shell.

Put the shell's own options before `func`:

```bash
shellcraft --profile work --no-startup func run deploy staging
```

| Option | What it does |
| --- | --- |
| `--profile NAME` | Use that profile's funcs, settings and rc |
| `--no-startup` | Skip the rc file and the startup files. Good for cron, when the rc prints things |
| `--allow-system` | Let it run your system's programs, if `system_commands` is off |
| `-q` / `-v` after `run` | Quiet or show everything, as at the prompt. `-v` also prints the `✓` or `✗` summary line, on stderr |

The `✓` summary line doesn't show unless you ask with `-v`, so a func that works prints only its
own output. When a step fails without `-v`, you see that step's error message and nothing else.

`shellcraft func list`, `func show NAME`, `func check NAME` and `func path NAME` work the same way.

### Run it from cron

A cron line that runs a func every night at 2:00 and keeps its output:

```
0 2 * * * shellcraft --no-startup func run backup notes >> ~/backup.log 2>&1
```

`>> ~/backup.log` adds each night's output to the log, and `2>&1` puts the error messages in the
same file, so you can see later why a night failed.

### If it doesn't work

An unknown func exits with 127 and lists the funcs it found:
`shellcraft: func run: no func 'nosuch' (funcs: backup, recon)`. Check the name, and with
`--profile`, that the func is in that profile or your main folder.

If cron says `shellcraft: command not found`, cron's PATH is shorter than your terminal's. Use the
full path that `command -v shellcraft` prints in your terminal.

`-b` doesn't work here, since there is no shell to keep the job in. Put `&` after the command in
bash instead.

If you run it from a terminal, `read` and `input` can still ask you things. From cron or a pipe
they can't, and fail as they do in any script with no terminal. A func meant for cron should take
what it needs as arguments.

## Make and change funcs from the prompt

If you'd rather not edit files, `func` does it all from the prompt. A func made this way lasts
until you leave ShellCraft unless you save it.

| Command | What it does |
| --- | --- |
| `func new NAME` | Asks a few questions, then the steps one per line. An empty line ends |
| `func new NAME -e` | Opens your editor instead (see [the editor](#the-editor)) |
| `func new NAME 'CMD' 'CMD'` | All at once. Add `-d DESC`, `--system`, `--quiet`, `--command` |
| `--save` | Keep it on disk. Without it, the func lasts until you leave ShellCraft |
| `-g` | With `--save`: keep it with your main setup instead of the active profile |
| `func save NAME [-g]` | Put a func you made without `--save` on disk |
| `func` or `func list` | The funcs you can run |
| `func show NAME` | Its header and numbered steps (also `func view`) |
| `func check NAME` | Read it and run nothing: every line that won't parse and every unknown command, as `NAME:LINE: …` |
| `func edit NAME` | Open it in your editor |
| `func add NAME 'CMD'`, `insert NAME N 'CMD'`, `rm NAME N`, `move NAME FROM TO` | Change the steps |
| `func set NAME FIELD VALUE` | Change a header line: `description`, `system`, `output`, `command`, `usage` |
| `func rename OLD NEW`, `copy SRC DST [-g]`, `delete NAME [-y]` | Manage them |
| `func export NAME FILE` (or `--all`), `func import FILE [-f] [-g]` | Share them as one JSON file |
| `func path NAME` | Where its file is |

For example, to build the recon func without opening an editor:

```
func new recon --save -d "domain recon" --command
func add recon 'queryDns $1'
func show recon
```

The single quotes around `queryDns $1` keep the `$1` as text, so it's filled in when the func runs,
not when you add the step.

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
