# Bash style and Python style

← [Scripting guide](README.md)

This page is for you if you already know Python and want to script ShellCraft without learning
bash's `then`, `fi` and `esac`. Every block in ShellCraft can be written two ways: **bash style**,
the default, and **Python style**, where a line ends in `:` and the lines under it are indented.
After reading this page you'll know how a Python-style block turns into a shell block, where
Python rules apply and where shell rules still do, and the handful of places where it behaves
differently from real Python. Each kind of block is shown both ways, so you can also use the page
to translate a bash example you found somewhere.

## How a colon block becomes a shell block

ShellCraft is a shell, not a Python interpreter. A Python-style block is another way of writing
the same shell block, and the lines inside it are still shell commands: `echo`, `grep`, `mkdir`.
What changes is the shape of the block and the language of its condition.

Here is the same loop both ways:

<!-- run -->
```
for fruit in apple banana; do
  echo "I like $fruit"
done

for -py fruit in apple banana:
    echo "I like $fruit"
```

<!-- output -->
```
I like apple
I like banana
I like apple
I like banana
```

The first loop is bash: `do` opens it and `done` closes it. The second is the Python style. The
`-py` after `for` tells ShellCraft to read the line the Python way, the `:` opens the block, and
the indented line is its body. When you run it, ShellCraft rewrites it into its own brace form,
`for -py fruit in apple banana { echo "I like $fruit" }`, and runs that. You never need to write
the braces yourself, but they're still there if you want a block on one line.

The rules:

- Put `-py` after the keyword that starts the block: `for -py`, `if -py`, `while -py`,
  `until -py`, `case -py`, `select -py`. Three keywords need no flag because they only exist in
  the Python style: `def` for a function, `try:` and `match`.
- End that line with `:` and indent the lines inside it. Four spaces is usual. Any amount works as
  long as the lines of one block line up.
- The block ends at the first line that's indented less, the way Python's blocks do.
- Blocks inside a Python-style block don't need `-py` again. A nested `if` or `for` is read the
  Python way already.
- `pass` does nothing, for a block you haven't written yet.
- A short block fits on one line after the colon: `for -py n in 1..3: echo $n`.

At the prompt, type the first line and press Enter. The next line starts indented for you. Press
Backspace to go back a level, and press Enter on an empty line to run the block. ↑ brings the whole
block back, as you typed it.

## Conditions: Python or a command

In bash, the condition of an `if` is always a command. The `if` runs its body when that command
succeeds, which means it finished with exit status 0. `[ -f notes.txt ]` is a command too: it
checks something and succeeds when the check is true.

In the Python style, ShellCraft looks at the condition first. If it reads as a Python expression,
it's evaluated as Python. If it doesn't, it runs as a command, as in bash. So both of these work:

- `if -py int(n) > 3:` is Python, because `int(n) > 3` is a valid expression.
- `if -py grep -q ERROR app.log:` is a command, because `grep -q ERROR app.log` isn't Python.

`[ … ]`, `[[ … ]]` and `test` are always commands, with or without a `!` in front, even though
`[ -f x ]` would also read as a Python list. So `if -py [ -f notes.txt ]:` works the way it does in
bash.

The one place this catches people is a condition that is a single word. A bare word is a valid
Python name, so ShellCraft reads it as Python and looks for a variable with that name.
`while -py true:` stops with:

```
unknown name 'true' (variables here: status; quote text: "true")
```

Write `True` with a capital T, as in Python. For a command with no arguments, such as `ls`, use the
bash style or add an argument.

### What a Python condition can use

You get Python's expressions, without imports or your own modules:

- Comparisons, `and`, `or`, `not`, `in`, `not in`, and the `x if test else y` form.
- Sums with `+`, `-`, `*`, `/`, `//` and `%`.
- Lists, tuples, indexing and slices: `line.split()[0]`, `name[:3]`.
- The functions `len`, `int`, `float`, `str`, `bool`, `abs`, `min` and `max`.
- `match(REGEX, TEXT)`, true when the regular expression is found anywhere in the text.
- `exists(PATH)`, `isfile(PATH)` and `isdir(PATH)` for files and folders.
- Text methods: `.lower()`, `.upper()`, `.title()`, `.strip()`, `.split()`, `.replace()`,
  `.startswith()`, `.endswith()`, `.find()`, `.count()`, `.isdigit()`, `.removeprefix()` and a few
  more. Anything else, like `.capitalize()`, is refused with
  `method '.capitalize()' is not available`.
