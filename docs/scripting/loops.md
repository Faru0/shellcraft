# Loops and menus

← [Scripting guide](README.md)

This page is for you when you want a script to do the same thing more than once: for every server
in a list, every file in a folder, every line of a file, or until something is true. After reading
it you'll be able to write each kind of loop, stop one early, and build a menu that keeps asking
until the user picks Quit.

If variables are new to you, read [Variables and input](variables-and-input.md) first. Loops use
them on every line.

## for: once per item

Reach for `for` when you know the list of things up front: three servers, a handful of file names,
the numbers 1 to 10. The loop runs its body once for every word after `in`. Each time round, the
variable holds the next word:

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

Read it piece by piece. `for fruit` names the variable. You choose the name, and you write it
without a `$` here because you're naming it, not using it. `in apple banana cherry` is the list.
`; do` says the list is over and the body starts. The indented line is the body, and inside it you
use the variable with a `$`, as `$fruit`. `done` ends the loop.

The indent is only there to make it easy to read. In bash style, `do` and `done` mark the body, not
the spaces.

A short loop fits on one line. Put a `;` before `done` so ShellCraft knows the body has ended:

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

### If it doesn't work

A loop with no `do` stops before it starts. If you write `for n in 1 2 3` and put the body on the
next line without `; do`, ShellCraft says:

```
for: expected 'do', got 'echo'
```

and then `'done' without a matching for` for the `done` line. Add `; do` at the end of the `for`
line, or put `do` on a line of its own.

