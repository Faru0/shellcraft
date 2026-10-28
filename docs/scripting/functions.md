# Functions

← [Scripting guide](README.md)

A function gives a name to a few lines of script, so you can run them again by typing that name.
It works like any other command: it takes arguments, it can be piped, and it can succeed or fail.
This page shows you how to write one, hand it values, get a result back, and keep it for every
session.

## Make a function and call it

Write the name, `()`, and the commands between `{` and `}`. Then call it by its name:

<!-- run -->
```
hello() {
  echo "Hello!"
  echo "Today is a good day to write a function."
}
hello
```

<!-- output -->
```
Hello!
Today is a good day to write a function.
```

The `{` goes on the first line, and the `}` goes on a line of its own at the end. A short function
fits on one line, with a `;` after the last command and a space before the `}`:

<!-- run -->
```
greet() { echo "Hi there"; }
greet
```

<!-- output -->
```
Hi there
```

Defining a function doesn't run it. Its commands run each time you call it.

## Give it arguments

The words after the function's name are its arguments. Inside, `$1` is the first, `$2` the second,
and so on. `$#` is how many there are, and `$@` is all of them:

<!-- run -->
```
introduce() {
  echo "Name: $1"
  echo "Role: $2"
  echo "($# arguments: $@)"
}
introduce Ada engineer
```

<!-- output -->
```
Name: Ada
Role: engineer
(2 arguments: Ada engineer)
```

Quote an argument that has spaces, so it arrives as one: `introduce "Ada Lovelace" engineer`.

To go through every argument, loop over `"$@"`, with the quotes. Each argument stays whole:

<!-- run -->
```
list() {
  for item in "$@"; do
    echo "- $item"
  done
}
list milk "brown bread" eggs
```

<!-- output -->
```
- milk
- brown bread
- eggs
```

`shift` drops the first argument, so `$2` becomes `$1`. It's handy when the first argument is
special and the rest are a list:

<!-- run -->
```
tag() {
  label=$1
  shift
  for file in "$@"; do echo "$label: $file"; done
}
tag urgent report.txt notes.txt
```

<!-- output -->
```
urgent: report.txt
urgent: notes.txt
```

## Default values

`${1:-something}` is the first argument, or `something` when there isn't one:

<!-- run -->
```
greet() {
  local name=${1:-friend}
  echo "Hello, $name"
}
greet
greet Sam
```

<!-- output -->
```
Hello, friend
Hello, Sam
```

## Check the arguments

When a function can't work without an argument, check for it first. Print a message to the error
stream with `>&2`, and stop with `return 1`:

<!-- run -->
```
backup() {
  if [ -z "$1" ]; then
    echo "usage: backup FILE" >&2
    return 1
  fi
  if [ ! -f "$1" ]; then
    echo "backup: $1 doesn't exist" >&2
    return 1
  fi
  cp "$1" "$1.bak"
  echo "saved $1.bak"
}
echo "some notes" > notes.txt
backup
backup missing.txt
backup notes.txt
```

<!-- output -->
```
usage: backup FILE
backup: missing.txt doesn't exist
saved notes.txt.bak
```

The short form fits on one line, with `||`. It reads "the argument is there, or stop":

<!-- run -->
```
open_project() {
  [ -n "$1" ] || return 1
  echo "opening $1"
}
open_project || echo "open_project needs a name"
open_project website
```

<!-- output -->
```
open_project needs a name
opening website
```

## local variables

A variable you set in a function is the shell's own, so it's still there after the function ends.
`local` makes it belong to that call only, and puts back the old value when the function returns.
Use `local` for anything that's only needed inside:

<!-- run -->
```
name=outside
show() {
  local name=inside
  echo "in the function: $name"
}
show
echo "after it: $name"
```

<!-- output -->
```
in the function: inside
after it: outside
```

## Succeed or fail with return

`return 0` means it worked, and any other number means it didn't. Without a `return`, a function
gives back the status of its last command. That makes a function usable as a condition:

<!-- run -->
```
is_weekend() {
  case $1 in
    Sat|Sun) return 0 ;;
    *) return 1 ;;
  esac
}
for day in Fri Sat; do
  if is_weekend $day; then echo "$day: weekend"; else echo "$day: work day"; fi
done
is_weekend Sun && echo "Sun: time off"
```

<!-- output -->
```
Fri: work day
Sat: weekend
Sun: time off
```

## Give back a value

`return` only carries a number from 0 to 255. To give back text, or a bigger number, `echo` it and
catch it with `$( )`:

<!-- run -->
```
add() {
  echo $(( $1 + $2 ))
}
total=$(add 1200 34)
echo "the total is $total"

slug() {
  echo "$1" | tr "A-Z " "a-z-"
}
echo "file name: $(slug "My Big Report").md"
```

