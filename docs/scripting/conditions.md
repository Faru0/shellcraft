# Conditions and choices

← [Scripting guide](README.md)

This page is for when your script needs to decide something: back up only if the folder exists,
warn when the disk is nearly full, do one thing for `dev` and another for `prod`. After reading
it you'll be able to write an `if` with `elif` and `else`, compare text and numbers, check files
and folders, combine checks with "and" and "or", and pick between many options with `case`. The
examples are in bash style first. The last section shows the same things in the Python style.

## if, then, else

An `if` runs its commands only when the condition holds. Say you want a greeting that depends on
the time of day. Write it over several lines, the same way in a script and at the prompt:

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
Here the condition is `[ $hour -lt 12 ]`, "hour is less than 12". `$hour` has a `$` because you
want the variable's value, 9, not the word "hour". Indenting the commands isn't required, but it
makes the script easier to read.

Add `else` for what to do otherwise, and `elif` ("else if") for more checks in between. ShellCraft
tries them from the top, and only the first branch that matches runs:

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

15 isn't less than 12, so the first branch is skipped. 15 is less than 18, so "Good afternoon"
prints, and the `else` is never looked at.

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

An `if` can go inside another one. You'd do that when a second question only makes sense once the
first is answered, like asking for confirmation only on production. Each `if` needs its own `fi`:

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

This is the idea that makes the rest of the page make sense. The condition of an `if` is a
command. The `if` branch runs when that command succeeds.

Every command finishes with an exit status, a number. 0 means it succeeded, and anything else
means it didn't. That's the opposite of what you might expect from other languages, where 0 is
false: in the shell, 0 is "no problems". `$?` holds the exit status of the last command:

<!-- run -->
```
echo "ERROR disk full" > app.log
grep -q ERROR app.log
echo "found it: $?"
grep -q WARNING app.log
echo "not found: $?"
```

<!-- output -->
```
found it: 0
not found: 1
```

`grep -q` prints nothing and succeeds when it finds a match. So it can go straight after `if`,
which is how you'd check a log for errors:

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

`[ … ]` is a command too. Its whole job is to check something and succeed when it's true. That's
why it needs spaces around it like any other command: `[` is its name, and `]` is its last
argument.

## Comparing text

Use these when you check an answer, a name or a setting. Inside `[ ]`, put spaces after `[`,
before `]`, and around every word. Quote variables, so an empty value or one with spaces still
works:

| Test | True when |
| --- | --- |
| `[ "$a" = "$b" ]` | The two are the same text. `==` works too |
| `[ "$a" != "$b" ]` | They're different |
| `[ -z "$a" ]` | `a` is empty |
| `[ -n "$a" ]` | `a` has something in it |