If you forget `done`, the prompt keeps waiting for more lines. In a file, you get
`missing 'done'`. `source -n` finds both before you run anything, see
[Check a script before you run it](README.md#check-a-script-before-you-run-it).

## Loop over a list

Often the list lives in a variable: the servers you check every morning, the folders you back up.
ShellCraft has no arrays. Keep the list in a plain variable, with spaces between the items, and
leave the variable *unquoted* after `in` so it splits into words:

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

Inside the loop, quote the variable as usual: `"$server"`. Everywhere else in ShellCraft a variable
stays one piece, quotes or not. The list after `in` is the one place where an unquoted variable is
split at its spaces, and that's what makes this work.

With quotes, `"$servers"` stays one item, so the loop runs once with the whole text. This is the
most common surprise with list loops:

<!-- run -->
```
servers="web1 web2 db1"
for server in "$servers"; do
  echo "checking $server"
done
```

<!-- output -->
```
checking web1 web2 db1
```

If you see every item on one line like that, take the quotes off after `in`.

To add to a list, put the old value and the new item together and store the result back. Start
with an empty list:

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

The extra space at the front of `todo` doesn't matter, because the loop splits at spaces and skips
empty pieces.

## Count with a range

When you need numbers, like making ten test files or trying something five times, you don't have to
type them all out. `{1..5}` is the numbers from 1 to 5. A third number is the step, and a leading
zero pads them all to the same width:

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

The first loop counts from 0 to 20 in steps of 5. The second pads to two digits, which keeps file
names in order when you list them: `file-02` sorts before `file-10`, and `file-2` wouldn't. The
third shows that letters work too.

To print several items on one line, build the text in a variable and print it once at the end.
Each `echo` ends its own line, so an `echo` inside the loop would give you five lines:

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
`report.json`.

A range with braces only takes plain numbers. `{1..$count}` doesn't count: the loop runs once with
the text `{1..3}`. When the end comes from a variable, use `seq`, which counts too:
`for n in $(seq 1 $count); do … done`. In Python style, `for -py n in 1..$count:` works as well.

## Loop over files

This is the loop you'll write most: do something to every file that matches a pattern. A pattern
like `*.log` becomes the matching file names, in order:

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

`notes.txt` doesn't match `*.log`, so the loop never sees it. The `*` means "any text here".

Here's a real job: rename every `.txt` file in a folder to `.md`. `basename` with a second word
takes that ending off the name, so `notes.txt` becomes `notes`:

<!-- run -->
```
touch notes.txt todo.txt
for file in *.txt; do
  name=$(basename $file .txt)
  mv "$file" "$name.md"
  echo "renamed $file to $name.md"
done
```

<!-- output -->
```
renamed notes.txt to notes.md
renamed todo.txt to todo.md
```

The list is worked out once, before the first round. So renaming files inside the loop doesn't
confuse it.

When nothing matches, the pattern stays as it is, so the loop runs once with the text `*.log`. If
your loop then tries to delete or copy a file called `*.log`, you get an error about a file that
doesn't exist. Skip that case with a test at the top of the loop:

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

`[ -f "$file" ]` asks "is this a file?". When the answer is no, `||` runs `continue`, which skips
to the next round. Here there is no next round, so the loop ends quietly.

## Loop over a command's output

Sometimes the list comes from a command: the names in a file, the output of `ls`, a list a module
prints. `$(command)` in the list gives you every word that command prints:

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

It splits at every space as well as at line ends. That's fine for a list of single words. When the
lines themselves have spaces, like `web1 frontend`, a line would come apart into two rounds. Use
the next way instead.

## Go through a file line by line

You have a file with one record per line: a host and its role, a name and an email. You want each
whole line, spaces and all. In bash you'd write `while read line`. In ShellCraft you write
`for -py` with the command in `( )`, which gives you one whole line at a time.

It's written in the Python style: the line ends in `:`, and the loop body is the indented lines
under it. There's no `do` or `done`, so here the indent matters:

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

The first line writes a small test file with a comment and a blank line in it. In the loop, the
`if` checks each line with a Python condition: is it empty, or does it start with `#`? If so,
`continue` skips it. The nested `if` needs no `-py` of its own, because everything inside a
Python-style block is Python style already. Notice that `line` has no `$` in the condition but
does in the `echo`. A Python condition uses names like Python does, and a command line uses `$`
like the shell does.

Inside the loop, `$line` is always one word, and `$( )` doesn't run. To take a line apart, use
the `${…}` forms: `${line%% *}` cuts from the first space to the end, which leaves the first word,
and `${line#* }` cuts up to the first space, which leaves the rest:

<!-- run -->
```
printf "web1 frontend\ndb1 database\n" > hosts.txt
for -py line in (cat hosts.txt):
    echo "${line%% *} is a ${line#* } server"
```

<!-- output -->
```
web1 is a frontend server
db1 is a database server
```

[Defaults, lengths and trimming](variables-and-input.md#defaults-lengths-and-trimming) lists them
all. When a line has more fields than that, hand it to a [function](functions.md), where `cut` can
split it:

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

`describe` gets the whole line as `$1`. `cut -d " " -f 1` splits it at spaces and keeps the first
field, and `-f 2` keeps the second (`-f 3` would keep a third). `local` keeps `host` and `role`
inside the function, so they don't overwrite variables with the same names elsewhere in your
script. The last line shows that a one-line body can go right after the `:`.

If the file isn't there, `cat` reports `cat: hosts.txt: no such file` and the loop runs zero
times.

`for -py n in 1..10` counts too, and `1..10..2` counts in steps of 2.

## Loops in Python style

Every loop on this page can be written in the Python style, with a `:` and an indented body instead
of `do` and `done`. Use whichever you find easier to read. Here are `for` and `while`:

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

`countdown=3` is set the bash way, so it holds text, and `int(countdown)` turns it into a number
before the comparison. (After `countdown -= 1` it holds a number, which `int()` leaves as it is.)
Inside a Python-style block, `countdown -= 1` works like `countdown=$((countdown - 1))`. Here's a
counter that adds up as it goes:

<!-- run -->
```
n=0
total=0
while -py int(n) < 4:
    n += 1
    total += n
    echo "after $n: $total"
```

<!-- output -->
```
after 1: 1
after 2: 3
after 3: 6
after 4: 10
```

`n=0` is text, so the condition needs `int(n)` the first time round. After `n += 1`, `n` is a
number, so `total += n` can use it as it is.

`until -py` and `while -py True:` work the same way, and so do `break` and `continue`.
[Bash style and Python style](python-style.md) shows every loop both ways.

The Python style has two mistakes of its own. If you forget the `:` at the end of the first line,
ShellCraft doesn't see the start of a block and you get an error like `for: missing '{'`. If the
body isn't indented, you get `expected an indented block`. Add the `:`, and indent the body by
four spaces.

## Loops inside loops

When you need every pair, like every service on every environment, put one loop inside another.
The inner loop runs all the way through for each round of the outer one. Each loop needs its own
`done`:

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

The first `done` closes the inner loop, and the second closes the outer one. Indenting the inner
loop further makes it easy to see which `done` belongs to which.

## while: as long as a condition holds

Use `while` when you don't know the number of rounds ahead of time: keep trying until a server
answers, keep asking until the answer is valid, count down until zero. A `while` loop checks its
condition before each round, and stops as soon as it fails.

The condition is a command, the same kind `if` uses. Most often it's a test in `[ ]`. "Fails"
means the command ended with a non-zero exit status. A status of 0 means success, so the loop keeps
going while the test says 0. [Conditions and choices](conditions.md) has every test you can use.

Remember to change something inside, or it never stops:

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

`[ $countdown -gt 0 ]` asks "is countdown greater than 0?". The body prints the number, then
`countdown=$((countdown - 1))` takes one away. When `countdown` reaches 0, the test fails and the
loop ends. Without that last line, `countdown` would stay 3 forever. If that happens, press Ctrl-C
to stop the loop.

`until` is the opposite: it runs until the condition holds. Pick whichever reads more naturally.
"Until we've tried three times" reads better than "while we've tried fewer than three times":

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

Sometimes you want out of a loop before the list ends, because you found what you were looking for
or hit something bad. `break` leaves the loop right away. `continue` skips the rest of this round
and starts the next one:

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

At 2, `continue` skips the `echo`, so 2 never prints. At 5, `break` ends the loop, so 5 and 6 never
print.

They work after `&&` and `||` too, which keeps a loop short. `[ $word = skip ] && continue` reads
as "if the word is skip, continue":

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
advance how many rounds you need. The menus below use it. `while :` is the same: `:` is a command
that does nothing and succeeds, and you'll see it in bash scripts. Ctrl-C stops any loop.

## Send a whole loop somewhere

Put a pipe or a redirect after `done` and it takes the output of every round together. That's
handy when you want to sort what a loop prints, or save a report to a file:

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

The first loop prints three lines in the order 3, 1, 2, and `sort` puts them in order. The second
writes its three lines to `lines.txt` instead of the screen, and `wc -l` counts them. With `>`
after `done`, the file is written once for the whole loop. With `>` on the `echo` inside the loop,
each round would replace the file and you'd keep only the last line.

## Menus that keep asking

A menu that comes back after each choice is useful for a small control panel: check the disk, show
the logs, quit. It's a `while true` loop with a `case` inside. Each round prints the choices, asks,
and does what was picked. The Quit choice ends it with `break`:

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

`read -p "Choice: " choice` shows the question and stores the answer in `choice`. `case` compares
the answer with each pattern in turn. `q|Q)` matches either letter, and `*)` matches anything, so
it catches every answer that isn't on the menu. Each choice ends with `;;`. Without the `*)` line, a
wrong answer would do nothing at all, and the user wouldn't know why. [Conditions and
choices](conditions.md#case-pick-one-of-several-choices) explains `case` in full.

In a real menu, put `date` and `pwd` in place of the `echo` lines.

### A numbered menu with select

`select` saves you writing the menu by hand. It writes the numbered list for you, asks, and puts the
chosen word in your variable. The menu keeps asking until a `break`:

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

The user typed `2`, and `select` turned that into `banana`. The `break` right after means it only
asks once.

`$PS3` sets the question, which is `#? ` by default. `$REPLY` is exactly what was typed. When the
answer isn't one of the numbers, the variable is empty, so check it with `-n` ("is not empty")
before you use it:

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

The first answer, 5, isn't on the list, so `env` is empty, the `if` is skipped, and the message
uses `$REPLY` to show what was typed. The loop goes round and asks again. The list isn't printed a
second time, only the question. The second answer, 3, sets `env` to `prod`, and `break` ends it.

`select` and `case` work well together. Add a Quit item and `break` on it. Put quotes around an
item with a space in it, so it stays one choice:

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

`case _:` is Python's way of saying "anything else", like `*)` in bash style.

Menus need someone at the keyboard, so `select`, `read` and `input()` only work in the interactive
shell, not for AI clients over MCP.

## Where to go next

Loops that call your own commands are easier to read. [Functions](functions.md) shows you how to
make them. When a step inside a loop can fail and you want the script to stop or clean up, see
[Errors and cleanup](errors.md).
