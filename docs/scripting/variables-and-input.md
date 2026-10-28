# Variables and input

← [Scripting guide](README.md)

A variable holds a value so you can use it later. This page shows you how to set one, use it,
give it a default, do sums with it, and fill it with an answer the user types.

## Set a variable and use it

Write the name, an `=` and the value, with no spaces around the `=`. Put a `$` in front of the name
to use the value:

<!-- run -->
```
project=shellcraft
echo "I'm working on $project"
```

<!-- output -->
```
I'm working on shellcraft
```

A variable lasts for the whole session. One set in your rc file is still there at the prompt, and in
every startup file that runs after it.

Names are letters, digits and `_`, and they can't start with a digit. `my_host`, `count2` and
`API_URL` are fine.

Inside a Python-style block or a `def`, you can also write it the Python way, with spaces and a
Python value: `count = 0`. See [Python style: name = value](#python-style-name--value).

## Quoting

Use double quotes when a value has spaces, and around a variable when you use it:

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

## Default values

`${name:-something}` gives you the value of `name`, or `something` when `name` is empty or was
never set. It's the easy way to have a fallback:

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

To give a variable a value only when it doesn't have one yet, assign the default back to it. This
is handy at the top of a startup file:

<!-- run -->
```
ROLE=${ROLE:-dev}
echo "role is $ROLE"
```

<!-- output -->
```
role is dev
```

## Arithmetic

Put a sum inside `$(( ))`. You can use `+`, `-`, `*`, `/` (whole numbers), `%` (the remainder) and
`**` (a power). Inside the brackets, the `$` in front of a variable is optional:

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

## Python style: name = value

Inside a Python-style block (`for -py`, `if -py`, `while -py` and the rest) and in a `def`, the right
side of `name = value` is a Python value, the same kind a Python condition uses. Text goes in quotes,
and other variables go in without a `$`:

<!-- run -->
```
first=Ada
for -py n in 1..3:
    total = int(n) * 10
    name = first.upper() + "!"
    big = int(total) > 15
    echo "$n: $total $name $big"
```

<!-- output -->
```
1: 10 ADA! False
2: 20 ADA! True
3: 30 ADA! True
```

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

The value is stored as text, as every variable is, so `total` is text again on the next line and
`int(total)` turns it back into a number. A yes or no is stored as `True` or `False`, and a fraction
as `2.5`. `/` always gives a fraction (`4 / 2` is `2.0`), so use `//` for a whole number. `count += 1`
on a variable that isn't set yet stops with `count: not set, give it a value first`.

Outside these blocks, at the prompt or in a bash block, `x = 5` with spaces runs a command called
`x`, as in bash. The bash form `name=value` works everywhere, Python-style blocks included.

## Save a command's output

`$(command)` runs a command and puts what it printed in its place. Store it in a variable to use it
later:

<!-- run -->
```
today=$(date +%Y-%m-%d)
echo "Notes for $today" > notes.txt
cat notes.txt
```

You'll see `Notes for` and today's date, like `Notes for 2026-10-27`.

## Ask the user

There are two ways to ask a question and keep the answer. Both only work in the interactive shell:
at the prompt, in the rc file, in startup files and in funcs.

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

Wrap it in `int()` when you need a whole number, or `float()` for any number. ShellCraft keeps
asking until the answer is one:

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
variable's name:

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

`read` always asks the person at the keyboard. It doesn't read from a pipe or a file. To go through
the lines of a file, see [Go through a file line by line](loops.md#go-through-a-file-line-by-line).

### Keep asking until the answer is good

Put `read` in a loop that runs until the answer passes a test. This one won't accept an empty
answer:

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

### Ask a yes or no question

`case` checks the answer against a few patterns, so `y`, `Y`, `yes` and `Yes` all count:

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

There's more on `case` in [Conditions and choices](conditions.md#case-pick-one-of-several-choices), and a
numbered menu with `select` in [Loops and menus](loops.md#a-numbered-menu-with-select).

## What you can do with a value

ShellCraft fills in `$name`, `${name}` and `${name:-default}`. It doesn't have bash's other
`${…}` forms, like `${#name}` or `${name^^}`. Use a command or a Python condition instead:

| You want | Write |
| --- | --- |
| The value in capitals | `upper=$(echo $name \| tr a-z A-Z)` |
| One field of a value | `second=$(echo "a,b,c" \| cut -d , -f 2)` |
| The file name from a path | `file=$(basename $path)` |
| The folder from a path | `folder=$(dirname $path)` |
| Check how long it is | `if -py len(name) > 20:` |
| Whether it starts with something | `if -py name.startswith("dev"):` |
| Whether it contains something | `[[ $name == *test* ]]` or `if -py "test" in name:` |

<!-- run -->
```
path=/home/ada/projects/report.txt
name=$(basename $path)
upper=$(echo $name | tr a-z A-Z)
echo "$name in capitals is $upper"
echo "it lives in $(dirname $path)"
```

<!-- output -->
```
report.txt in capitals is REPORT.TXT
it lives in /home/ada/projects
```

There are no arrays. A list is a variable with its items separated by spaces, and a `for` loop goes
through them one by one. See [Loop over a list](loops.md#loop-over-a-list).

## Environment variables

A plain variable belongs to ShellCraft. Modules, subshells and programs you start from ShellCraft
don't see it. To hand a value to them, make it an environment variable with `env set` or `export`:

```
env set API_URL=https://api.example.test     # this session; `env` lists what you set
env set -s GITHUB_TOKEN                      # a secret: asked with hidden typing
export DEPLOY_ENV=$env                       # bash's way, from a variable you already have
```

Use them in a script the same way, with a `$`: `echo $API_URL`. Variables from your system, like
`$HOME` and `$PATH`, work too. [Pipes and scripting](../scripting.md#environment-variables) has
the details.

## Remove a variable

`unset name` forgets a variable. After that, `$name` is empty:

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
