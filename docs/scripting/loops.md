# Loops and menus

← [Scripting guide](README.md)

This page shows you how to repeat things: once for every item in a list, every file in a folder,
every line of a file, or as long as a condition holds. It ends with menus that keep asking until
the user is done.

## for: once per item

A `for` loop runs its commands once for every word after `in`. Each time round, the variable holds
the next word:

<!-- run -->
```
for fruit in apple banana cherry; do
  echo "I like $fruit"
done
```

<!-- output -->
```
I like apple
I like banana
I like cherry
```

The parts are: `for`, a variable name, `in`, the list, `; do`, the commands, and `done`. A short
loop fits on one line:

<!-- run -->
```
for n in 1 2 3; do echo "number $n"; done
```

<!-- output -->
```
number 1
number 2
number 3
```

## Loop over a list

ShellCraft has no arrays. Keep a list in a plain variable, with spaces between the items, and leave
the variable *unquoted* after `in` so it splits into words:

<!-- run -->
```
servers="web1 web2 db1"
for server in $servers; do
  echo "checking $server"
done
```

<!-- output -->
```
checking web1
checking web2
checking db1
```

With quotes, `"$servers"` stays one item, and the loop runs once. Inside the loop, quote the
variable as usual: `"$server"`.

To add to a list, put the old value and the new item together:

<!-- run -->
```
todo=""
todo="$todo backup"
todo="$todo deploy"
for task in $todo; do echo "- $task"; done
```

<!-- output -->
```
- backup
- deploy
```

## Count with a range

`{1..5}` is the numbers from 1 to 5. A third number is the step, and a leading zero pads them all:

<!-- run -->
```
for n in {0..20..5}; do echo "step $n"; done
for n in {01..03}; do echo "file-$n.txt"; done
for letter in {a..c}; do echo "part $letter"; done
```

<!-- output -->
```
step 0
step 5
step 10
step 15
step 20
file-01.txt
file-02.txt
file-03.txt
part a
part b
part c
```

To print several items on one line, build the text in a variable and print it once at the end:

<!-- run -->
```
line=""
for n in {1..5}; do line="$line$n "; done
echo "$line"
```

<!-- output -->
```
1 2 3 4 5 
```

`{a,b,c}` lists the choices to fill in: `for f in report.{csv,json}` goes through `report.csv` and
`report.json`. `seq` counts too, and takes variables: `for n in $(seq 1 $count); do … done`.

## Loop over files

A pattern like `*.log` becomes the matching file names, in order:

<!-- run -->
```
touch app.log db.log notes.txt
for file in *.log; do
  echo "found $file"
done
```

<!-- output -->
```
found app.log
found db.log
```

When nothing matches, the pattern stays as it is, so the loop runs once with the text `*.log`.
Skip that case with a test at the top of the loop:

<!-- run -->
```
for file in *.bak; do
  [ -f "$file" ] || continue
  echo "removing $file"
done
echo "no backups to remove"
```

<!-- output -->
```
no backups to remove
```

## Loop over a command's output

`$(command)` in the list gives you every word that command prints:

<!-- run -->
```
printf "alpha\nbeta\ngamma\n" > names.txt
for name in $(cat names.txt); do
  echo "hello $name"
done
```

<!-- output -->
```
hello alpha
hello beta
hello gamma
```

It splits at every space as well as at line ends. When the lines themselves have spaces, use the
next way instead.

## Go through a file line by line

`for -py` with the command in `( )` gives you one whole line at a time, spaces and all. This is how
you'd use `while read line` in bash. It's written in the Python style: the line ends in `:`, and the
loop is the indented lines under it:

<!-- run -->
```
printf "web1 frontend\nweb2 frontend\n# a comment\n\ndb1 database\n" > hosts.txt
for -py line in (cat hosts.txt):
    if line == "" or line.startswith("#"):
        continue
    echo "host and role: $line"
```

<!-- output -->
```
host and role: web1 frontend
host and role: web2 frontend
host and role: db1 database
```

The nested `if` takes a Python condition, and needs no `-py` of its own. Inside the loop, `$line`
is always one word, and `$( )` doesn't run. To take a line apart, hand it to a
[function](functions.md), where `cut` can split it:

<!-- run -->
```
describe() {
  local host=$(echo "$1" | cut -d " " -f 1)
  local role=$(echo "$1" | cut -d " " -f 2)
  echo "$host is a $role server"
}
printf "web1 frontend\ndb1 database\n" > hosts.txt
for -py line in (cat hosts.txt): describe $line
```

<!-- output -->
```
web1 is a frontend server
db1 is a database server
```

`for -py n in 1..10` counts too, and `1..10..2` counts in steps of 2.

## Loops in Python style

Every loop on this page can be written in the Python style. Here are `for` and `while`:

<!-- run -->
```
for -py fruit in apple banana:
    echo "I like $fruit"

countdown=3
while -py int(countdown) > 0:
    echo "$countdown..."
    countdown -= 1
echo "liftoff!"
```

<!-- output -->
```
I like apple
I like banana
3...
2...
1...
liftoff!
```

Inside a Python-style block, `countdown -= 1` works like `countdown=$((countdown - 1))`. Here's a
counter that adds up as it goes:

<!-- run -->
```
n=0
total=0
while -py int(n) < 4:
    n += 1
    total += int(n)
    echo "after $n: $total"
```

<!-- output -->
```
after 1: 1
after 2: 3
after 3: 6
after 4: 10
```

