# Bash style and Python style

← [Scripting guide](README.md)

Every block in ShellCraft can be written two ways. **Bash style** is the default and looks like
bash: `do` and `done`, `then` and `fi`. **Python style** looks like Python: the line that starts a
block ends in `:`, and the lines inside it are indented under it. This page shows every kind of
block both ways, side by side, so you can pick the one you like or copy whichever is closer to what
you need.

## The rules of the Python style

- Start the block with `-py` after the keyword: `for -py`, `if -py`, `while -py`, `case -py`,
  `select -py`. A function uses `def` instead.
- End that line with `:`, and indent the lines inside it. Four spaces is usual; any amount works as
  long as the lines of one block line up.
- The block ends at the first line that's indented less.
- Blocks inside a Python-style block don't need `-py` again.
- Conditions are Python: `if -py n > 3:`, with variables named without `$` (`$n` works too). A
  condition that isn't Python runs as a command, so `if -py grep -q ERROR app.log:` works.
- Variables are always one word, whatever they hold, so you rarely need quotes around them.
- `pass` does nothing, as in Python, for a block that has nothing to do yet.
- A short block fits on one line after the colon: `for -py n in 1..3: echo $n`.

The commands inside a block are the same in both styles: `echo`, `read`, `x=1`, `$((n + 1))`.
Python-style blocks can also set a variable the Python way, `count = 0` and `count += 1` (see
[Set a variable](#set-a-variable)).

At the prompt, type the first line and press Enter. The next line starts indented for you. Press
Backspace to go back a level, and press Enter on an empty line to run the block. ↑ brings the whole
block back, as you typed it.

## Set a variable

Bash style writes `name=value`, with no spaces, and does sums in `$(( ))`:

<!-- run -->
```
count=0
for n in 1 2 3; do
  count=$((count + n))
done
echo "total: $count"
```

<!-- output -->
```
total: 6
```

Python style writes `name = value`, with a Python value on the right, and `+=` to add to a
variable:

<!-- run -->
```
for -py n in 1 2 3:
    if n == "1":
        count = 0
    count += int(n)
echo "total: $count"
```

<!-- output -->
```
total: 6
```

`-=`, `*=`, `/=`, `//=` and `%=` work too. The value can be anything a Python condition can use:
`name = name.strip().lower()`, `total = int(a) * 2`, `big = int(n) > 10` (stored as `True` or
`False`). Variables still hold text, so `int(count)` reads one back as a number; `count += 1` does
that for you. `/` always gives a fraction (`2.0`), and `//` a whole number.

This only works inside a Python-style block or a `def`. At the prompt or in a bash block,
`x = 5` with spaces runs a command named `x`, as in bash. `name=value` works everywhere.

## if, elif, else

Bash style:

<!-- run -->
```
hour=15
if [ $hour -lt 12 ]; then
  echo "Good morning"
elif [ $hour -lt 18 ]; then
  echo "Good afternoon"
else
  echo "Good evening"
fi
```

<!-- output -->
```
Good afternoon
```

Python style:

<!-- run -->
```
hour=15
if -py int(hour) < 12:
    echo "Good morning"
elif int(hour) < 18:
    echo "Good afternoon"
else:
    echo "Good evening"
```

<!-- output -->
```
Good afternoon
```

Variables are text, so `int(hour)` turns one into a number before comparing it.

## Comparing text and numbers

Bash style uses `[ ]` tests, with `-lt`, `-gt` and the rest for numbers and `=` for text:

<!-- run -->
```
name="Ada Lovelace"
count=3
if [ "$name" = "Ada Lovelace" ] && [ $count -gt 2 ]; then
  echo "Ada, with $count items"
fi
if [[ $name == Ada* ]]; then echo "starts with Ada"; fi
```

<!-- output -->
```
Ada, with 3 items
starts with Ada
```

Python style uses Python's operators, `and` / `or` / `not`, and text methods:

<!-- run -->
```
name="Ada Lovelace"
count=3
if -py name == "Ada Lovelace" and int(count) > 2:
    echo "Ada, with $count items"
if -py name.startswith("Ada"):
    echo "starts with Ada"
if -py "Love" in name and len(name) < 20:
    echo "a short name with Love in it"
```

<!-- output -->
```
Ada, with 3 items
starts with Ada
a short name with Love in it
```

`man if` lists everything a Python condition can use: `len`, `int`, `match(REGEX, TEXT)`,
`exists(PATH)`, `isfile(PATH)`, `isdir(PATH)`, `.lower()`, `.endswith()` and more.

## Test a command

Bash style puts the command straight after `if`:

<!-- run -->
```
echo "ERROR disk full" > app.log
if grep -q ERROR app.log; then
  echo "the log has errors"
fi
```

<!-- output -->
```
the log has errors
```

Python style does the same. A condition that isn't Python runs as a command, and it's true when the
command succeeds:

<!-- run -->
```
echo "ERROR disk full" > app.log
if -py grep -q ERROR app.log:
    echo "the log has errors"
```

<!-- output -->
```
the log has errors
```

`status` is the last command's exit status, so you can also run the command first and check it:
`if -py status != 0:`.

## Files and folders

Bash style:

<!-- run -->
```
mkdir -p backups
if [ -d backups ] && [ ! -f backups/latest.tar ]; then
  echo "the folder is there, with no backup yet"
fi
```

<!-- output -->
```
the folder is there, with no backup yet
```

Python style:

<!-- run -->
```
mkdir -p backups
if -py isdir("backups") and not exists("backups/latest.tar"):
    echo "the folder is there, with no backup yet"
```

<!-- output -->
```
the folder is there, with no backup yet
```

## for: a list of items

Bash style:

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

Python style:

<!-- run -->
```
for -py fruit in apple banana cherry:
    echo "I like $fruit"
```

<!-- output -->
```
I like apple
I like banana
I like cherry
```

## for: counting

Bash style counts with `{FIRST..LAST}`, and Python style with `FIRST..LAST`. A third number is the
step:

<!-- run -->
```
for n in {1..3}; do echo "bash $n"; done
for -py n in 1..3: echo "python $n"
for -py n in 0..20..10: echo "step $n"
```

<!-- output -->
```
bash 1
bash 2
bash 3
python 1
python 2
python 3
step 0
step 10
step 20
```

## for: files and lines

Both styles take a pattern like `*.log`. To go through a command's output, bash style uses `$( )`,
which splits it into words. Python style puts the command in `( )`, and gives you one whole line at a
time, spaces and all:

<!-- run -->
```
printf "web1 frontend\nweb2 frontend\ndb1 database\n" > hosts.txt

for word in $(cat hosts.txt); do
  echo "word: $word"
done

for -py line in (cat hosts.txt):
    echo "line: $line"
```

<!-- output -->
```
word: web1
word: frontend
word: web2
word: frontend
word: db1
word: database
line: web1 frontend
line: web2 frontend
line: db1 database
```

Skip comments and blank lines with an `if` and `continue`:

<!-- run -->
```
printf "# my servers\nweb1\n\ndb1\n" > servers.txt
for -py line in (cat servers.txt):
    if line == "" or line.startswith("#"):
        continue
    echo "server: $line"
```

<!-- output -->
```
server: web1
server: db1
```

## while and until

Bash style:

<!-- run -->
```
n=3
while [ $n -gt 0 ]; do
  echo "$n..."
  n=$((n - 1))
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

Python style:

<!-- run -->
```
n=3
while -py int(n) > 0:
    echo "$n..."
    n -= 1
echo "liftoff!"
```

<!-- output -->
```
3...
2...
1...
liftoff!
```

`until` runs until its condition holds, in both styles: `until [ $n -ge 3 ]; do … done`, or
`until -py int(n) >= 3:`. `while true` loops until a `break`; in Python style it's
`while -py True:`.

## break and continue

They work the same in both styles. Here they are in Python style:

<!-- run -->
```
for -py n in 1..6:
    if n == "2":
        continue
    if n == "5":
        break
    echo "n is $n"
```

<!-- output -->
```
n is 1
n is 3
n is 4
```

## Nesting

Blocks go inside each other in both styles. In Python style, the indentation shows what belongs to
what, and only the outer block needs `-py`:

<!-- run -->
```
for -py env in dev prod:
    for service in web api:
        if env == "prod" and service == "api":
            echo "$service on $env: ask first"
        else:
            echo "$service on $env"
```

<!-- output -->
```
web on dev
api on dev
web on prod
api on prod: ask first
```

The bash version of the same thing:

<!-- run -->
```
for env in dev prod; do
  for service in web api; do
    if [ $env = prod ] && [ $service = api ]; then
      echo "$service on $env: ask first"
    else
      echo "$service on $env"
    fi
  done
done
```

<!-- output -->
```
web on dev
api on dev
web on prod
api on prod: ask first
```

## case: one of several choices

Bash style:

<!-- run -->
```
for file in notes.txt photo.jpg script.py; do
  case $file in
    *.txt|*.md) echo "$file: text" ;;
    *.jpg|*.png) echo "$file: picture" ;;
    *) echo "$file: something else" ;;
  esac
