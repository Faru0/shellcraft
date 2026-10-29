# Variables and input

← [Scripting guide](README.md)

This page is the place to start if you've never written a script. A variable is a name that holds
a value, like a project name, a count or a path, so you can use it later without typing it again.
After reading this page you'll be able to set variables and use them, quote text so it comes out
the way you meant, give a value a fallback, do sums, keep a command's output, and ask the user a
question and use the answer.

## Set a variable and use it

Say you type the same project name in five places in your rc file. Put it in a variable once, and
you only have to change it in one place. Write the name, an `=` and the value, with no spaces
around the `=`. Put a `$` in front of the name to use the value:

<!-- run -->
```
project=shellcraft
echo "I'm working on $project"
```

<!-- output -->
```
I'm working on shellcraft
```

Notice that `project` has no `$` when you set it, and does when you use it. The `$` means "fill in
the value here". When you set a variable you're naming it, not reading it, so there's nothing to
fill in. Without the `$`, the name is only a word: `echo project` prints `project`.

A variable lasts for the whole session. One set in your rc file is still there at the prompt, and in
every startup file that runs after it.

Names are letters, digits and `_`, and they can't start with a digit. `my_host`, `count2` and
`API_URL` are fine.

Inside a Python-style block or a `def`, you can also write it the Python way, with spaces and a
Python value: `count = 0`. See [Python style: name = value](#python-style-name--value).

### If it doesn't work

**Spaces around the `=`.** `name = Ada` doesn't set anything. Outside a Python-style block,
ShellCraft reads it the way bash does: run a command called `name` with the words `=` and `Ada`.
There's no such command, so you see:

```
command not found: name
```

Write `name=Ada`, with no spaces.

**A variable that was never set.** If you use `$name` before giving it a value, or spell it wrong,
it's empty, the same as in bash. Nothing warns you, so the line just has a gap where the value
should be:

<!-- run -->
```
echo "Hello, $user_name"
```

<!-- output -->
```
Hello, 
```

When a value is missing from your output, check the spelling and check that the line setting it
ran first. To get a fallback instead, see [Default values](#default-values). `set -u` turns this into
an error that stops the script, see [Errors and cleanup](errors.md).

## Quoting

Quotes tell ShellCraft where a piece of text starts and ends. You need them most when a value has
spaces. Use double quotes for such a value, and get in the habit of putting them around a variable
when you use it:

<!-- run -->
```
name="Ada Lovelace"
greeting="Hello, $name"
echo "$greeting"
```

<!-- output -->
```
Hello, Ada Lovelace
```

Without the quotes on the first line, ShellCraft stops and tells you what to do, and `name` isn't
set:

```
name=Ada Lovelace: quote a value that contains spaces: name="…"
```

In ShellCraft a variable stays one piece wherever you use it, quoted or not, with one exception:
the list after `in` in a `for` loop, which is split at spaces on purpose (see
[Loop over a list](loops.md#loop-over-a-list)). Quoting variables anyway costs nothing, and it keeps
your scripts working if you ever copy them to bash.

The two kinds of quotes do different things:

- **Double quotes** `"…"` keep the text together and still fill in `$variables`.
- **Single quotes** `'…'` keep everything exactly as typed. Nothing is filled in.

<!-- run -->
```
item=coffee
echo "I want $item"
echo 'I want $item'
```

<!-- output -->
```
I want coffee
I want $item
```

Single quotes are what you want when the text has a `$` that isn't a variable, like a price or a
pattern for `grep`. To put an apostrophe in your text, use double quotes around it:
`echo "it's done"`. Inside single quotes, `'it's'` ends the quote at the apostrophe, and ShellCraft
reports `unterminated ' quote`.

When the name runs straight into other text, wrap it in braces so ShellCraft knows where it ends:

<!-- run -->
```
file=report
echo "${file}_2026.txt"
```

<!-- output -->
```
report_2026.txt
```

Without the braces, `$file_2026` looks like one name, `file_2026`. There's no variable called that,
so you'd get the text `$file_2026.txt` back. A letter, digit or `_` right after the name needs
braces. A `.`, `-`, `/` or space doesn't, because it can't be part of a name.

## Default values

You'll often want "use this value if it's there, otherwise use that one". For example, a startup
file that works for anyone, greeting them by name if it knows it.
`${name:-something}` gives you the value of `name`, or `something` when `name` is empty or was
never set:

<!-- run -->
```
echo "Hello, ${user_name:-stranger}"
user_name=Sam
echo "Hello, ${user_name:-stranger}"
```

<!-- output -->
```
Hello, stranger
Hello, Sam
```

The first `echo` runs before `user_name` is set, so you get the fallback. The second runs after,
so you get `Sam`.

To give a variable a value only when it doesn't have one yet, assign the default back to it. This
is handy at the top of a startup file, when an earlier file or your profile may already have set
it:

<!-- run -->
```
ROLE=${ROLE:-dev}
echo "role is $ROLE"
```

<!-- output -->
```
role is dev
```

If `ROLE` was already `prod`, it stays `prod`.

`${ROLE:=dev}` does the same in one go: it gives you the value and, when `ROLE` was empty, sets it
to `dev` as well. [Defaults, lengths and trimming](#defaults-lengths-and-trimming) has the other
`${…}` forms, like the length of a value or a part of it.

## Arithmetic

Every variable holds text, even when the text is a number. To do a sum, put it inside `$(( ))`.
You can use `+`, `-`, `*`, `/` (whole numbers), `%` (the remainder) and `**` (a power). Inside the
brackets, the `$` in front of a variable is optional:

<!-- run -->
```
apples=7
people=2
echo "each gets $((apples / people)), with $((apples % people)) left over"
echo "2 to the power of 8 is $((2 ** 8))"
```

<!-- output -->
```
each gets 3, with 1 left over
2 to the power of 8 is 256
```

`/` drops the fraction, so 7 / 2 is 3, and `%` gives you the 1 that was left over. Dividing by zero
stops the line with `$(( )): division by zero`. Inside `$(( ))`, a variable that was never set
counts as 0.

To count, add one to the variable and store it back:

<!-- run -->
```
count=0
count=$((count + 1))
count=$((count + 1))
echo "count is $count"
```

<!-- output -->
```
count is 2
```

A common slip is `count=count+1`. With no `$(( ))`, there's no sum: `count` becomes the text
`count+1`.

## Python style: name = value

Inside a Python-style block (`for -py`, `if -py`, `while -py` and the rest) and in a `def`, the right
side of `name = value` is a Python value, the same kind a Python condition uses. Text goes in quotes,
and other variables go in without a `$`. This is handy when you want Python's tools, like `.upper()`
or a comparison, without leaving the shell:

<!-- run -->
```
first=Ada
for -py n in 1..3:
    total = int(n) * 10
    name = first.upper() + "!"
    big = total > 15
    echo "$n: $total $name $big"
```

<!-- output -->
```
1: 10 ADA! False
2: 20 ADA! True
3: 30 ADA! True
```

Go through it line by line. `n` is a loop word, and loop words are text, so `int(n)` turns it into
a number and `* 10` multiplies instead of repeating the text. `total` now holds a number, and it
stays a number for the Python lines after it. `first.upper()` is Python's way of putting text in
capitals, and `+ "!"` adds an exclamation mark. `total > 15` is a yes or no question, so `big` is
`True` or `False`. The `echo` line is a normal shell line, so there you use `$` to fill the values
in, and `$total` gives it as text.

`count += 1` adds to a variable, and `-=`, `*=`, `/=`, `//=` and `%=` work too. With a number on the
right, the variable is read as a number. With text on the right, `+=` adds the text to the end:

<!-- run -->
```
count=0
for -py word in red green blue:
    count += 1
    if word == "red":
        names = word
    else:
        names += ", " + word
echo "$count colours: $names"
```

<!-- output -->
```
3 colours: red, green, blue
```

A value set in Python keeps its type: a whole number, a fraction like `2.5`, or `True` and
`False`. Bash only ever sees text: `$total` is `10`, `$big` is `True`. A value set the bash way
(`count=0`, `read`, a loop word, an argument) is text, so Python code needs `int()` to use it as a
number; `count += 1` does that for you. Anything else Python makes, like a list, is stored as its
text. `/` always gives a fraction (`4 / 2` is `2.0`), so use `//` for a whole number. `count += 1`
on a variable that isn't set yet stops with `count: not set, give it a value first`. Set it to 0
before the loop, as the example does.

To build text out of variables, use an f-string: `msg = f"{first} has {count} files"` fills in
the values between the braces, and `print(msg)` prints it. See
[Build text with f-strings and print](python-style.md#build-text-with-f-strings-and-print).

Outside these blocks, at the prompt or in a bash block, `x = 5` with spaces runs a command called
`x`, as in bash. The bash form `name=value` works everywhere, Python-style blocks included.

## Save a command's output

Many values come from a command: today's date, the current folder, the number of lines in a file.
`$(command)` runs a command and puts what it printed in its place. Store it in a variable to use it
later:

<!-- run -->
```
today=$(date +%Y-%m-%d)
echo "Notes for $today" > notes.txt
cat notes.txt
```

You'll see `Notes for` and today's date, like `Notes for 2026-10-27`.

The command runs once, when the line that sets `today` runs. After that, `$today` is plain text,
so it won't change if the script runs past midnight. That's usually what you want: every line of
the script uses the same date.

## Ask the user

Sometimes a script needs something only the person at the keyboard knows: which environment to
deploy to, a project name, a password. There are two ways to ask a question and keep the answer.
Both only work in the interactive shell: at the prompt, in the rc file, in startup files and in
funcs.

**`input()`** reads like Python. It works on a line of its own, and inside loops, `if` blocks and
functions:

<!-- run: ["Ada"] -->
```
name = input("What is your name? ")
echo "Nice to meet you, $name"
```

<!-- output -->
```
What is your name? Ada
Nice to meet you, Ada
```

The text in the brackets is the question. Leave a space at the end of it, so the answer doesn't
butt up against the question mark.

The question can hold variables. Write `$name` in it, as in `input("Age of $who? ")`, or make it
an f-string and put any Python expression between braces:

<!-- run: ["7", "9"] -->
```
for -py who in ada sam:
    age = input(f"How old is {who.title()}? ")
    echo "$who: $age"
```

<!-- output -->
```
How old is Ada? 7
ada: 7
How old is Sam? 9
sam: 9
```

Wrap it in `int()` when you need a whole number, or `float()` for any number. ShellCraft keeps
asking until the answer is one, so you don't have to check it yourself, and the variable holds the
number, so Python code can use it as one (`age + 1`):

<!-- run: ["thirty", "30"] -->
```
age = int(input("How old are you? "))
echo "Next year you'll be $((age + 1))"
```

<!-- output -->
```
How old are you? thirty
'thirty' is not a whole number; try again
How old are you? 30
Next year you'll be 31
```

**`read`** works the way it does in bash. `-p` sets the question, and the last word is the
variable's name. Note there's no `$` on that name, because you're setting it:

<!-- run: ["staging"] -->
```
read -p "Which environment? " env
echo "Deploying to $env"
```

<!-- output -->
```
Which environment? staging
Deploying to staging
```

`read` takes one name. `read -p "Name? " first last` stops with `read: unrecognized arguments: last`.
To split an answer into parts, read it into one variable and take it apart with `cut` or `${…}`, as
in [Defaults, lengths and trimming](#defaults-lengths-and-trimming).

Add `-s` for a password or a token. What you type isn't shown, and it never goes into your history:

<!-- run: ["hunter2"] -->
```
read -s -p "API token: " token
if [ -n "$token" ]; then echo "Token saved for this session"; fi
```

<!-- output -->
```
API token: 
Token saved for this session
```

`[ -n "$token" ]` checks that the answer isn't empty, so pressing Enter on its own doesn't count.

`read` always asks the person at the keyboard. It doesn't read from a pipe or a file. To go through
the lines of a file, see [Go through a file line by line](loops.md#go-through-a-file-line-by-line).

### Keep asking until the answer is good

A blank project name would make a mess later, so it's better to refuse it now. Put `read` in a loop
that runs until the answer passes a test. This one won't accept an empty answer:

<!-- run: ["", "", "website"] -->
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
Project name (required): 
Project name (required): website
Project: website
```

`[ -z "$project" ]` means "project is empty". The first line sets `project` to empty, so the loop
asks at least once. Each answer replaces `project`, and the loop checks again. Once it's not empty,
the loop ends. `project=""` at the top isn't strictly needed, since a variable that was never set
is empty too, but it makes the starting point clear and clears any `project` left over from
earlier.

The same in Python style, with `input()`:

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

`input()` also works inside Python code: in a condition, in a value, in `print()`. This loop keeps
asking for numbers and adds them up, until you answer no. `int(input(…))` in the middle asks again
on its own if the answer isn't a whole number:

<!-- run: ["y", "5", "y", "lots", "7", "n"] -->
```
total=0
while -py input("Add a number? (y/n) ") == "y":
    total += int(input("Number: "))
echo "Total: $total"
```

<!-- output -->
```
Add a number? (y/n) y
Number: 5
Add a number? (y/n) y
Number: lots
'lots' is not a whole number; try again
Number: 7
Add a number? (y/n) n
Total: 12
```

### Ask a yes or no question

Before a script deletes or overwrites something, it's polite to ask. People answer in different
ways, so accept them all. `case` checks the answer against a few patterns, so `y`, `Y`, `yes` and
`Yes` all count:

<!-- run: ["Yes"] -->
```
read -p "Clear the build folder? (y/n) " answer
case $answer in
  [yY]*) echo "clearing it" ;;
  *) echo "leaving it alone" ;;
esac
```

<!-- output -->
```
Clear the build folder? (y/n) Yes
clearing it
```

`[yY]*` means "starts with y or Y, then anything". Any other answer, including pressing Enter on its own,
falls through to `*)`, so the safe choice is the default.

In Python style, check the answer with a Python condition:

<!-- run: ["no"] -->
```
answer = input("Clear the build folder? (y/n) ")
if -py answer.lower() in ("y", "yes"):
    echo "clearing it"
else:
    echo "leaving it alone"
```

<!-- output -->
```
Clear the build folder? (y/n) no
leaving it alone
```

`answer.lower()` turns the answer into small letters, so `YES` and `Yes` match `"yes"`.

There's more on `case` in [Conditions and choices](conditions.md#case-pick-one-of-several-choices), and a
numbered menu with `select` in [Loops and menus](loops.md#a-numbered-menu-with-select). A
`confirm` function you can put in front of any risky command is in
[Complete examples](examples.md#ask-before-doing-something-risky).

## Defaults, lengths and trimming

Bash has a set of `${…}` forms that change a value as you use it, and ShellCraft has the common
ones. They work in quotes and out, the variable itself doesn't change (except with `:=`), and
inside single quotes they stay plain text. This table is the list to come back to:

| You want | Write | With `f=report.tar.gz` |
| --- | --- | --- |
| A fallback when it's empty or not set | `${f:-default}` | `report.tar.gz` |
| A fallback, and set the variable to it | `${f:=default}` | `report.tar.gz` |
| Something else, only when it's set | `${f:+yes}` | `yes` |
| Stop with a message when it's empty | `${f:?give a file}` | `report.tar.gz` |
| How long it is | `${#f}` | `13` |
| Cut the shortest match off the end | `${f%.*}` | `report.tar` |
| Cut the longest match off the end | `${f%%.*}` | `report` |
| Cut the shortest match off the start | `${f#*.}` | `tar.gz` |
| Cut the longest match off the start | `${f##*.}` | `gz` |
| Replace the first match | `${f/tar/zip}` | `report.zip.gz` |
| Replace every match | `${f//./_}` | `report_tar_gz` |
| Replace at the start or the end | `${f/#report/data}`, `${f/%gz/bz2}` | `data.tar.gz`, `report.tar.bz2` |
| Part of it, from position 0, 6 long | `${f:0:6}` | `report` |
| The last 2 characters | `${f: -2}` | `gz` |
| In capitals, or small letters | `${f^^}`, `${f,,}` | `REPORT.TAR.GZ`, `report.tar.gz` |
| The first letter in capitals | `${f^}` | `Report.tar.gz` |

Leave out the `:` in the first four (`${f-default}`, `${f=default}`, `${f+yes}`, `${f?msg}`) and
an empty value counts as set, so only a variable that was never set gets the fallback.

The patterns after `#`, `%` and `/` are the same globs you use for file names: `*` is any text,
`?` one character and `[abc]` one of those. `%` and `#` take the shortest match, `%%` and `##` the
longest. To match a `*` itself, quote it: `${f%"*"}`.

Here are a few of them pulling apart a path:

<!-- run -->
```
path=/home/ada/projects/report.txt
file=${path##*/}
echo "file: $file"
echo "folder: ${path%/*}"
echo "name: ${file%.*}, type: ${file##*.}"
echo "in capitals: ${file^^}"
echo "as a backup: ${file/.txt/.bak}"
```

<!-- output -->
```
file: report.txt
folder: /home/ada/projects
name: report, type: txt
in capitals: REPORT.TXT
as a backup: report.bak
```

`${path##*/}` cuts everything up to the last `/`, which leaves the file name, like `basename`
does. `${path%/*}` cuts from the last `/` to the end, which leaves the folder, like `dirname`.

A default is handy for a function's arguments. `${1:-world}` is the first argument, or `world`
when there isn't one:

<!-- run -->
```
greet() { name=${1:-world}; echo "Hello, $name (${#name} letters)"; }
greet
greet Ada
```

<!-- output -->
```
Hello, world (5 letters)
Hello, Ada (3 letters)
```

The word after `:-`, `:=` or `:+` can use other variables and `$( )`, and it can hold another
`${…}`: `${out:-${name%.*}.csv}`.

`:?` is for a value a script can't do without. When the value is empty, it shows the message as
an error and the script stops right there, with status 1:

```
deploy() { target=${1:?give a target}; echo "deploying to $target"; }
deploy staging        # deploying to staging
deploy                # stops with: 1: give a target
```

At the prompt, only that line stops. Leave out the message (`${1:?}`) and you get
`1: parameter null or not set`, as in bash.

Under `set -u`, the forms with a fallback never count as using an unset variable, so
`${name:-}` is a safe way to read one that may not be there.

Anything else in `${…}`, like bash's `${!name}` or `${name@Q}`, stops with
`${name@Q}: bad substitution` rather than doing something you didn't mean.

There are no arrays. A list is a variable with its items separated by spaces, and a `for` loop goes
through them one by one. See [Loop over a list](loops.md#loop-over-a-list).

## Environment variables

A plain variable belongs to ShellCraft. Modules, subshells and programs you start from ShellCraft
don't see it. That's usually fine. But when a module needs an API address or a token, it looks in
the environment, so you have to put the value there. Make it an environment variable with `env set`
or `export`:

```
env set API_URL=https://api.example.test     # this session; `env` lists what you set
env set -s GITHUB_TOKEN                      # a secret: asked with hidden typing
export DEPLOY_ENV=$env                       # bash's way, from a variable you already have
```

Use them in a script the same way, with a `$`: `echo $API_URL`. Variables from your system, like
`$HOME` and `$PATH`, work too. [Pipes and scripting](../scripting.md#environment-variables) has
the details.

## Remove a variable

`unset name` forgets a variable. You'd do this to tidy up a temporary value, or so that a later
`${name:-default}` uses its default again. After that, `$name` is empty, as if it had never been
set, and `${name:-…}` gives you the fallback:

<!-- run -->
```
temp=42
unset temp
echo "temp is now '${temp:-gone}'"
```

<!-- output -->
```
temp is now 'gone'
```

To keep the variable but make it empty, set it to nothing instead: `temp=""`.

## Where to go next

Now that you can hold values, [Printing text](printing.md) shows you how to show them neatly, and
[Conditions and choices](conditions.md) shows you how to make decisions with them.
