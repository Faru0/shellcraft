# Complete examples

← [Scripting guide](README.md)

This page is for when you'd rather start from something that works than from a blank file. Each
example is a whole script you can copy into your rc file, a startup file or a func, then change to
fit. Each one starts with the situation it's for, shows the script, and then walks through how it
works, so you know which lines to change.

They put together the pieces from the other pages of this guide. Most are in bash style, and the
last three are in Python style. [Bash style and Python style](python-style.md) shows how to turn one
into the other. The marked ones run in the test suite, so they work as shown.

## A friendly rc file

**The situation.** Every time you open ShellCraft you do the same few things: go to your projects
folder, set your editor, and try to remember what you meant to do today. Your `~/.shellcraft/rc`
runs every time the shell starts, so it can do all that for you.

This one says hello by the time of day, sets up a few things, shows your reminders, and defines two
functions you can use at the prompt:

```
# ~/.shellcraft/rc

# ── Say hello ──────────────────────────────────────────
hour=$(date +%H)
if [ $hour -lt 12 ]; then
  echo "Good morning!"
elif [ $hour -lt 18 ]; then
  echo "Good afternoon!"
else
  echo "Good evening!"
fi

# ── Where and how ──────────────────────────────────────
[ -d ~/projects ] && cd ~/projects
export EDITOR=nano                 # quiet; `env set` says what it did

# ── Reminders, one per line in a plain text file ───────
if [ -s ~/.shellcraft/reminders.txt ]; then
  echo "Reminders:"
  cat ~/.shellcraft/reminders.txt
fi

# ── Functions for the prompt ───────────────────────────
mkcd() {
  [ -n "$1" ] || return 1
  mkdir -p "$1" && cd "$1"
}

remind() {
  echo "$*" >> ~/.shellcraft/reminders.txt
  echo "saved: $*"
}
```

**How it works.** Lines starting with `#` are comments. ShellCraft skips them, so use them to
label the parts of your file.

`date +%H` prints the hour from 00 to 23, and `$( )` stores it in `hour`. The `if` then compares it
with `-lt`, "less than": before 12 is morning, before 18 is afternoon, and anything else is evening.
`elif` is short for "else if".

`[ -d ~/projects ] && cd ~/projects` only moves you if the folder exists. On a machine without it,
the test fails, `&&` skips the `cd`, and you don't get an error every time the shell starts.
`export` makes `EDITOR` an environment variable, so programs you start can see it.

`-s` in the reminders test means "the file exists and isn't empty". So the `Reminders:` heading
only shows when there's something under it.

The two functions are new commands for the prompt. `mkcd` takes a folder name as `$1`. If you
forget it, `[ -n "$1" ] || return 1` stops the function with a failing status. Otherwise it makes
the folder, with `-p` so a folder that already exists isn't an error, and moves into it. `remind`
uses `$*`, which is every word you typed after the function's name, as one piece of text. `>>` adds
it as a new line at the end of the reminders file.

At the prompt, `remind call the bank` adds a line to the list you see at every start, and
`mkcd new-app` makes a folder and goes into it.

To make it yours, change the greetings, the folder in the `cd` line, and the editor. There's more
on functions in [Functions](functions.md).

## Ask which project to work on

**The situation.** You switch between a few projects, and each morning you want the shell to start
in the right one. You'd like it to ask, rather than you typing `cd` every time.

This is a pair of startup files. The first asks which project you want with a numbered menu. The
second uses the answer. They share variables because they run in the same shell, in name order.

`~/.shellcraft/startup/10-project.sc`:

<!-- run: ["2"] -->
```
PS3="Which project today? "
select project in website api docs none; do
  [ -n "$project" ] && break
  echo "pick a number from the list"
done
```

<!-- output -->
```
1) website
2) api
3) docs
4) none
Which project today? 2
```

`~/.shellcraft/startup/20-go.sc`:

```
if [ "$project" != none ]; then
  export PROJECT=$project
  [ -d ~/work/$project ] && cd ~/work/$project
  echo "working on $project"
fi
```

**How it works.** `PS3` is the question `select` asks. `select` prints the four choices with
numbers and waits. When you type `2`, it puts `api` in `project`. `[ -n "$project" ]` checks that
the answer was one of the numbers, because anything else leaves `project` empty. If it's set,
`break` ends the menu. If not, the loop asks again.

The second file starts with the `project` the first one set. If you picked `none`, it does nothing.
Otherwise `export PROJECT=$project` makes the name available to modules and programs, the `cd`
moves you into the project folder if it exists, and the `echo` confirms it.