`-z` checks for an empty value, so give the variable one first (`answer=""`). A variable that was
never set isn't empty in ShellCraft: `$a` stays as the text `$a`, so `[ -z "$a" ]` is false. See
[a variable that was never set](variables-and-input.md#set-a-variable-and-use-it).

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

Why the quotes matter: the shell puts the value in place of `$answer` before `[` sees it. If
`answer` is empty and you write `[ $answer = yes ]`, `[` sees `[ = yes ]`, which makes no sense,
and you get:

```
[: unexpected argument 'yes'
```

With quotes, `[ "$answer" = yes ]`, it sees an empty piece of text and says "not equal", which is
what you meant. The same thing happens when the value has a space: `name="Ada L"` and
`[ $name = "Ada L" ]` fails with `[: unexpected argument 'L'`.

## Comparing numbers

Use these for counts, sizes, percentages and times. Numbers use letters instead of symbols,
because `<` and `>` mean "read from" and "write to" a file:

| Test | Means |
| --- | --- |
| `-eq` | Equal |
| `-ne` | Not equal |
| `-lt` | Less than |
| `-le` | Less than or equal |
| `-gt` | Greater than |
| `-ge` | Greater than or equal |

Here's a disk check that warns at two levels:

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

Put the bigger threshold first. If the 75 check came first, 91 would match it and the "almost
full" branch would never run, because only the first branch that matches runs.

To compare the result of a sum, work it out with `$(( ))` first: `[ $((a + b)) -gt 10 ]`.

`=` compares text and `-eq` compares numbers, and they can disagree: `[ 10 = 10.0 ]` is false
because the text differs, while `[ 10 -eq 010 ]` is true because the numbers are the same. A value
that isn't a whole number can't be compared with `-eq` and the others:

```
[: abc: integer expression expected
```

## Checking files and folders

Scripts that back up, clean up or install things need to know what's there before they act. These
tests check a path:

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

A `!` in front turns a test around: `[ ! -f x ]` is true when `x` is *not* a file. Leave a space
after the `!`.

## Combining conditions

Often one check isn't enough: "during working hours **and** on a weekday", "Saturday **or**
Sunday". There are two ways to combine them.

Inside one test, `-a` means "and" and `-o` means "or":

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

Between commands, `&&` runs the next one only if the first succeeded, and `||` only if it failed.
They make a quick one-line `if`, handy for "create it if it's missing" or "warn if it's not there":

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

The first line's test succeeds, so `&&` runs the `echo`. In the second, there's no
`settings.toml`, so the test fails and `||` runs its `echo`.

You can chain them: `[ -f app.log ] && echo found || echo missing`. Use a full `if` when the
branches have more than one command each.

## Patterns with [[ ]]

`[[ … ]]` is a stronger test. Reach for it when you want to match part of a value, such as "does
this file name end in `.csv`?". It does everything `[ ]` does, plus:

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

The regular expression `^v[0-9]+$` means "starts with v, then one or more
digits, then the end".

## case: pick one of several choices

When you'd write `if … elif … elif … elif`, all comparing the same variable, a `case` is shorter
and easier to read. It compares one value against a list of patterns and runs the first one that
matches:

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
- `*)` matches anything, so put it last as the "none of the above" choice. Anything after it
  would never be reached.
- When a short choice fits on one line, write it that way: `dev) echo "local" ;;`

A pattern can list several values with `|`, and use `*`, `?` and `[ ]` like file names do. This is
how you'd sort files by type:

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

Patterns are case sensitive, which is why `*.JPG` is listed next to `*.jpg`. To accept "y", "Y",
"yes" and "Yes" in one pattern, write `[yY]*`.

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

`read -p` shows the question and stores the answer in `choice`. The `*)` choice catches anything
that isn't on the menu, including an empty answer.

To keep showing the menu until the user picks Quit, put it in a loop, or use `select`, which numbers
the choices for you. Both are in [Loops and menus](loops.md#menus-that-keep-asking).

## Python style

Everything on this page can be written in the Python style too, which reads more naturally if you
know Python. Put `-py` after `if`, end the line with `:`, and indent what's inside. The condition
is Python, and variables are used by their plain names, without `$`:

<!-- run -->
```
name="Ada Lovelace"
age=36
if -py int(age) >= 18 and name.startswith("Ada"):
    echo "an adult named Ada"
if -py "Love" in name:
    echo "the name contains Love"
if -py len(name) > 20:
    echo "a long name"
else:
    echo "a short name"
```

<!-- output -->
```
an adult named Ada
the name contains Love
a short name
```

Variables are always text, so wrap a number in `int()` before comparing it. Without it,
`age >= 18` stops with `'>=' not supported between instances of 'str' and 'int'`, and
`age == 36` is quietly false. [Variables are text](python-style.md#variables-are-text) explains
why.

You can use comparisons, `and`, `or`, `not`, `in`, text methods like `.lower()`, `.startswith()`
and `.endswith()`, and the functions `len`, `int`, `float`, `str`, `min`, `max`, `abs`,
`match(REGEX, TEXT)`, `exists(PATH)`, `isfile(PATH)` and `isdir(PATH)`. `status` is the last
command's exit status. Imports aren't allowed. `man if` lists everything.

Here are the file checks the Python way, `isdir` and `isfile` in place of `-d` and `-f`:

<!-- run -->
```
touch notes.txt
if -py isdir("notes.txt"):
    echo "a folder"
elif isfile("notes.txt"):
    echo "a file"
```

<!-- output -->
```
a file
```

A condition that isn't Python runs as a command, so tests like `grep -q` work too:

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

ShellCraft decides by trying to read the condition as Python first. `[ … ]` and `[[ … ]]` are
always commands. [Conditions: Python or a command](python-style.md#conditions-python-or-a-command)
has the details, including why `while -py true:` needs a capital T.

### case in Python style: match

`match` lists the choices as `case PATTERN:`, each with its commands indented under it, as in
Python. `|` separates alternatives, and `case _:` is "anything else":

<!-- run: ["2"] -->
```
echo "1) Start the dev server"
echo "2) Run the tests"
choice = input("Choose 1-2: ")
match choice:
    case "1":
        echo "starting the server"
    case "2" | "t":
        echo "running the tests"
    case _:
        echo "'$choice' isn't on the menu"
```

<!-- output -->
```
1) Start the dev server
2) Run the tests
Choose 1-2: 2
running the tests
```

`choice = input(…)` is the Python way to ask, the same as `read -p`. `match choice:` reads the
variable `choice` (`match $choice:` works too). A quoted pattern can use `*`, `?` and `[ ]`, and
only the first choice that matches runs. A short choice fits on one line: `case "q": echo bye`.
The same block can also be written `case -py $choice:` with the choices as bare patterns, `"1":`
and `_:`. In the brace form it's `match -py $choice { case "1" { … } case _ { … } }`.

A short block fits on one line after the `:`: `if -py n == "3": echo three`. The brace form,
`if -py (n == "3") { echo three }`, also still works. Use whichever style you find easier to read,
and mix them in the same script. [Bash style and Python style](python-style.md) shows every
example both ways.

## If it doesn't work

**`command not found: [1`.** There's no space after `[`. `[$a -eq 1]` glues `[` to the value, so
the shell looks for a command named `[1`. Write `[ $a -eq 1 ]`.

**`[: unexpected argument …`.** A variable was empty or had a space in it. Put it in double quotes:
`[ "$name" = "Ada L" ]`. See [Comparing text](#comparing-text).

**`[: abc: integer expression expected`.** You used `-eq`, `-lt` or another number test on
something that isn't a whole number. Check the value, or compare it as text with `=`.

**The test is always true.** `[ $a=2 ]` with no spaces around `=` is one word, and `[` with one
non-empty word in it is always true. Write `[ $a = 2 ]`.

**A file named `3` appeared.** `[ $a > 3 ]` didn't compare anything: `>` sent the output to a file
called `3`, and the test came out true. Use `-gt`.

**`'fi' without a matching if`.** The `;` before `then` is missing: `if [ $a -eq 1 ] then …`. On
one line, `then` needs a `;` or a new line before it.

**`missing 'fi'`.** The `if` was never closed. Add `fi` at the end, one for every `if`.