done
```

<!-- output -->
```
notes.txt: text
photo.jpg: picture
script.py: something else
```

Python style: each choice is a pattern and a `:`, with its commands indented under it. `|`
separates alternatives, and `_` is "anything else":

<!-- run -->
```
for -py file in notes.txt photo.jpg script.py:
    case $file:
        "*.txt" | "*.md":
            echo "$file: text"
        "*.jpg" | "*.png":
            echo "$file: picture"
        _:
            echo "$file: something else"
```

<!-- output -->
```
notes.txt: text
photo.jpg: picture
script.py: something else
```

Outside a Python-style block, write `case -py $file:`. A pattern can be exact text (`"yes"`) or use
`*`, `?` and `[ ]` (`"[yY]*"` is anything that starts with y or Y).

## Ask the user

Both ways of asking work in both styles. `read -p "QUESTION" NAME` is the bash way, and
`NAME = input("QUESTION")` is the Python way. `int(input(…))` keeps asking until the answer is a
whole number.

Bash style:

<!-- run: ["Ada", "y"] -->
```
read -p "Your name: " name
read -p "Get the newsletter? (y/n) " news
case $news in
  [yY]*) echo "Thanks $name, you're signed up" ;;
  *) echo "Thanks $name" ;;
esac
```

<!-- output -->
```
Your name: Ada
Get the newsletter? (y/n) y
Thanks Ada, you're signed up
```

Python style:

<!-- run: ["Ada", "y"] -->
```
name = input("Your name: ")
news = input("Get the newsletter? (y/n) ")
if -py news.lower().startswith("y"):
    echo "Thanks $name, you're signed up"