The numbers at the start of the file names, `10-` and `20-`, are only there to set the order. Leave
gaps, so you can add a `15-something.sc` later without renaming anything. To use it, change the
list after `in` to your own projects, and `~/work` to where they live.

## Ask before doing something risky

**The situation.** Some commands you want to think twice about: deleting logs, restarting a server,
pushing to production. A `confirm` function you can put in front of any command asks a yes or no
question first, and only lets the command run on yes. Keep it in your rc file and use it with `&&`:

<!-- run: ["n", "yes"] -->
```
confirm() {
  read -p "${1:-Are you sure?} (y/n) " answer
  case $answer in
    [yY]|[yY][eE][sS]) return 0 ;;
    *) echo "cancelled"; return 1 ;;
  esac
}

confirm "Delete the old logs?" && echo "deleting the logs"
confirm "Restart the server?" && echo "restarting"
```

<!-- output -->
```
Delete the old logs? (y/n) n
cancelled
Restart the server? (y/n) yes
restarting
```

**How it works.** The question is the function's first argument, `$1`. `${1:-Are you sure?}` uses
it, or falls back to `Are you sure?` when you call `confirm` on its own.

The `case` accepts `y`, `Y`, and `yes` in any mix of capitals: `[yY][eE][sS]` is three letters,
each one either small or capital. Anything else, including pressing Enter, prints `cancelled`.

The trick is the `return`. `return 0` means success and `return 1` means failure, the same exit
status every command has. `&&` runs the command after it only when the one before it succeeded. So
`confirm "…" && something` runs `something` only on a yes. This is the difference between printing
and returning: `echo "cancelled"` is for you to read, and `return 1` is what tells `&&` to stop.

In real use, the command after `&&` is the risky one: `confirm "Delete the old logs?" && rm *.log`.

## Try again until it works

**The situation.** A server is restarting, a network link is flaky, or a service takes a moment to
come up. The first try fails, but the third works. Rather than running the command by hand until it
works, have a loop retry it a few times, waiting between tries. The test here fails twice, then
works:

<!-- run -->
```
attempt=0
until [ $attempt -ge 3 ]; do
  attempt=$((attempt + 1))
  if [ $attempt -eq 3 ]; then
    echo "attempt $attempt: connected"
    break
  fi
  echo "attempt $attempt: no answer, trying again"
  sleep 0.1
done
```

<!-- output -->
```
attempt 1: no answer, trying again
attempt 2: no answer, trying again
attempt 3: connected
```

**How it works.** `attempt` counts the tries. `until [ $attempt -ge 3 ]` keeps going until it has
tried three times, so a check that never works doesn't loop forever. Each round adds one to
`attempt` first, then tries. On success, the `if` prints a message and `break` leaves the loop
straight away. On failure, it says so and `sleep` waits before the next round.

Swap the `if` test for the real check, such as `if curl -s https://example.com > /dev/null; then`,
and the `sleep` for a sensible wait, like `sleep 5`. To do something when every try failed, set
`ok=""` before the loop and `ok=yes` right before the `break`. After `done`,
`[ "$ok" = yes ] || echo "gave up after $attempt tries"` tells you when it never worked.

## Count what's in a file

**The situation.** You keep a list of servers in a plain text file, with comments and blank lines
to keep it tidy. You want a quick count of how many of each kind you have.

This goes through the list, skips comments and blank lines, and sorts the names by type:

<!-- run -->
```
printf "# my servers\nweb1\nweb2\n\ndb1\ncache1\nweb3\n" > servers.txt

web=0
db=0
other=0
for name in $(grep -v "^#" servers.txt); do
  case $name in
    web*) web=$((web + 1)) ;;
    db*) db=$((db + 1)) ;;
    *) other=$((other + 1)) ;;
  esac
done
printf "%-10s %3d\n" web $web database $db other $other
```

<!-- output -->
```
web          3
database     1
other        1
```

**How it works.** The first line only makes a test file. With your own file, leave it out.

