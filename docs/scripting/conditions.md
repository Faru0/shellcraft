# Conditions and choices

← [Scripting guide](README.md)

This page shows you how to make a script decide: run something only when a condition holds, pick
between two or more paths, compare text and numbers, check files, and choose between many options
with `case`.

## if, then, else

An `if` runs its commands only when the condition holds. Write it over several lines, the same way
in a script and at the prompt:

<!-- run -->
```
hour=9
if [ $hour -lt 12 ]; then
  echo "Good morning"
fi
```

<!-- output -->
```
Good morning
```

The parts are: `if`, the condition, `; then`, the commands, and `fi` (`if` backwards) to close it.
Indenting the commands isn't required, but it makes the script easier to read.

Add `else` for what to do otherwise, and `elif` ("else if") for more checks in between. Only the
first branch that matches runs:

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

A short `if` fits on one line. Put a `;` where the line breaks would be:

<!-- run -->
```
count=3
if [ $count -gt 0 ]; then echo "$count new messages"; else echo "no messages"; fi
```

<!-- output -->
```
3 new messages
```

### Nested ifs

An `if` can go inside another one. Each needs its own `fi`:

<!-- run -->
```
env=prod
confirmed=yes
if [ $env = prod ]; then
  if [ $confirmed = yes ]; then
    echo "deploying to production"
  else
    echo "production needs confirmed=yes"
  fi
else
  echo "deploying to $env"
fi
```

<!-- output -->
```
deploying to production
```

## Conditions are commands

The condition is a command. The `if` branch runs when that command succeeds, which means its exit
status is 0. `[ … ]` is a command that checks something and succeeds when it's true. Any other
command works too, like `grep -q`, which succeeds when it finds a match:

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

`$?` holds the exit status of the last command: 0 when it succeeded, anything else when it didn't.

## Comparing text

Inside `[ ]`, put spaces after `[`, before `]`, and around every word. Quote variables, so an empty
value or one with spaces still works:

| Test | True when |
| --- | --- |
| `[ "$a" = "$b" ]` | The two are the same text. `==` works too |
| `[ "$a" != "$b" ]` | They're different |
| `[ -z "$a" ]` | `a` is empty, or not set |
| `[ -n "$a" ]` | `a` has something in it |

<!-- run -->
```
answer=""
if [ -z "$answer" ]; then echo "you didn't answer"; fi
color=blue
if [ "$color" = "blue" ]; then echo "blue it is"; fi
if [ "$color" != "red" ]; then echo "not red"; fi
```

<!-- output -->
```
you didn't answer
blue it is
not red
```

## Comparing numbers

Numbers use letters instead of symbols, because `<` and `>` mean "read from" and "write to" a file:

| Test | Means |
| --- | --- |
| `-eq` | Equal |
| `-ne` | Not equal |
| `-lt` | Less than |
| `-le` | Less than or equal |
| `-gt` | Greater than |
| `-ge` | Greater than or equal |

<!-- run -->
```
disk=91
if [ $disk -ge 90 ]; then
  echo "disk is almost full: $disk%"
elif [ $disk -ge 75 ]; then
  echo "disk is filling up: $disk%"
fi
```

<!-- output -->
```
disk is almost full: 91%
```

To compare the result of a sum, work it out with `$(( ))` first: `[ $((a + b)) -gt 10 ]`.

## Checking files and folders

| Test | True when |
| --- | --- |
| `[ -e PATH ]` | It exists, file or folder |
| `[ -f PATH ]` | It's a file |
| `[ -d PATH ]` | It's a folder |
| `[ -s PATH ]` | It's a file with something in it |
| `[ -r PATH ]`, `[ -w PATH ]`, `[ -x PATH ]` | You can read it, write it, run it |

<!-- run -->
```
mkdir -p backups
if [ -d backups ]; then echo "the backups folder is there"; fi
if [ ! -f backups/latest.tar ]; then echo "no backup yet"; fi
```

<!-- output -->
```
the backups folder is there
no backup yet
```

A `!` in front turns a test around: `[ ! -f x ]` is true when `x` is *not* a file.

## Combining conditions

**Inside one test**, `-a` means "and" and `-o` means "or":

<!-- run -->
```
hour=10
day=Mon
if [ $hour -ge 9 -a $hour -lt 17 ]; then echo "working hours"; fi
if [ "$day" = Sat -o "$day" = Sun ]; then echo "weekend"; else echo "weekday"; fi
```

<!-- output -->
```
working hours
weekday
```

**Between commands**, `&&` runs the next one only if the first succeeded, and `||` only if it
failed. They make a quick one-line `if`:

<!-- run -->
```
mkdir -p logs
[ -d logs ] && echo "logs folder ready"
[ -f settings.toml ] || echo "no settings.toml, using the defaults"
```

<!-- output -->
```
logs folder ready
no settings.toml, using the defaults
```

You can chain them: `[ -f app.log ] && echo found || echo missing`. Use a full `if` when the
branches have more than one command each.

## Patterns with [[ ]]

`[[ … ]]` is a stronger test. It does everything `[ ]` does, plus:

- `==` with a pattern: `*` is any text, `?` any one character.
- `=~` with a regular expression.
- `&&`, `||` and `( )` inside the brackets.
- Variables are never split, so quotes are optional.

<!-- run -->
```
file=report-2026.csv
if [[ $file == *.csv ]]; then echo "a CSV file"; fi
if [[ $file == report-* ]]; then echo "a report"; fi
version=v12
if [[ $version =~ ^v[0-9]+$ ]]; then echo "a valid version"; fi
if [[ -n $file && $file != *.tmp ]]; then echo "worth keeping"; fi
```

<!-- output -->
```
a CSV file
a report
a valid version
worth keeping
```

## case: pick one of several choices

When you'd write `if … elif … elif … elif`, a `case` is shorter and easier to read. It compares one
value against a list of patterns and runs the first one that matches:

<!-- run -->
```
env=staging
case $env in
  dev)
    echo "local machine, anything goes"
    ;;
  staging)
    echo "the test server"
    ;;
  prod)
    echo "careful: production"
    ;;
  *)
    echo "unknown environment: $env"
    ;;
esac
```

<!-- output -->
```
the test server
```

How it's built:

- `case VALUE in` starts it, and `esac` (`case` backwards) ends it.
- Each choice is a pattern, a `)`, its commands, and `;;` to end that choice.
- `*)` matches anything, so put it last as the "none of the above" choice.
- When a short choice fits on one line, write it that way: `dev) echo "local" ;;`

A pattern can list several values with `|`, and use `*`, `?` and `[ ]` like file names do:

<!-- run -->
```
for file in notes.txt photo.JPG script.py data.csv; do
  case $file in
    *.txt|*.md) echo "$file: text" ;;
    *.jpg|*.JPG|*.png) echo "$file: picture" ;;
    *.py) echo "$file: Python" ;;
    *) echo "$file: something else" ;;
  esac
done
```

<!-- output -->
```
notes.txt: text
photo.JPG: picture
script.py: Python
data.csv: something else
```

### A menu with case

`case` and `read` together make a simple menu. Print the choices, ask, then act on the answer:

<!-- run: ["2"] -->
```
echo "1) Start the dev server"
echo "2) Run the tests"
echo "3) Quit"
read -p "Choose 1-3: " choice
case $choice in
  1) echo "starting the server" ;;
  2) echo "running the tests" ;;
  3) echo "bye" ;;
  *) echo "'$choice' isn't on the menu" ;;
esac
```

<!-- output -->
```
1) Start the dev server
2) Run the tests
3) Quit
Choose 1-3: 2
running the tests
```

To keep showing the menu until the user picks Quit, put it in a loop, or use `select`, which numbers
the choices for you. Both are in [Loops and menus](loops.md#menus-that-keep-asking).

## Python conditions with -py

If you know Python, `if -py` lets you write the condition in Python instead. Variables are used by
their plain names, without `$`, and the blocks use `{ }`:

<!-- run -->
```
name="Ada Lovelace"
age=36
if -py (int(age) >= 18 and name.startswith("Ada")) {
  echo "an adult named Ada"
}
if -py ("Love" in name) { echo "the name contains Love" }
if -py (len(name) > 20) { echo "a long name" } else { echo "a short name" }
```

<!-- output -->
```
an adult named Ada
the name contains Love
a short name
```

Variables are always text, so wrap a number in `int()` before comparing it. You can use
comparisons, `and`, `or`, `not`, `in`, text methods like `.lower()`, `.startswith()` and
`.endswith()`, and the functions `len`, `int`, `float`, `str`, `min`, `max`, `abs`,
`match(REGEX, TEXT)`, `exists(PATH)`, `isfile(PATH)` and `isdir(PATH)`. `status` is the last
command's exit status. Imports aren't allowed. `man if` lists everything.

<!-- run -->
```
touch notes.txt
if -py (exists("notes.txt") and status == 0) { echo "notes.txt is there" }
if -py (isdir("notes.txt")) { echo "a folder" } elif (isfile("notes.txt")) { echo "a file" }
```

<!-- output -->
```
notes.txt is there
a file
```

Use whichever style you find easier to read. You can mix them in the same script.