`until -py` and `while -py True:` work the same way, and so do `break` and `continue`.
[Bash style and Python style](python-style.md) shows every loop both ways.

## Loops inside loops

Put one loop inside another to go through every pair. Each loop needs its own `done`:

<!-- run -->
```
for env in dev prod; do
  for service in web api; do
    echo "$service on $env"
  done
done
```

<!-- output -->
```
web on dev
api on dev
web on prod
api on prod
```

## while: as long as a condition holds

A `while` loop checks its condition before each round, and stops as soon as it fails. Remember to
change something inside, or it never stops:

<!-- run -->
```
countdown=3
while [ $countdown -gt 0 ]; do
  echo "$countdown..."
  countdown=$((countdown - 1))
done
echo "liftoff!"
```

<!-- output -->
```
3...
2...
1...
liftoff!
```

`until` is the opposite: it runs until the condition holds.

<!-- run -->
```
tries=0
until [ $tries -ge 3 ]; do
  tries=$((tries + 1))
  echo "attempt $tries"
done
```

<!-- output -->
```
attempt 1
attempt 2
attempt 3
```

## Stop early with break and continue

`break` leaves the loop right away. `continue` skips the rest of this round and starts the next one:

<!-- run -->
```
for n in 1 2 3 4 5 6; do
  if [ $n -eq 2 ]; then continue; fi
  if [ $n -eq 5 ]; then break; fi
  echo "n is $n"
done
```

<!-- output -->
```
n is 1
n is 3
n is 4
```

They work after `&&` and `||` too, which keeps a loop short:

<!-- run -->
```
for word in keep skip keep stop keep; do
  [ $word = skip ] && continue
  [ $word = stop ] && break
  echo $word
done
```

<!-- output -->
```
keep
keep
```

In nested loops, `break` and `continue` act on the innermost loop only.

`while true` makes a loop that only stops at a `break`, which is useful when you don't know in
advance how many rounds you need. Ctrl-C stops any loop.

## Send a whole loop somewhere

Put a pipe or a redirect after `done` and it takes the output of every round together:

<!-- run -->
```
for n in 3 1 2; do echo "item $n"; done | sort
for n in 1 2 3; do echo "line $n"; done > lines.txt
wc -l lines.txt
```

<!-- output -->
```
item 1
item 2
item 3
3 lines.txt
```

## Menus that keep asking

A menu that comes back after each choice is a `while true` loop with a `case` inside. The Quit
choice ends it with `break`:

<!-- run: ["1", "9", "2", "q"] -->
```
while true; do
  echo "1) Show the date"
  echo "2) Show this folder"
  echo "q) Quit"
  read -p "Choice: " choice
  case $choice in
    1) echo "today is a fine day" ;;
    2) echo "you're in the work folder" ;;
    q|Q) echo "bye"; break ;;
    *) echo "'$choice' isn't a choice, try again" ;;
  esac
done
```

<!-- output -->
```
1) Show the date
2) Show this folder
q) Quit
Choice: 1
today is a fine day
1) Show the date
2) Show this folder
q) Quit
Choice: 9
'9' isn't a choice, try again
1) Show the date
2) Show this folder
q) Quit
Choice: 2
you're in the work folder
1) Show the date
2) Show this folder
q) Quit
Choice: q
bye
```

In a real menu, put `date` and `pwd` in place of the `echo` lines.

### A numbered menu with select

`select` writes the numbered list for you, asks, and puts the chosen word in your variable. The
menu keeps asking until a `break`:

<!-- run: ["2"] -->
```
select fruit in apple banana cherry; do
  echo "you picked $fruit"
  break
done
```

<!-- output -->
```
1) apple
2) banana
3) cherry
#? 2
you picked banana
```

`$PS3` sets the question, which is `#? ` by default. `$REPLY` is exactly what was typed. When the
answer isn't one of the numbers, the variable is empty, so check it with `-n`:

<!-- run: ["5", "3"] -->
```
PS3="Deploy to which environment? "
select env in dev staging prod; do
  if [ -n "$env" ]; then
    echo "deploying to $env"
    break
  fi
  echo "'$REPLY' isn't on the list, pick 1, 2 or 3"
done
```

<!-- output -->
```
1) dev
2) staging
3) prod
Deploy to which environment? 5
'5' isn't on the list, pick 1, 2 or 3
Deploy to which environment? 3
deploying to prod
```

`select` and `case` work well together. Add a Quit item and `break` on it:

<!-- run: ["1", "3"] -->
```
PS3="What now? "
select action in "Run tests" "Build" Quit; do
  case $action in
    "Run tests") echo "running the tests" ;;
    Build) echo "building" ;;
    Quit) break ;;
    *) echo "pick a number from the list" ;;
  esac
done
```

<!-- output -->
```
1) Run tests
2) Build
3) Quit
What now? 1
running the tests
What now? 3
```

In Python style, `select -py` and `match` make the same menu:

<!-- run: ["1", "3"] -->
```
PS3="What now? "
select -py action in "Run tests" "Build" Quit:
    match action:
        case "Run tests":
            echo "running the tests"
        case "Build":
            echo "building"
        case "Quit":
            break
        case _:
            echo "pick a number from the list"
```

<!-- output -->
```
1) Run tests
2) Build
3) Quit
What now? 1
running the tests
What now? 3
```

Menus need someone at the keyboard, so `select`, `read` and `input()` only work in the interactive
shell, not for AI clients over MCP.