else:
    echo "Thanks $name"
```

<!-- output -->
```
Your name: Ada
Get the newsletter? (y/n) y
Thanks Ada, you're signed up
```

`read -s` hides what's typed, for passwords and tokens. The question in `input()` can use
variables: `input("Age of $name? ")`.

### Keep asking until the answer is good

Bash style:

<!-- run: ["", "website"] -->
```
project=""
while [ -z "$project" ]; do
  read -p "Project name (required): " project
done
echo "Project: $project"
```

<!-- output -->
```
Project name (required): 
Project name (required): website
Project: website
```

Python style:

<!-- run: ["", "website"] -->
```
project=""
while -py project == "":
    project = input("Project name (required): ")
echo "Project: $project"
```

<!-- output -->
```
Project name (required): 
Project name (required): website
Project: website
```

A number in a range:

<!-- run: ["seven", "12", "7"] -->
```
level=0
while -py not (1 <= int(level) <= 10):
    level = int(input("Level (1-10): "))
echo "Level $level"
```

<!-- output -->
```
Level (1-10): seven
'seven' is not a whole number; try again
Level (1-10): 12
Level (1-10): 7
Level 7
```

## Menus

A menu that keeps asking until you quit, in bash style:

<!-- run: ["1", "q"] -->
```
while true; do
  echo "1) Say hello"
  echo "q) Quit"
  read -p "Choice: " choice
  case $choice in
    1) echo "hello!" ;;
    q|Q) echo "bye"; break ;;
    *) echo "'$choice' isn't a choice" ;;
  esac
done
```

<!-- output -->
```
1) Say hello
q) Quit
Choice: 1
hello!
1) Say hello
q) Quit
Choice: q
bye
```

The same menu in Python style:

<!-- run: ["1", "q"] -->
```
while -py True:
    echo "1) Say hello"
    echo "q) Quit"
    choice = input("Choice: ")
    case $choice:
        "1":
            echo "hello!"
        "q" | "Q":
            echo "bye"
            break
        _:
            echo "'$choice' isn't a choice"
```

<!-- output -->
```
1) Say hello
q) Quit
Choice: 1
hello!
1) Say hello
q) Quit
Choice: q
bye
```

### A numbered menu with select

`select` numbers the choices for you. Bash style:

<!-- run: ["2"] -->
```
PS3="Deploy to which environment? "
select env in dev staging prod; do
  echo "deploying to $env"
  break
done
```

<!-- output -->
```
1) dev
2) staging
3) prod
Deploy to which environment? 2
deploying to staging
```

Python style:

<!-- run: ["5", "3"] -->
```
PS3="Deploy to which environment? "
select -py env in dev staging prod:
    if env == "":
        echo "'$REPLY' isn't on the list"
        continue
    echo "deploying to $env"
    break