- `status`, the exit status of the last command.
- Any function you wrote, called with brackets (see [Functions with def](#functions-with-def)).

`man if` lists everything.

## Variables are text

This is the difference from Python that matters most. A shell variable always holds text, even
when it looks like a number. In a Python condition, `port` is the text `"8080"`, not the number
8080, so comparing it to a number quietly gives the wrong answer:

<!-- run -->
```
port=8080
if -py port == 8080:
    echo "matched"
else:
    echo "no match: port is the text '8080'"
if -py int(port) == 8080:
    echo "matched once it's a number"
```

<!-- output -->
```
no match: port is the text '8080'
matched once it's a number
```

The first `if` compares the text `"8080"` with the number 8080. In Python those are never equal,
so the `else` runs. The second one turns the text into a number with `int()` first. Do that every
time you compare or add numbers. A loop variable is text too: in `for -py n in 1..3`, `n` is `"1"`,
`"2"` and `"3"`, which is why the examples on this page write `n == "2"` or `int(n) == 2`.

`<` and `>` are louder about it. `if -py port > 1000:` stops with:

```
'>' not supported between instances of 'str' and 'int' (loop variables are text: use int(name) to compare numbers)
```

And when the text isn't a number at all, `int()` says so:
`ValueError: invalid literal for int() with base 10: 'abc'`.

You name a variable without `$` in Python: `port`, not `$port`. `$port` works too and means the
same. A name that has no variable behind it is an error, which also catches typos and text you
forgot to quote:

```
unknown name 'nope' (variables here: status; quote text: "nope")
```

## Set a variable

You'll want this whenever a loop keeps a running total, or a block works something out to use
later.

Bash style writes `name=value`, with no spaces around `=`, and does sums in `$(( ))`:

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

Python style writes `name = value`, with a Python expression on the right, and `+=` to add to a
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

The `if` sets `count` to 0 on the first pass. `count += int(n)` reads `count` back as a number,
adds `n`, and stores the result as text again. After the block, `count` is an ordinary shell
variable, so `echo "total: $count"` prints it.

`-=`, `*=`, `/=`, `//=` and `%=` work too. The value can be anything a Python condition can use:
`name = name.strip().lower()`, `total = int(a) * 2`, or
`kind = "even" if int(n) % 2 == 0 else "odd"`. Whatever you assign is stored as text, so
`int(count)` reads one back as a number; `count += 1` does that for you. `/` always gives a
fraction (`2.0`), and `//` a whole number.

Some things work differently from Python:

- Assignment only works inside a Python-style block or a `def`. At the prompt or in a bash block,
  `x = 5` with spaces runs a command named `x`, as it would in bash, and you see
  `command not found: x`. `name=value` with no spaces works everywhere. The exception is
  `name = input("…")`, which works anywhere (see [Ask the user](#ask-the-user)).
- `+=` with text on the right joins the text: `total += size` turns `"0"` into `"0120"`. Write
  `total += int(size)` to add.
- `$name` isn't filled in inside a Python string. `msg = "file $n"` stores `file $n` as it is.
  Use an f-string instead: `msg = f"file {n}"` (see the next section).
- `True` and `False` are stored as the text `"True"` and `"False"`. Any text that isn't empty
  counts as true, so `if -py ok:` runs even when `ok` holds `"False"`. Compare it instead:
  `if -py ok == "True":`.
- `$( )` doesn't run in a Python value. `out = $(date)` is refused with
  `$( … ) is bash: write out=$(command), with no spaces`. Do what it says:
  `out=$(date)` works inside any block.

## Build text with f-strings and print

Sooner or later a script has to say something built from its variables: a summary line, a
table, a file name with a date in it. In Python you'd reach for an f-string and `print()`, and
you can here too. Inside a Python-style block or a `def`:

<!-- run -->
```
name="Ada"
count=3
for -py _ in 1:
    msg = f"{name} has {count} files"
    print(msg)
    print(f"{name.upper()} will have {int(count) + 1} tomorrow")
```

<!-- output -->
```
Ada has 3 files
ADA will have 4 tomorrow
```

Anything between `{ }` is a Python expression, the same kind a condition can use. Remember that
variables are text, so `{int(count) + 1}` turns `count` into a number before adding.

`print()` works like Python's: give it several values and it puts a space between them. `sep=`
changes what goes between them and `end=` what goes at the end (a new line, unless you say
otherwise). Its output goes where `echo`'s would: to the screen, into a pipe after the block, or
into `$( )`.

<!-- run -->
```
for -py n in 1..3:
    print("line", n, sep=" #", end=".\n")
```

<!-- output -->
```
line #1.
line #2.
line #3.
```

### Line things up with format specs

After a `:` inside the braces you can say how a value is laid out. This is Python's format
language, and it's what you use for tables and money:

<!-- run -->
```
for -py n in 8 9 10:
    print(f"{n:>3} | {int(n) * int(n):>4} | {int(n) * 1000:,}")
```

<!-- output -->
```
  8 |   64 | 8,000
  9 |   81 | 9,000
 10 |  100 | 10,000
```

| Spec | What it does | `f"…"` | Gives |
| --- | --- | --- | --- |
| `<10`, `>10`, `^10` | pad to 10 characters, on the left, right or both sides | `f"{name:>6}"` | `   Ada` |
| `*^9` | pad with `*` instead of spaces | `f"{name:*^9}"` | `***Ada***` |
| `.2f` | two decimal places | `f"{price:.2f}"` | `3.14` |
| `,` | a comma between thousands | `f"{n:,}"` | `1,234,567` |
| `05d` | a whole number, padded with zeros | `f"{n:05d}"` | `00042` |
| `.0%` | a percentage | `f"{0.5:.0%}"` | `50%` |
| `!r` | the value as Python would show it, quotes and all | `f"{name!r}"` | `'Ada'` |
| `>{w}` | a width from another variable | `f"{name:>{w}}"` | `   Ada` when `w` is 6 |

A spec for numbers (`d`, `f`, `%`, `,` and the like) reads a variable's text as a number for
you, so `f"{price:.2f}"` works even though `price` holds the text `"3.14159"`. If the text isn't
a number, you'll see `format: 'abc' is not a number (for ':.2f')`. To print a brace itself, double
it: `f"{{"` is `{`.

### .format()

`.format()` does the same job when you'd rather keep the pattern apart from the values. `{}`
takes the next value, `{0}` a value by its position, and `{name}` one you pass by name:

<!-- run -->
```
for -py _ in 1:
    print("{}-{}".format("web", 1))
    print("{0} and {0} again".format("echo"))
    print("{host}:{port:>5}".format(host="db1", port=80))
```

<!-- output -->
```
web-1
echo and echo again
db1:   80
```

### What's refused, and why

Python code in a script runs in a small, safe subset, because scripts and AI clients can both
send it. So a few things that Python allows are refused, each with a message that says why:

- In `.format()`, a field is `{}`, a number or a name. `{0.attr}` and `{0[key]}` are refused
  (`format: field '{0.__class__}' is not allowed`), because they're the usual way to reach
  Python's insides from a format string.
- A width or precision over 100000 is refused (`format: 999999999 in '>999999999' is too big`),
  and so is any result longer than 100000 characters. A typo can't fill your memory.
- Keyword arguments only work in `.format(name=…)` and in `print(sep=…, end=…)`.

### Where print works

`print(…)` is a line of its own inside a Python-style block or a `def`. At the prompt and in
bash lines, `print` isn't a command: there you use `echo` or `printf`. One `print` always ends
its own line, so `end=""` can't join two of them; build the whole line first and print it once.
If a value can't be worked out, the line reports the error and its status is 1, which `set -e`
and `try` see like any other failing command.

## if, elif, else

You'll reach for this whenever a script has to choose, such as greeting differently by time of
day.

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

`elif` and `else:` line up with the `if`, as in Python, and don't take `-py`. `int(hour)` turns
the text into a number before comparing it. [Conditions and choices](conditions.md) explains the
bash tests in more depth.

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

Notice that the bash version needs quotes around `"$name"` because the value has a space in it,
and the Python version doesn't, because `name` is one Python value however many spaces it holds.

## Test a command

Sometimes the question is "did this command work?", such as "does the log contain an error?".
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

Python style does the same. `grep -q ERROR app.log` isn't Python, so it runs as a command, and the
condition is true when the command succeeds:

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

`status` is the last command's exit status, so you can also run the command first and check it on
the next line: `if -py status != 0:`.

## Files and folders

Before writing a backup, you'd check that the folder is there and that you won't overwrite an old
one. Bash style:

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

The path is quoted here because it's text, not a variable. `isdir(folder)`, with no quotes, would
check the folder named in the variable `folder`.

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

The list after `in` is shell words, not a Python list: no brackets and no commas.

## for: counting

Bash style counts with `{FIRST..LAST}`, and Python style with `FIRST..LAST`. Unlike Python's
`range()`, the last number is included. A third number is the step:

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
which splits it into words. Python style puts the command in `( )`, and gives you one whole line at
a time, spaces and all. Reach for the Python form when you read a list of servers or a CSV file,
where a line is the unit you care about:

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

The bash loop sees six words, because `$( )` splits at spaces as well as line ends. The Python
loop sees three lines.

To take a line apart, use `.split()` as you would in Python:

<!-- run -->
```
printf "web1 frontend\ndb1 database\n" > hosts.txt
for -py line in (cat hosts.txt):
    host = line.split()[0]
    role = line.split()[1]
    echo "$host is a $role server"
```

<!-- output -->
```
web1 is a frontend server
db1 is a database server
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

A `while` loop repeats as long as its condition holds, such as counting down or waiting for a file
to appear. Bash style:

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
`while -py True:`, with a capital T (see [Conditions: Python or a command](#conditions-python-or-a-command)).

## break and continue

They work the same in both styles: `continue` skips to the next pass, and `break` leaves the loop.
Here they are in Python style:

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

The bash version of the same thing needs a `done` and a `fi` for each block it opens:

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

## match: one of several choices

When one value can be one of several things, a file type or a menu choice, a chain of `elif`s gets
long. Bash uses `case` for this:

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

Python style uses `match`, as Python does: each choice is `case`, a pattern and a `:`, with its
commands indented under it. `|` separates alternatives, and `_` is "anything else":

<!-- run -->
```
for -py file in notes.txt photo.jpg script.py:
    match file:
        case "*.txt" | "*.md":
            echo "$file: text"
        case "*.jpg" | "*.png":
            echo "$file: picture"
        case _:
            echo "$file: something else"
```

<!-- output -->
```
notes.txt: text
photo.jpg: picture
script.py: something else
```

It looks like Python's `match`, but the patterns are shell patterns, the kind you use for file
names. That's what makes `"*.txt"` work:

- `match file:` and `match $file:` are the same: both read the variable.
- A pattern can be exact text (`"yes"`) or use `*`, `?` and `[ ]`. `"[yY]*"` is anything that
  starts with y or Y.
- Only the first choice that matches runs. When none matches, nothing runs.
- A short choice fits on one line: `case "q": echo bye`.
- Python's `if` guards (`case x if …:`) aren't supported, and say
  `match: no 'if' guards on a choice; test inside it instead`. Put an `if` inside the choice.
  Class patterns and capture patterns aren't supported either.

The other spelling is `case -py $file:` with bare patterns, `"*.txt" | "*.md":` and `_:`. It works
the same way:

<!-- run -->
```
case -py notes.txt:
    "*.txt":
        echo "a text file"
    _:
        echo "something else"
```

<!-- output -->
```
a text file
```

## Ask the user

Scripts that set something up often need an answer: a name, a yes or no, a number. Both ways of
asking work in both styles. `read -p "QUESTION" NAME` is the bash way, and
`NAME = input("QUESTION")` is the Python way. Unlike other Python assignments,
`name = input(…)` works anywhere, even outside a block.

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

`.lower()` means "Y", "yes" and "Yes" all count. `read -s` hides what's typed, for passwords and
tokens. The question in `input()` can use variables, unlike other Python strings:
`input("Age of $name? ")`. It can also be an f-string, which lets you work the question out:

<!-- run: ["7", "9"] -->
```
for -py who in ada sam:
    age = input(f"Age of {who.title()}? ")
    print(f"{who.title()} is {age}")
```

<!-- output -->
```
Age of Ada? 7
Ada is 7
Age of Sam? 9
Sam is 9
```

### Keep asking until the answer is good

A required answer can't be empty, so ask again until it isn't. Bash style:

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

For a number in a range, `int(input(…))` does half the work: it keeps asking until the answer is
a whole number. The `while` does the other half and checks the range:

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

"seven" is refused by `int(input(…))` itself, which asks again on the spot. "12" is a whole number,
so `input` accepts it, but the `while` condition sees it's out of range and goes round again.

## Menus

A menu that keeps asking until you quit is a loop around a choice. In bash style:

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
    match choice:
        case "1":
            echo "hello!"
        case "q" | "Q":
            echo "bye"
            break
        case _:
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

The `break` inside the `case "q"` choice leaves the `while` loop, not the `match`.

### A numbered menu with select

`select` numbers the choices for you and puts the one picked in a variable. Bash style:

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

`PS3` is the question `select` asks. When the answer isn't one of the numbers, `env` is empty and
`REPLY` holds what was typed, so the `if` explains and asks again. `select -py` takes its choices
like `for -py`, so `select -py host in (cat hosts.txt):` offers one line of the file per choice.

## Functions with def

A function gives a name to a few commands you run often, like greeting someone or checking a path.

### Arguments and defaults

Bash style is `name() { … }`, with the arguments in `$1`, `$2` and so on:

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

Python style uses `def`, with the arguments named in the brackets. A parameter with `=` has a
default, and one without is required:

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

You call it like any shell command, `greet Ada`, with spaces and no brackets. Typing `greet` alone
stops with `greet: missing argument 'name'`, followed by how to use it, because `name` has no default.

The parameters are local: they belong to that one call, and a shell variable with the same name
comes back unchanged afterwards. `$1`, `$#` and `$@` still work, so extra arguments aren't lost.

Inside a `def`, conditions are Python with no `-py`, and `return 1` gives a failing status, the
way a bash function would:

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

`||` runs the command on its right only when the one on its left fails. `$?` is the status the
function returned.

### Ask from inside a function

Asking works in either style. Here it's `input()` in a `def`:

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

### return gives back a value

In bash, a function's `return` is only a status, a number from 0 to 255, and the way to hand back
text is to print it. In a `def`, `return` gives back a Python value, and what you get depends on how
you call the function:

- Called as a command (`add 1200 34`), a number from 0 to 255 becomes the exit status, `True` and
  `False` become 0 and 1, and anything else is printed. A plain `return` is status 0.
- Called from Python with brackets (`add(4, 4)`), you get the value itself.

<!-- run -->
```
def add(a, b):
    return int(a) + int(b)

def is_weekend(day):
    return day in ("Sat", "Sun")

def plan(day):
    if is_weekend(day):
        return day + ": rest"
    hours = add(4, 4)
    return day + ": work " + hours + " hours"

plan Sat
plan Mon
add 1200 34
echo "$(add 1200 34)"
```

<!-- output -->
```
Sat: rest
Mon: work 8 hours
1234
1234
```

`is_weekend(day)` returns `True` or `False`, so the `if` in `plan` reads like Python. `add(4, 4)`
returns the number 8, and `hours = …` stores it as the text `"8"`, which is why
`"…work " + hours` can join it to other text. `add 1200 34` is called as a command, and 1234 is
more than 255, so it's printed rather than used as a status. `$( )` captures that printed text.

Mind the difference between printing and returning. `echo` inside a function writes to the screen,
or into `$( )`. `return` hands a value back to the Python code that called it. A Python string
doesn't fill in `$name`, so build a returned string with `+`, as `plan` does.

### Call a bash function from Python

A bash function called from Python gives back what it printed, with the last newline taken off.
That lets you use any shell tool inside a Python expression:

<!-- run -->
```
slug() { echo "$1" | tr "A-Z " "a-z-"; }

def save_post(title):
    name = slug(title) + ".md"
    echo "saving $name"

save_post "Hello World"
```

<!-- output -->
```
saving hello-world.md
```

`slug` is bash: it pipes its argument through `tr` to make it lower case with dashes. `slug(title)`
in the `def` runs it and gets `hello-world` back as text. Calling a name that isn't a function
stops with `foo is not a function`, followed by the list of built-in ones.

[Functions](functions.md#call-a-function-from-python) has the details. Giving back a value with
`echo` and `$( )`, and keeping functions in your rc file, work the same as in
[Functions](functions.md).

## Handle errors with try

A script that copies files or edits a config should stop and say so when one step fails, and clean
up after itself either way. Bash style checks each command that might fail. `if ! cmd; then` runs a
block when it fails, and `cmd || other` runs `other`:

<!-- run -->
```
printf 'name=web\nport=80\n' > app.conf
if ! grep -q "^host=" app.conf; then
  echo "no host set"
fi
grep -q "^port=" app.conf || echo "no port set"
```

<!-- output -->
```
no host set
```

To stop at the first failure in bash, you turn on [`set -e`](errors.md#stop-on-errors-with-set--e)
and clean up in a [trap](errors.md#clean-up-with-trap): `trap 'rm -f run.lock' EXIT`.

Python style wraps the commands in `try:`. It looks like Python's, but there are no exceptions
here: a command fails when its exit status isn't 0. The first command that fails skips the rest of
the body and runs `except:`. `except as rc:` puts the failing status in `rc`. `finally:` runs at the
end whatever happened:

<!-- run -->
```
printf 'name=web\nport=80\n' > app.conf
try:
    grep -q "^name=" app.conf
    echo "name found"
    grep -q "^host=" app.conf
    echo "host found"
except as rc:
    echo "a setting is missing (status $rc)"
finally:
    echo "check done"
```

<!-- output -->
```
name found
a setting is missing (status 1)
check done
```

The first `grep` finds `name=` and succeeds, so the body goes on. The second finds no `host=` and
fails with status 1, so `echo "host found"` is skipped and `except` runs with `rc` set to 1.
`finally` runs last.

The short version of the rules:

- A Python line that goes wrong counts as a failure too, such as a condition with an unknown name.
- Tests don't count: a command in an `if` or `while` condition, on the left of `&&` or `||`, or
  after `!`.
- A function you call in the body counts as one command.
- You need `except:`, `finally:` or both. There are no error types, so `except ValueError:` is
  refused with `there are no error types here; use 'except:', or 'except as status:' for the failing status`.
- `finally:` also runs when `break`, `continue`, `return` or `exit` leaves the block, so it's the
  place to delete a lock file or a temporary folder.

A short body fits after the colon: `try: grep -q ERROR app.log` and `except: echo "no errors"`.
In a bash function, use the brace form, `try { … } except as rc { … }`.
[Catch failures with try](errors.md#catch-failures-with-try) has the full story: the brace form,
how `try` works with `set -e` and the ERR trap, and what to check when it doesn't catch something.
`man try` has it too.

## Mixing the styles

You don't have to pick one style for a whole script. Use whichever reads best for each block. A
Python-style block can hold bash lines, and a bash function can hold a Python-style block:

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

Inside the `for -py` loop, the `if [ … ]; then … fi` is bash, because it ends in `then` rather
than `:`. A line keeps the style it's written in. The same goes for variables: one set the Python
way with `count = 0` is an ordinary shell variable afterwards, and `$count` reads it in a bash line.

The brace form, `for -py n in 1..3 { echo $n }`, is the Python style on one line, and still works
everywhere.

## In your rc file, startup files and funcs

Python-style blocks work in `~/.shellcraft/rc`, in `~/.shellcraft/startup/*.sc`, in funcs, with
`source`, and at the prompt. In a func, a whole block counts as one step.

## When the indentation is wrong

Indentation is part of the syntax, so a file with a block that's indented wrongly doesn't run at
all. ShellCraft says which line to fix, the way Python does. For example, this loop forgot to
indent its body:

```
for -py n in 1..2:
echo $n
```

If that's in a file called `count.sc`, `source -n count.sc` checks it without running anything
and shows:

```
count.sc:2: expected an indented block
1 problem (1 error)
```

Running the file, or typing it at the prompt, gives the same message with the line it's about.
The messages you can get:

| Message | What's wrong |
| --- | --- |
| `expected an indented block` | The line after a `:` isn't indented |
| `unindent does not match any outer indentation level` | A line goes back to an indent no block uses |
| `inconsistent use of tabs and spaces in indentation` | A block mixes tabs and spaces |
| `'else' without a matching if` | An `else:` that doesn't line up with an `if` |
| `'except' without a matching try` | An `except:` or `finally:` that doesn't line up with a `try:` |
| `try: needs an 'except:' or a 'finally:'` | A `try:` block with nothing after it |
| `match: each choice is case PATTERN:` | A choice in a `match` block without `case` in front |

## Common mistakes

**You forgot the colon.** `if -py int(n) > 3` with no `:` isn't a Python-style header, so
ShellCraft reads it as the brace form and stops with `if: missing '{'`. Add the `:`.

**You forgot `-py`.** `if n == "3":` at the top of a script is read as bash, which wants a
`then`, so it stops with `missing 'then'`. Inside a Python-style block or a `def` you can leave `-py` out, but not on the outer
block.

**A comparison is always false.** You compared a variable with a number, as in `n == 3`. Variables
are text, so write `n == "3"` or `int(n) == 3`. See [Variables are text](#variables-are-text).

**`while -py true:` says `unknown name 'true'`.** Python's true is `True`.

**`$name` shows up as it is.** You put it in a Python string, such as `msg = "hi $name"`. Write
`msg = f"hi {name}"`. Inside the braces, leave the `$` out too: `f"{$name}"` is refused.

**An f-string prints as typed.** `echo f"{name}"` is a bash line, so the shell doesn't know
about f-strings and prints `f{name}`. Use `print(f"{name}")` inside a Python-style block, or
`echo "$name"`.

**`print("x")` says `command not found`.** You used it at the prompt or in a bash block.
`print` only works inside a Python-style block or a `def`. Use `echo` there.

**`if -py ok:` runs even though `ok` is `False`.** The variable holds the text `"False"`, which
isn't empty. Write `if -py ok == "True":`.

## Side by side

Use this table to translate a bash example into Python style or back, one line at a time.

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
| Words of a command's output | `for w in $(cat f); do` | (use the line form) |
| Lines of a file | (no direct form) | `for -py line in (cat f):` |
| While | `while [ … ]; do … done` | `while -py …:` |
| Forever | `while true; do` | `while -py True:` |
| Choices | `case $x in a) … ;; *) … ;; esac` | `match x:` then `case "a":` and `case _:` |
| Numbered menu | `select x in a b; do … done` | `select -py x in a b:` |
| Print | `echo "$a-$b"` | `print(f"{a}-{b}")` (in a block) |
| Print, padded | `printf "%5s\n" "$a"` | `print(f"{a:>5}")` (in a block) |
| Ask | `read -p "Q? " x` | `x = input("Q? ")` |
| Ask for a number | `read -p "N? " n` and a test | `n = int(input("N? "))` |
| Function | `f() { local a=$1; … }` | `def f(a):` |
| Give back a value | `echo` it, and `$(f)` | `return value` |
| Catch a failure | `if ! cmd; then … fi` | `try:` then `except:` |
| Stop at the first failure | `set -e` | `try:` |
| Always clean up | `trap 'rm -f lock' EXIT` | `finally:` |
| Do nothing | `:` or `true` | `pass` |
