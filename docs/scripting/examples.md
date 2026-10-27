# Complete examples

← [Scripting guide](README.md)

Whole scripts you can copy into your rc file, a startup file or a func, then change to fit. Each
one puts together the pieces from the other pages of this guide. Most are in bash style, and the
last two are in Python style; [Bash style and Python style](python-style.md) shows how to turn one
into the other. The marked ones run in the test
suite, so they work as shown.

## A friendly rc file

Your `~/.shellcraft/rc` runs every time the shell starts. This one says hello by the time of day,
sets up a few things, and defines two functions you can use at the prompt:

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

At the prompt, `remind call the bank` adds a line to the list you see at every start, and
`mkcd new-app` makes a folder and goes into it.

## Ask which project to work on

A pair of startup files. The first asks which project you want with a numbered menu. The second
uses the answer. They share variables because they run in the same shell, in name order.

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

## Ask before doing something risky

A `confirm` function you can put in front of any command. It asks a yes or no question, and
succeeds only on yes. Keep it in your rc file and use it with `&&`:

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

In real use, the command after `&&` is the risky one: `confirm "Delete the old logs?" && rm *.log`.

## Try again until it works

A loop that retries a command a few times, waiting between tries. The test here fails twice, then
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

Swap the `if` test for the real check, such as `if curl -s https://example.com > /dev/null; then`,
and the `sleep` for a sensible wait, like `sleep 5`.

## Count what's in a file

Go through a list of servers, skip comments and blank lines, and sort them by type:

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

## A to-do list

Three functions that keep a to-do list in a plain text file. Put them in a startup file, like
`~/.shellcraft/startup/30-todo.sc`, and use them from the prompt:

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

For a real list, set `todo_file=~/.shellcraft/todo.txt`, so it's the same file wherever you are.

## A menu func

A [func](../funcs.md) is a file of steps you run with `func run NAME`. This one is a small control
panel that keeps asking until you quit. Save it as `~/.shellcraft/funcs/panel.func`:

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

Because of `command: yes`, typing `panel` runs it too. A loop written over several lines counts as one
step of the func.

## A setup func with arguments

This func makes a new project folder with a few starter files. `$1` is the project's name, and it
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

A func stops at the first step that fails. Without a name, the first step prints the usage and
ends with `false`, so that step fails and nothing after it runs. With a name, you end up in the new
folder, because a func runs in your shell.

## A Python-style to-do list

The to-do list from above, written in the Python style: `def` with named arguments, `if -py` and a
`for -py` loop over the lines of the file. Put it in a startup file, like
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

`$*` is every argument as one piece of text, so `todo_add buy milk` saves `buy milk`. `item` is
required, so `todo_add` on its own stops with `todo_add: missing argument 'item'`.

## A Python-style setup menu

A startup file that asks a few questions when the shell starts, then sets things up from the
answers. It uses `input()`, `int(input())` that keeps asking until it gets a number, a `case -py`
menu, and a loop that checks each answer:

<!-- run: ["2", "abc", "3"] -->
```
echo "1) website"
echo "2) api"
echo "3) none"
pick = input("Which project? ")
case -py $pick:
    "1":
        project=website
    "2":
        project=api
    _:
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

In a real startup file, put `cd ~/work/$project` or `env set PROJECT=$project` where the `echo`
lines are.
