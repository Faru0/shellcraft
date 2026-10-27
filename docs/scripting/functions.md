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