```

<!-- output -->
```
1) dev
2) staging
3) prod
Deploy to which environment? 5
'5' isn't on the list
Deploy to which environment? 3
deploying to prod
```

`select -py` takes its choices like `for -py`, so `select -py host in (cat hosts.txt):` offers one
line of the file per choice.

## Functions

Bash style: `name() { … }`, with the arguments in `$1`, `$2` and so on:

<!-- run -->
```
greet() {
  local name=$1
  local greeting=${2:-Hello}
  echo "$greeting, $name"
}
greet Ada
greet Sam Hi
```

<!-- output -->
```
Hello, Ada
Hi, Sam
```

Python style: `def`, with the arguments named in the brackets. A parameter with `=` has a default,
and one without is required:

<!-- run -->
```
def greet(name, greeting="Hello"):
    echo "$greeting, $name"
greet Ada
greet Sam Hi
```

<!-- output -->
```
Hello, Ada
Hi, Sam
```

Typing just `greet` stops with `greet: missing argument 'name'`, because `name` has no default. The
parameters are `local`: they belong to the call, and the shell's own variables of the same
name come back afterwards. `$1`, `$#` and `$@` still work, so extra arguments aren't lost. Inside,
conditions are Python:

<!-- run -->
```
def describe(path):
    if isdir(path):
        echo "$path is a folder"
    elif isfile(path):
        echo "$path is a file"
    else:
        echo "$path isn't there"
        return 1
mkdir -p docs
describe docs
describe missing.txt || echo "(it returned $?)"
```

<!-- output -->
```
docs is a folder
missing.txt isn't there
(it returned 1)
```

Ask from a function in either style. Here it's `input()` in a `def`:

<!-- run: ["40"] -->
```
def ask_age(who):
    age = int(input("How old is $who? "))
    if int(age) >= 18:
        echo "$who is an adult"
    else:
        echo "$who is $age"
ask_age Ada
```

<!-- output -->
```
How old is Ada? 40
Ada is an adult
```

`return`, giving back a value with `echo` and `$( )`, and keeping functions in your rc file all
work the same as in [Functions](functions.md).

## Mixing the styles

Use whichever reads best for each block. A Python-style block can hold bash lines, and a bash
function can hold a Python-style block:

<!-- run -->
```
for -py name in web1 db1:
    if [ $name = db1 ]; then
        echo "$name: a database"
    fi
    for part in cpu disk; do echo "$name $part"; done

report() {
  for -py n in 1..2:
      echo "report line $n"
}
report
```

<!-- output -->
```
web1 cpu
web1 disk
db1: a database
db1 cpu
db1 disk
report line 1
report line 2
```

The brace form, `for -py n in 1..3 { echo $n }`, is the Python style on one line, and still works
everywhere.

## In your rc file, startup files and funcs

Python-style blocks work in `~/.shellcraft/rc`, in `~/.shellcraft/startup/*.sc`, in funcs, with
`source`, and at the prompt. In a func, a whole block counts as one step. A file with a block that's
indented wrongly doesn't run at all, and ShellCraft says which line to fix, the way Python does:

| Message | What's wrong |
| --- | --- |
| `expected an indented block` | The line after a `:` isn't indented |
| `unindent does not match any outer indentation level` | A line goes back to an indent no block uses |
| `inconsistent use of tabs and spaces in indentation` | A block mixes tabs and spaces |
| `'else' without a matching if` | An `else:` that doesn't line up with an `if` |

## Side by side

| | Bash style | Python style |
| --- | --- | --- |
| Set a variable | `name="Ada"` | `name = "Ada"` (in a block) |
| Add one | `n=$((n + 1))` | `n += 1` (in a block) |
| If | `if [ $n -gt 3 ]; then … fi` | `if -py int(n) > 3:` |
| Else if | `elif [ … ]; then` | `elif …:` |
| Else | `else` | `else:` |
| Text equals | `[ "$a" = "b" ]` | `a == "b"` |
| Contains | `[[ $a == *x* ]]` | `"x" in a` |
| And, or, not | `&&`, `\|\|`, `!` | `and`, `or`, `not` |
| A file exists | `[ -f x ]` | `isfile("x")` |
| A list | `for x in a b c; do … done` | `for -py x in a b c:` |
| Counting | `for n in {1..5}; do` | `for -py n in 1..5:` |
| Lines of a file | `for -py line in (cat f)` | `for -py line in (cat f):` |
| While | `while [ … ]; do … done` | `while -py …:` |
| Forever | `while true; do` | `while -py True:` |
| Choices | `case $x in a) … ;; *) … ;; esac` | `case -py $x:` then `"a":` and `_:` |
| Numbered menu | `select x in a b; do … done` | `select -py x in a b:` |
| Ask | `read -p "Q? " x` | `x = input("Q? ")` |
| Ask for a number | `read -p "N? " n` and a test | `n = int(input("N? "))` |
| Function | `f() { local a=$1; … }` | `def f(a):` |
| Do nothing | `:` or `true` | `pass` |
