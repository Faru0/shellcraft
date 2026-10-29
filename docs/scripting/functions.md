# Functions

← [Scripting guide](README.md)

A function gives a name to a few lines of script, so you can run them again by typing that name.
It works like any other command: it takes arguments, it can be piped, and it can succeed or fail.
This page is for you when you catch yourself typing the same few commands again and again. After
reading it you'll be able to write a function in bash style or Python style, hand it values, get a
result back, give it options like `-v`, and keep it for every session.

When a step inside a function can fail, [Errors and cleanup](errors.md) shows you how to stop,
clean up and carry on.

## Make a function and call it

Say there are two lines you print at the start of every session. Rather than type them each time,
give them a name. Write the name, `()`, and the commands between `{` and `}`. Then call it by its
name:

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

Defining a function doesn't run it. ShellCraft reads the lines between the braces and keeps them
under that name. Its commands run each time you call it, and only then.

That also means the definition has to come first. Calling a function on a line above the one that
defines it fails with `command not found: later`, because at that moment there's no `later` yet.
In a file, put your functions near the top and use them below.

If you forget the space after `{`, as in `greet() {echo hi}`, ShellCraft tells you how it should
look: `greet: a function body goes in braces: greet() { echo hi; }`.

## Give it arguments

A function that does the same thing every time is a start, but most of the time you want to tell
it what to work on: which file, which name, which server. The words after the function's name are
its arguments. Inside, `$1` is the first, `$2` the second, and so on. `$#` is how many there are,
and `$@` is all of them:

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

The arguments are split at spaces, the same way as for any command. So `introduce Ada Lovelace
engineer` would give `Lovelace` as the role. Quote an argument that has spaces, so it arrives as
one: `introduce "Ada Lovelace" engineer`.

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

Without the quotes, `for item in $@` splits every argument again at its spaces, and you'd get
`brown` and `bread` on two lines. That's the most common slip with `$@`, so make the quotes a
habit.

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

Some arguments have an obvious answer when nobody gives one. `${1:-something}` is the first
argument, or `something` when there isn't one:

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

The `:-` reads "or, if it's empty". It works for any variable, not only arguments:
`${editor:-nano}`. [Variables and input](variables-and-input.md) has more of these.

## Check the arguments

When a function can't work without an argument, check for it first. Otherwise it goes ahead with
an empty name and fails somewhere less clear, or does something you didn't mean, like copying a
file to `.bak`. Print a message to the error stream with `>&2`, and stop with `return 1`:

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

The first call has no argument, so `[ -z "$1" ]` is true ("`$1` is empty") and the function
prints how to use it. The second gets a name, but `[ ! -f "$1" ]` finds there's no such file. Only
the third gets past both checks and makes the copy.

Two details are worth knowing. `>&2` sends the message to the error stream, which is where
problems belong: if someone pipes your function's output into another command, the warning still
shows on the screen instead of going down the pipe. And `return 1` ends the function there and
then, with a status that says it failed, so the lines after it don't run.

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
That surprises people. If a function sets `name` for its own use, it quietly changes the `name`
your rc file set too.

`local` makes a variable belong to that call only, and puts back the old value when the function
returns. Use `local` for anything that's only needed inside:

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

Inside `show`, `name` is `inside`. When `show` returns, the shell's own `name` comes back as it
was. Take away the word `local` and the last line would say `after it: inside`. You can declare
several at once: `local verbose=no file=notes.txt`.

## Succeed or fail with return

Every command ends with a number, its exit status: `0` means it worked, and any other number
means it didn't. A function gets one too. `return 0` says it worked, and `return 1` (or any other
number) says it didn't. Without a `return`, a function gives back the status of its last command.

That makes a function usable as a condition, the same as `[ -f notes.txt ]` or `grep -q`:

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