The three counters start at 0. `grep -v "^#"` prints every line that does *not* start with `#`,
which drops the comment. The `for` splits that output at spaces and line ends, and empty lines give
no words at all, so the blank line is skipped for free. That only works because each server name
is one word. For lines with spaces in them, see
[Go through a file line by line](loops.md#go-through-a-file-line-by-line).

The `case` sorts each name by how it starts and adds one to the right counter. At the end, one
`printf` prints all three rows. Its pattern has two places, so it uses the six words two at a time.

## A to-do list

**The situation.** You want a to-do list that's always one command away, without opening an editor
or an app. Three functions that keep the list in a plain text file do the job. Put them in a startup
file, like `~/.shellcraft/startup/30-todo.sc`, and use them from the prompt:

<!-- run -->
```
todo_file=todo.txt

todo_add() {
  [ -n "$1" ] || return 1
  echo "$*" >> $todo_file
  echo "added: $*"
}

todo_list() {
  if [ ! -s $todo_file ]; then
    echo "nothing to do"
    return 0
  fi
  cat -n $todo_file
}

todo_clear() {
  rm $todo_file
  echo "all done"
}

todo_list
todo_add buy milk
todo_add write the report
todo_list
todo_clear
```

<!-- output -->
```
nothing to do
added: buy milk
added: write the report
     1	buy milk
     2	write the report
all done
```

**How it works.** `todo_file` holds the file's name in one place, so all three functions agree on
it and you only change it once.

`todo_add` refuses to add an empty item: with no words after it, `$1` is empty and the function
returns 1. Otherwise `$*` is all the words, so `todo_add buy milk` saves `buy milk` as one line.
`>>` adds it to the end of the file and creates the file the first time.

`todo_list` checks `[ ! -s $todo_file ]`, "the file doesn't exist or is empty", and says so. That's
nicer than the error `cat` would give for a missing file. Otherwise `cat -n` prints the file with
line numbers. `todo_clear` deletes the file, which empties the list.

The last five lines only try the functions out. In your startup file, leave them out and type
the commands at the prompt instead.

For a real list, set `todo_file=~/.shellcraft/todo.txt`, so it's the same file wherever you are.

## A menu func

**The situation.** There are a few jobs you do all the time, and you'd like a small control panel
for them instead of remembering each command. A [func](../funcs.md) is a file of steps you run with
`func run NAME`. This one keeps asking until you quit. Save it as `~/.shellcraft/funcs/panel.func`:

```
# func: panel
# description: A menu of everyday jobs
# command: yes
# usage: panel
while true; do
  echo
  echo "1) What's in this folder"
  echo "2) Today's date"
  echo "3) Disk use of this folder"
  echo "q) Quit"
  read -p "Choose: " choice
  case $choice in
    1) ls ;;
    2) date ;;
    3) du -sh . ;;
    q|Q) break ;;
    *) echo "'$choice' isn't on the menu" ;;
  esac
done
```

**How it works.** The `#` lines at the top describe the func. `func list` and `func show` show the
description and the `usage` line.

Because of `command: yes`, typing `panel` runs it too. A loop written over several lines counts as one
step of the func.

The body is the menu from [Menus that keep asking](loops.md#menus-that-keep-asking). `while true`
loops forever, and only `break` on `q` or `Q` ends it. The empty `echo` at the top of each round
leaves a blank line, so each menu is easy to tell apart from the output above it. `*)` catches any
answer that isn't on the menu and says so, instead of doing nothing. To add a job, add an `echo`
line to the menu and a matching line to the `case`.

## A setup func with arguments

**The situation.** Every new project starts the same way: make a folder, add a README and a notes
file, go into it. This func does all of that from one command. `$1` is the project's name, and it
stops with a message if you forget it:

```
# func: newproject
# description: Make a project folder with a README and a notes file
# command: yes
# usage: newproject NAME
if [ -z "$1" ]; then echo "usage: newproject NAME" >&2; false; fi
mkdir -p ~/work/$1
cd ~/work/$1
echo "# $1" > README.md
echo "Started $(date +%Y-%m-%d)" > notes.txt
echo "created ~/work/$1"
```

**How it works.** A func stops at the first step that fails. Without a name, the first step prints
the usage and ends with `false`, so that step fails and nothing after it runs. `false` is a command
that does nothing except fail, which is exactly what's needed here. The usage message goes to the
error stream with `>&2`, so it shows in the error color.

With a name, `newproject website` makes `~/work/website`, moves into it, writes `# website` as the
README's heading and today's date into the notes. You end up in the new folder, because a func runs
in your shell.

Change `~/work` to where you keep projects, and add any other starter files you always want.

## A Python-style to-do list

**The situation.** The same to-do list as above, but you'd rather write it the Python way. This
version uses `def` with named arguments, `if -py` and a `for -py` loop over the lines of the file.
It also numbers the list itself. Put it in a startup file, like
`~/.shellcraft/startup/30-todo.sc`:

<!-- run -->
```
todo_file=todo.txt

def todo_add(item):
    echo "$*" >> $todo_file
    echo "added: $*"

def todo_list():
    if not isfile(todo_file):
        echo "nothing to do"
        return 0
    number=0
    for -py item in (cat $todo_file):
        number=$((number + 1))
        echo "$number. $item"

def todo_done(which):
    rm $todo_file
    echo "all done"

todo_list
todo_add buy milk
todo_add write the report
todo_list
todo_done all
```

<!-- output -->
```
nothing to do
added: buy milk
added: write the report
1. buy milk
2. write the report
all done
```

**How it works.** `def todo_add(item):` makes a function with one named argument. Naming it means
ShellCraft checks it for you: `item` is required, so `todo_add` on its own stops with
`todo_add: missing argument 'item'`. You don't need the `[ -n "$1" ]` line the bash version has.
`$*` is every argument as one piece of text, so `todo_add buy milk` saves `buy milk`.

In `todo_list`, `if not isfile(todo_file):` is a Python condition. `isfile` checks that the file
exists, and `todo_file` has no `$` because it's inside a Python condition. Then `for -py item in
(cat $todo_file):` goes through the file one whole line at a time, and the loop counts as it goes
to print the numbers.

`todo_done` takes an argument, `which`, so you can type `todo_done all`. This simple version clears
the whole list whatever you pass.

## A Python-style setup menu

**The situation.** You want the shell to ask you a few questions when it starts, then set things up
from the answers: which project, and how many terminals you'll need. A wrong answer, like letters
where you wanted a number, should be asked again rather than break anything.

This startup file uses `input()`, `int(input())` that keeps asking until it gets a number, a
`match` menu, and a loop that runs once for each terminal:

<!-- run: ["2", "abc", "3"] -->
```
echo "1) website"
echo "2) api"
echo "3) none"
pick = input("Which project? ")
match pick:
    case "1":
        project=website
    case "2":
        project=api
    case _:
        project=none

if -py project != "none":
    windows = int(input("How many terminals for $project? "))
    for -py n in 1..$windows:
        echo "terminal $n: ready for $project"
else:
    echo "no project today"
```

<!-- output -->
```
1) website
2) api
3) none
Which project? 2
How many terminals for api? abc
'abc' is not a whole number; try again
How many terminals for api? 3
terminal 1: ready for api
terminal 2: ready for api
terminal 3: ready for api
```

**How it works.** The three `echo` lines print the menu, and `input()` asks and stores the answer in
`pick`. `match` is the Python-style `case`: it compares `pick` with each `case` in turn. `case _:`
catches everything else, so any answer other than 1 or 2 means `none`.

`if -py project != "none":` only goes on when you picked a project. `int(input(…))` asks for the
number of terminals. When the answer was `abc`, ShellCraft said so and asked again by itself, with
no extra code from you. `for -py n in 1..$windows:` then counts from 1 to that number.

In a real startup file, put `cd ~/work/$project` or `env set PROJECT=$project` where the `echo`
lines are.

## A backup that always cleans up

**The situation.** You back up a folder by hand now and then. You want two guarantees: two backups
never run at the same time, and a backup that fails halfway doesn't leave a mess behind. This
Python-style function copies a folder into `backups/`. A lock file stops two backups from running
at once. `try:` catches a failing step, and `finally:` removes the lock whatever happened, so a
failed backup never leaves it behind:

<!-- run -->
```
def backup(folder):
    if isfile("backup.lock"):
        echo "a backup is already running"
        return 1
    touch backup.lock
    try:
        [ -d $folder ]
        mkdir -p backups
        cp -r $folder backups/
        echo "saved $folder"
    except as rc:
        echo "could not back up $folder (status $rc)"
    finally:
        rm -f backup.lock

mkdir notes
backup notes
backup photos
[ -f backup.lock ] || echo "no lock left behind"
```

<!-- output -->
```
saved notes
could not back up photos (status 1)
no lock left behind
```

**How it works.** First the function checks for `backup.lock`. If it's there, another backup is
running, so it says so and returns 1 without touching anything. Otherwise `touch` creates the lock.

Inside `try:`, each line runs in order, and the first one that fails jumps straight to `except`.
`[ -d $folder ]` fails when the folder isn't there, so the rest of the body is skipped. That's why
`backup photos` never reaches `cp`. `except as rc:` puts the failing line's exit status in `rc`, so
the message can show it.

`finally:` runs last, every time: after a good backup, after a failed one, and even after a `return`
inside `try`. That's the right place for cleanup, because you can't forget it on one of the paths.
The last line of the example checks that the lock really is gone.

In a real backup, `tar czf` or `rsync` goes where `cp` is, and a failure in any of them is caught
the same way. [Errors and cleanup](errors.md) explains `try`, `except` and `finally` in full, along
with `trap` and `set -e`.