<!-- output -->
```
the total is 1234
file name: my-big-report.md
```

Everything the function prints becomes the value, so leave out other `echo` lines when you use it
this way.

## Functions that call functions

A function can call any other function, as long as both are defined by the time it runs:

<!-- run -->
```
log() {
  echo "[$1] $2"
}
deploy() {
  log info "deploying $1"
  log info "done with $1"
}
deploy website
```

<!-- output -->
```
[info] deploying website
[info] done with website
```

## Ask the user from a function

`read` works inside a function. A function that runs on its own line shows each command's output
as it goes, so its questions come in the right order:

<!-- run: ["Ada", "y"] -->
```
signup() {
  read -p "Your name: " who
  read -p "Get the newsletter? (y/n) " news
  case $news in
    [yY]*) echo "Thanks $who, you're signed up" ;;
    *) echo "Thanks $who, no newsletter" ;;
  esac
}
signup
```

<!-- output -->
```
Your name: Ada
Get the newsletter? (y/n) y
Thanks Ada, you're signed up
```

`input()` works inside a function too: `answer = input("Your name: ")`.

## Python style: def

`def` writes a function the Python way: the arguments are named in the brackets, the line ends in
`:`, and the body is indented under it. A parameter with `=` has a default; one without is
required:

<!-- run -->
```
def greet(name, greeting="Hello"):
    if name == "Ada":
        echo "Welcome back, Ada"
    else:
        echo "$greeting, $name"
greet Ada
greet Sam
greet Sam Hi
```

<!-- output -->
```
Welcome back, Ada
Hello, Sam
Hi, Sam
```

Each parameter is a `local` variable, so the shell's own `name` isn't touched. Calling `greet` with
no arguments stops with `greet: missing argument 'name'`. Inside, `if` and `for` take Python
conditions, and `$1`, `$#` and `$@` still work. `return`, `echo` with `$( )`, `read` and `input()`
work the same as in a bash-style function:

<!-- run -->
```
def add(a, b=0):
    echo $((a + b))
total=$(add 1200 34)
echo "the total is $total"

def is_weekend(day):
    case $day:
        "Sat" | "Sun":
            return 0
        _:
            return 1
is_weekend Sun && echo "Sun: time off"
```

<!-- output -->
```
the total is 1234
Sun: time off
```

## Stop on errors with set -e

Normally a command that fails is reported and the next line runs anyway. When later steps depend
on earlier ones, you want the script to stop instead. `set -e` does that. After it, the first
command that fails stops the function it is in, the line that called it, and the rest of the
file:

<!-- run -->
```
set -e
check_settings() {
  echo "checking"
  [ -f settings.conf ]
  echo "settings found"
}
check_settings
echo "this line doesn't run"
```

<!-- output -->
```
checking
✗ example.sc:7: check_settings (set -e: the rest of the file was skipped)
```

`set -e` never closes ShellCraft. It stops what is running: the line you typed at the prompt, a
function, a `source`d file, your rc file, a startup file or a func. `$?` keeps the status of the
command that failed. `set +e` turns it off again.

A failure doesn't stop anything when it is a question rather than an error: the test of an `if`,
`while` or `until`, a command before `&&` or `||`, and a command after `!`. So the usual checks
still work:

<!-- run -->
```
set -e
[ -f settings.conf ] && echo "found it"
if [ -f settings.conf ]; then
  echo "using settings.conf"
else
  echo "no settings.conf, using the defaults"
fi
echo "still running"
```

<!-- output -->
```
no settings.conf, using the defaults
still running
```

The same goes for a function used as a test: `if check_settings; then` runs the whole function,
because its failure is the answer you asked for.

`def` functions behave the same way:

<!-- run -->
```
set -e
def check_settings():
    echo "checking"
    [ -f settings.conf ]
    echo "settings found"

check_settings
echo "this line doesn't run"
```

<!-- output -->
```
checking
✗ example.sc:7: check_settings (set -e: the rest of the file was skipped)
```

### Other options

`set` has three more options that help when you write a script:

| Option | What it does |
| --- | --- |
| `set -u` | Using a variable that isn't set is an error that stops the script, so a typo like `$fiel` doesn't quietly become nothing. `${name:-default}` is still fine |
| `set -x` | Show each command, with its variables filled in, before it runs. Good for finding out what a script really does |
| `set -o pipefail` | A pipe fails when any part of it fails, not only the last one |

You can turn on several at once, as in bash: `set -euo pipefail`. `set -o` shows which ones are
on, and `+` turns one off (`set +x`).

With `set -u`, a typo stops the script with a message:

```
set -u
file=notes.txt
echo "saving $fiel"         # fiel: unbound variable
```

`set -x` prints each command to the error stream, starting with `+`:

<!-- run -->
```
set -x
name=world
echo "hello $name"
set +x
echo "no trace"
```

<!-- output -->
```
+ name=world
+ echo 'hello world'
hello world
+ set +x
no trace
```

`grep` finds nothing here, but `wc` works, so the pipe succeeds until `pipefail` is on:

<!-- run -->
```
printf "apple\nbanana\n" | grep cherry | wc -l
echo "status: $?"
set -o pipefail
printf "apple\nbanana\n" | grep cherry | wc -l
echo "status: $?"
```

<!-- output -->
```
0
status: 0
0
status: 1
```

`set -- A B C` replaces the arguments: after it, `$1` is `A` and `$#` is 3. See `man set` for
everything.

## Clean up with trap

A function that makes a temporary file should remove it, even when a step fails halfway. `trap`
runs a command when the function ends, however it ends:

<!-- run -->
```
set -e
make_report() {
  local tmp=report.tmp
  trap 'rm -f $tmp; echo "removed $tmp"' EXIT
  echo "draft" > $tmp
  echo "writing the report"
  grep -q "final" $tmp
  echo "report done"
}
make_report
```

<!-- output -->
```
writing the report
removed report.tmp
✗ example.sc:10: make_report (set -e: the rest of the file was skipped)
```

`grep` fails, `set -e` stops the function, and the `EXIT` trap still removes the file. The trap
runs inside the function, so it sees its `local` variables and `$1`. Put the command in single
quotes, so `$tmp` is filled in when the trap runs, not when you set it.

A trap belongs to the function that set it and goes away when the function ends. A file you run
with `source` and a func have their own traps too. A trap set at the prompt, in your rc file or
in a startup script belongs to the session: its `EXIT` trap runs when you leave ShellCraft.

There are three signals:

| Signal | When the command runs |
| --- | --- |
| `EXIT` | When the function, file or func ends: at the end, on `return`, when `set -e` stops it, on Ctrl-C |
| `INT` | When you press Ctrl-C. Then it stops as usual |
| `ERR` | After a command fails, where `set -e` would stop (not in an `if` test, before `&&` or `\|\|`, or after `!`) |

An `ERR` trap is handy for logging. Inside it, `$?` is the failing command's status:

<!-- run -->
```
trap 'echo "failed with status $?"' ERR
echo "step one"
grep -q "needle" /dev/null
echo "step two"
if grep -q "needle" /dev/null; then echo "found"; fi
echo "done"
```

<!-- output -->
```
step one
failed with status 1
step two
done
```

The `grep` inside the `if` is a question, so it doesn't count as an error. When a function has no
trap of its own for `INT` or `ERR`, the one of whatever called it runs.

`trap` works the same in a `def` function:

<!-- run -->
```
def backup(name):
    trap 'echo "backup of $name finished"' EXIT
    echo "copying $name"
    return 0

backup notes.txt
```

<!-- output -->
```
copying notes.txt
backup of notes.txt finished
```

`trap` on its own lists the traps set here, `trap - EXIT` removes one, and `trap '' INT` sets one
that does nothing. See `man trap`.

## Keep your functions

A function lasts until you leave ShellCraft. To have it in every session, put it in
`~/.shellcraft/rc`, or in a file in `~/.shellcraft/startup/`, such as `20-functions.sc`. Then it's
ready at the prompt each time the shell starts. See [Startup](../startup.md).

These commands help you keep track of them:

| Command | What it does |
| --- | --- |
| `declare -f` | Show every function and its body |
| `declare -F` | Only the names |
| `type NAME` | Say whether NAME is a function, and show it |
| `unset -f NAME` | Forget a function |

<!-- run -->
```
greet() { echo hi; }
type greet
unset -f greet
type greet
```

<!-- output -->
```
greet is a function
greet() { echo hi; }
greet: not found
```

A function with the same name as a command takes its place, so you can change what a command does.
`\NAME` runs the real command, skipping your function:

```
ls() { \ls -l "$@"; }        # ls now always lists the long way
```

AI clients that use your shell over MCP can't see or run your functions.

## Functions, aliases or funcs?

All three give a name to something you type often. Use the one that fits:

| | Best for | Arguments | Lasts |
| --- | --- | --- | --- |
| [Alias](../aliases-and-history.md) | A shorter name for one command with its options: `alias ll='ls -l'` | Added at the end | Saved in `~/.shellcraft/aliases` |
| Function | Logic: `if`, loops, `read`, several steps that use `$1`; `def` for named arguments | `$1`, `$2`, `$@`, anywhere, or named ones with `def` | The session; put it in your rc file to keep it |
| [Func](../funcs.md) | A routine you run on demand: a checklist, a recon pass, setting up a project | `$1`, `$2`, `$@` | A file in `~/.shellcraft/funcs/`; it stops at the first step that fails |