`is_weekend` prints nothing. It only answers yes or no through its status, and `if` and `&&` read
that answer. [Exit status and $?](errors.md#exit-status-and-) explains the numbers in more detail.

## Give back a value

Here's the part that catches most newcomers. In a bash-style function, `return` isn't how you
hand back a result. It only carries a status, a number from 0 to 255. `return 300` gives you
`44`, because the number wraps around past 255, and a text like `return hello` isn't a number at
all.

To give back text, or a bigger number, print it with `echo` and catch it with `$( )`. `$( )` runs
the command and puts whatever it printed in its place. A Python-style `def` can also
[return the value](#give-back-a-value-with-return).

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

`total=$(add 1200 34)` runs `add`, catches the `1234` it printed, and stores it in `total`. In
the second part, `$( )` is used right inside a longer text, so the file name is built in one line.

Everything the function prints becomes the value, so leave out other `echo` lines when you use it
this way. If you want to show a message as well, send it to the error stream with `>&2`, which
`$( )` doesn't catch.

Mind the `$( )`. `who=name` stores the word `name`. `who=$(name)` runs the function `name` and
stores what it printed.

## Functions that call functions

Once you have a few functions, you'll want them to use each other. A small `log` function that
formats a line is a good example: every other function can call it, and when you want a different
format you change it in one place. A function can call any other function, as long as both are
defined by the time it runs:

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

A function can ask questions, which is handy for a setup step you run now and then. `read` works
inside a function. A function that runs on its own line shows each command's output as it goes,
so its questions come in the right order:

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

`read -p "Your name: " who` shows the prompt and puts what you type in `who`. `input()` works
inside a function too: `answer = input("Your name: ")`. Both are covered in
[Variables and input](variables-and-input.md).

## Python style: def

If you know some Python, or you'd rather give arguments real names than count `$1` and `$2`,
write the function with `def`. The arguments are named in the brackets, the line ends in `:`, and
the body is indented under it. A parameter with `=` has a default; one without is required:

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

You call it the same way as a bash-style function, with the arguments after the name and no
brackets: `greet Sam Hi`. Each argument fills the next parameter, so `name` is `Sam` and
`greeting` is `Hi`. When there's no second argument, `greeting` falls back to `Hello`.

Each parameter is a `local` variable, so the shell's own `name` isn't touched. Calling `greet` with
no arguments stops with `greet: missing argument 'name' (usage: greet NAME)`. Inside, `if` and
`for` take Python conditions, and `$1`, `$#` and `$@` still work. `echo` with `$( )`, `read` and
`input()` work the same as in a bash-style function.

Notice the two ways of reading a parameter. In a Python condition like `name == "Ada"`, you write
the bare name. In a command like `echo "$greeting, $name"`, you write `$` in front, as for any
shell variable. The rule is the same everywhere in the Python style: Python code uses names,
commands use `$`. [Bash style and Python style](python-style.md) has the full picture.

### Give back a value with return

In a `def`, `return` takes a Python value: `return int(a) + int(b)`, `return "Hello, " + name`,
`return True`. When you call the function as a command, ShellCraft turns that value into what a
command gives back:

- a number from 0 to 255 is the status, so `return 0` and `return 1` work as in bash;
- `True` is status 0 and `False` is status 1;
- `None` is status 0;
- anything else (text, a bigger number, a decimal) is printed, with status 0.

A bare `return` gives the status of the last command. Anything the function echoed comes first.
For a check you use in an `if`, write `return True` or `return False`: it reads well, and it
works both ways.

So in a `def`, the printing and returning problem from [Give back a value](#give-back-a-value)
goes away. `return "Hello, " + name` prints the text when you call the function on its own, and
`$(greet Sam)` catches it like any output:

<!-- run -->
```
def greet(name):
    return "Hello, " + name
greet Ada
echo "[$(greet Sam)]"

def is_weekend(day):
    return day in ("Sat", "Sun")
is_weekend Sun && echo "Sun: time off"
is_weekend Mon || echo "Mon: work day"
```

<!-- output -->
```
Hello, Ada
[Hello, Sam]
Sun: time off
Mon: work day
```

### Call a function from Python

Sometimes you want to use a function's answer inside Python code: a condition, `name = …`, or
another `return`. There you can call any function by name, with brackets: `add(1, 2)`. A number
you pass goes in as a number, and text as text. What you get back is the function's `return` value, as a Python value. A function without `return EXPR` gives what
it printed instead, without the last new line, like `$( )`. So a bash function that echoes works
too. Its output is caught, not shown, and its status goes to `$?`.

<!-- run -->
```
def add(a, b):
    return int(a) + int(b)

def is_weekend(day):
    return day in ("Sat", "Sun")

slug() {
  echo "$1" | tr "A-Z " "a-z-"
}

def report(title, a, b):
    total = add(a, b) * 2
    file = slug(title) + ".md"
    echo "$file: $total"

report "My Big Report" 1200 34
for -py day in Fri Sat:
    if is_weekend(day):
        echo "$day: weekend"
    else:
        echo "$day: work day"
```

<!-- output -->
```
my-big-report.md: 2468
Fri: work day
Sat: weekend
```

In `report`, `add(a, b)` gets back the number `1234` from `return`, so `* 2` does arithmetic on
it. `slug(title)` calls a bash-style function, which has no `return` value, so you get what it
printed, `my-big-report`, and `+ ".md"` adds to that text. In the loop, `is_weekend(day)` gives
`True` or `False`, which is what `if` wants.

Two things to watch for. In Python code you use brackets, `add(a, b)`. On a command line you
don't, `add 1 2`. And arguments on a command line are always text: `report` was called that way,
so `a` and `b` are `"1200"` and `"34"`, which is why `add` uses `int(a)`. Without it, `a + b`
would join them into `"120034"`.

Only functions defined in the shell can be called this way, plus the built-in ones (`len`,
`int`, `exists` and the rest, see [Python style](python-style.md#comparing-text-and-numbers)).
Calling a name that isn't a function stops with `NAME is not a function`, followed by the list of
built-in ones. Keyword arguments (`add(a=1)`) aren't supported. A function can call itself, up to 100 calls deep.

## Options like -v and -f FILE

Commands take options: `ls -l`, `grep -i word`. Once a function has more than one or two
arguments, options make it easier to call, because you don't have to remember the order. Your
functions can take them too, with `getopts`. You tell it the letters to look for, and each time
you call it, it gives you the next option. A letter followed by `:` takes a value, so `"vf:"`
means `-v` and `-f FILE`:

<!-- run -->
```
backup() {
  local verbose=no file=notes.txt
  while getopts "vf:" opt; do
    case $opt in
      v) verbose=yes ;;
      f) file=$OPTARG ;;
      \?) echo "usage: backup [-v] [-f FILE] [NAME...]"; return 2 ;;
    esac
  done
  shift $((OPTIND - 1))
  echo "verbose=$verbose file=$file"
  echo "the rest: $@"
}
backup -v -f todo.txt monday tuesday
backup -vftodo.txt friday
backup -x
echo "status: $?"
```

<!-- output -->
```
verbose=yes file=todo.txt
the rest: monday tuesday
verbose=yes file=todo.txt
the rest: friday
getopts: illegal option -- x
usage: backup [-v] [-f FILE] [NAME...]
status: 2
```

Here is what happens:

- `opt` gets the option's letter, and `$OPTARG` gets its value (`todo.txt`).
- `getopts` fails when the options run out, which ends the `while` loop. The options stop at the
  first word that doesn't start with `-`, or at `--`.
- `$OPTIND` is the number of the next argument to read. `shift $((OPTIND - 1))` drops the options,
  so `$1`, `$2`… and `$@` are what came after them.
- Options can be grouped (`-vf todo.txt`), and the value can be glued on (`-ftodo.txt`).
- An option you didn't list gives `?` and a message. Write the `case` arm as `\?)`: a bare `?)`
  would match any single letter.

To print your own messages instead, start the letters with `:`. Then an unknown option gives `?`
with the letter in `$OPTARG`, and a missing value gives `:`:

<!-- run -->
```
convert() {
  while getopts ":o:" opt; do
    case $opt in
      o) echo "output: $OPTARG" ;;
      :) echo "-$OPTARG needs a file name" ;;
      \?) echo "convert has no -$OPTARG option" ;;
    esac
  done
}
convert -q -o
```

<!-- output -->
```
convert has no -q option
-o needs a file name
```

Each function call starts with its own `$OPTIND` at 1, and the caller's comes back when it
returns, so a function that reads options can call another one that does.

In a `def` function, the same `while getopts` loop works as a bash line, or in Python style.
The condition isn't Python, so it runs as a command:

<!-- run -->
```
def backup():
    verbose=no
    while -py getopts "vf:" opt:
        match opt:
            case "v":
                verbose=yes
            case "f":
                echo "file: $OPTARG"
            case _:
                return 2
    shift $((OPTIND - 1))
    echo "verbose=$verbose, the rest: $@"

backup -v -f todo.txt monday
```

<!-- output -->
```
file: todo.txt
verbose=yes, the rest: monday
```

In `match` (and `case -py`), use `case _:` for the mistakes: a quoted `"?"` is a pattern there
and matches any single letter. See `man getopts`.

## When a step fails

A function stops at `return`, and otherwise runs every line, even after one of them fails. When
the later lines depend on the earlier ones, you want more than that.
[Errors and cleanup](errors.md) shows you how:

- [Stop on errors with set -e](errors.md#stop-on-errors-with-set--e), so the first failure stops
  the function and the script.
- [Clean up with trap](errors.md#clean-up-with-trap), so a temporary file is removed however the
  function ends.
- [Catch failures with try](errors.md#catch-failures-with-try), to handle a failure and carry on,
  in bash style or Python style.

## Keep your functions

A function you type at the prompt lasts until you leave ShellCraft. To have it in every session, put it in
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

All three give a name to something you type often, so it's fair to wonder which to use. In
short: an alias for a shorter spelling of one command, a function as soon as you need logic or
arguments in the middle, and a func for a routine you run on purpose now and then. The table has
the details:

| | Best for | Arguments | Lasts |
| --- | --- | --- | --- |
| [Alias](../aliases-and-history.md) | A shorter name for one command with its options: `alias ll='ls -l'` | Added at the end | Saved in `~/.shellcraft/aliases` |
| Function | Logic: `if`, loops, `read`, several steps that use `$1`; `def` for named arguments | `$1`, `$2`, `$@`, anywhere, or named ones with `def` | The session; put it in your rc file to keep it |
| [Func](../funcs.md) | A routine you run on demand: a checklist, a recon pass, setting up a project | `$1`, `$2`, `$@` | A file in `~/.shellcraft/funcs/`; it stops at the first step that fails |
