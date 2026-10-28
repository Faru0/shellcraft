# Printing text

← [Scripting guide](README.md)

Almost every script prints something: a greeting from your rc file, a line saying a backup worked,
a table of servers, a warning in red. This page shows you how. After reading it you'll be able to
print one line or a whole block, line up columns and format numbers with `printf`, add color, send
error messages where they belong, and save what you print to a file.

Printing is for people. It puts text on the screen. If you want a value that the rest of your script
can use, store it in a variable instead (see [Variables and input](variables-and-input.md)), or have
a function hand it back (see [Functions](functions.md)).

## One line with echo

`echo` is the command you'll use most. It prints its words with a space between them, then ends
the line:

<!-- run -->
```
echo Hello from my rc file
name=Ada
echo "Welcome back, $name"
```

<!-- output -->
```
Hello from my rc file
Welcome back, Ada
```

The first `echo` has no quotes, and that's fine for plain words. The second has a variable in it,
and the quotes keep the whole greeting together.

Put the text in double quotes when it has a `$variable`, more than one space in a row, or a
character like `|`, `;`, `&`, `<` or `>`. Without quotes, those mean something to the shell.

### If it doesn't look right

**Your spaces disappeared.** `echo a     b` prints `a b`. Without quotes, `echo` gets two separate
words and puts one space between them. Quote the text, `echo "a     b"`, and every space stays.

**Part of the line went missing, and a file appeared.** `echo Done > ok` writes `Done` into a file
called `ok`, because `>` means "send the output to a file". If you meant to print the `>`, quote
it: `echo "Done > ok"`. The same goes for `|`, which would send the text to another command, and
`;`, which would end the command there.

**A value is missing, or a `$` disappeared.** A variable that was never set, or whose name is
spelled differently, is empty, so it leaves a gap. The same happens to a `$` you meant as text:
`echo "costs $5"` prints `costs `. To print a real `$`, like a price, use single quotes:
`echo 'costs $5'`. `set -u` turns a missing variable into an error, see
[Errors and cleanup](errors.md).

## Several lines at once

You'll want this for a welcome message, a short help text or a list of reminders. ShellCraft has
no heredoc (`cat <<EOF`), and a quoted string can't run across several lines of a script. There are
four other ways to print a block of text. Pick the one that reads best to you.

**One `echo` per line.** The plainest way, and the easiest to edit later:

<!-- run -->
```
echo "Good morning!"
echo "  1. Check the alerts"
echo "  2. Pull the latest code"
echo
echo "Have a good day."
```

<!-- output -->
```
Good morning!
  1. Check the alerts
  2. Pull the latest code

Have a good day.
```

An `echo` with nothing after it prints an empty line. The quotes keep the two spaces at the start
of the numbered lines, which indent them.

**`echo -e` with `\n`.** The `-e` makes `\n` start a new line and `\t` insert a tab. It's handy when
the text is short and you'd rather keep it on one line of your script:

<!-- run -->
```
echo -e "Name:\tAda\nRole:\tadmin\nTeam:\tplatform"
```

<!-- output -->
```
Name:	Ada
Role:	admin
Team:	platform
```

Without `-e`, `\n` is printed as the two characters `\` and `n`. If you see a `\n` in your output,
that's what's missing.

**`printf` with one line per argument.** `printf` repeats its pattern for every argument, so
`"%s\n"` prints each one on its own line:

<!-- run -->
```
printf "%s\n" "First line" "Second line" "Third line"
```

<!-- output -->
```
First line
Second line
Third line
```

**Keep the text in a file and `cat` it.** Best for a long message, a banner or a help screen.
Write it once in any editor, and print it from your rc file:

```
cat ~/.shellcraft/welcome.txt
```

The text comes out exactly as it is in the file, spaces and blank lines included.

## Columns and numbers with printf

`echo` prints text as it is. When you need things lined up in columns, a number with two decimals,
or a ticket number padded with zeros, use `printf`.

`printf` takes a pattern and fills in its `%` places with the words after it, in order. Unlike
`echo`, it doesn't put a new line between items for you, so finish the pattern with `\n`. Each
`%` place says what kind of value goes there and how wide to make it. Here they all are, to come
back to when you need one:

| Place | Prints | Example | Result |
| --- | --- | --- | --- |
| `%s` | Text | `printf "%s\n" hi` | `hi` |
| `%d` | A whole number | `printf "%d\n" 42` | `42` |
| `%5d` | A number, right-aligned in 5 spaces | `printf "%5d\n" 42` | `   42` |
| `%03d` | A number padded with zeros | `printf "%03d\n" 7` | `007` |
| `%.2f` | A number with 2 decimals | `printf "%.2f\n" 3.14159` | `3.14` |
| `%-10s` | Text, left-aligned in 10 spaces | `printf "%-10s|\n" hi` | `hi        \|` |
| `%10s` | Text, right-aligned in 10 spaces | `printf "%10s|\n" hi` | `        hi\|` |
| `%%` | A `%` sign | `printf "%d%%\n" 50` | `50%` |

Widths line things up into a table. Say you want a quick status list of your services:

<!-- run -->
```
printf "%-10s %6s\n" SERVICE STATUS
printf "%-10s %6s\n" web up
printf "%-10s %6s\n" database up
printf "%-10s %6s\n" cache down
```

<!-- output -->
```
SERVICE    STATUS
web            up
database       up
cache        down
```

Every line uses the same pattern. `%-10s` puts the name on the left of a 10-character space, and
the `-` is what makes it left-aligned. `%6s` puts the status on the right of a 6-character space.
Because every row has the same widths, the columns line up, whatever the length of each word.

And numbers come out the way you want them:

<!-- run -->
```
used=42.567
printf "Disk: %.1f%% used\n" $used
printf "Ticket #%05d\n" 381
```

<!-- output -->
```
Disk: 42.6% used
Ticket #00381
```

`%.1f` rounds to one decimal, so 42.567 becomes 42.6. The `%%` right after it prints a plain `%`.
You need two, because a single `%` would start another place. `%05d` pads 381 with zeros to five
digits.

If there are more places than words, the extra places are left empty: `printf "%s and %s\n" one`
prints `one and `. If a `%d` gets something that isn't a number, `printf` stops with a message like
`printf: 'abc': expected a number for %d`.

Each command's output ends on its own line in ShellCraft. So two `printf` commands without `\n`
still print on two lines. To build one line out of pieces, collect them in a variable and print it
once, as in [Count with a range](loops.md#count-with-a-range).

## A table from a loop

Typing a `printf` line per row works for three rows. For a list of any length, put `printf` inside
a loop to print one row per item:

<!-- run -->
```
printf "%-8s %s\n" HOST ROLE
for host in web1 web2 db1; do
  case $host in
    web*) role=frontend ;;
    db*) role=database ;;
  esac
  printf "%-8s %s\n" $host $role
done
```

<!-- output -->
```
HOST     ROLE
web1     frontend
web2     frontend
db1      database
```

The header is printed once, before the loop. Inside the loop, `case` works out the role from the
start of the host name: `web*` matches anything starting with `web`. Then one `printf` prints the
row with the same widths as the header, so they line up.

## Python style: print and f-strings

Inside a Python-style block or a `def`, you can print the way Python does, with `print()` and
f-strings. An f-string fills in the values between `{ }`, and a spec after a `:` lays them out,
so it does the job of `printf` without the `%` codes. Here is the same table as above:

<!-- run -->
```
print_table() {
  for -py host in web1 web2 db1:
      role = "database" if host.startswith("db") else "frontend"
      print(f"{host:<8} {role}")
}
echo "HOST     ROLE"
print_table
```

<!-- output -->
```
HOST     ROLE
web1     frontend
web2     frontend
db1      database
```

`{host:<8}` pads the host name to 8 characters, on the right, so the roles line up. `{n:>5}` pads
on the left, `{price:.2f}` gives two decimal places, and `{n:,}` puts commas in big numbers. A
variable holds text, but a spec for numbers like `.2f` reads it as a number for you:

<!-- run -->
```
for -py line in "tea 2.5" "cake 3.75":
    item = line.split()[0]
    price = line.split()[1]
    print(f"{item:<6}{price:>7.2f}")
```

<!-- output -->
```
tea      2.50
cake     3.75
```

`print()` puts a space between values (`print("a", n)`); `sep=` and `end=` change what goes between
them and at the end. Like `echo`, its output can go into a pipe or a file after the block, or into
`$( )`. `print` only works inside a Python-style block or a `def`: at the prompt and in bash lines,
use `echo` and `printf`. See [Build text with f-strings and print](python-style.md#build-text-with-f-strings-and-print)
for every spec and for `.format()`.

## Colors

A green OK or a red FAILED is easier to spot than plain text. `\e[…m` codes color the text. Use them
with `echo -e` or `printf`, and end with `\e[0m` to go back to the normal color. If you forget the
`\e[0m`, everything after it stays colored:

<!-- run -->
```
echo -e "\e[32mOK\e[0m all services are up"
echo -e "\e[1;31mFAILED\e[0m the backup didn't run"
printf "\e[33m%s\e[0m\n" "warning: disk almost full"
```

| Code | Color | Code | Color |
| --- | --- | --- | --- |
| `\e[31m` | Red | `\e[35m` | Magenta |
| `\e[32m` | Green | `\e[36m` | Cyan |
| `\e[33m` | Yellow | `\e[1m` | Bold |
| `\e[34m` | Blue | `\e[0m` | Back to normal |

You can combine them with a `;`: `\e[1;32m` is bold green. `\033` works in place of `\e`, so codes
copied from a bash script work as they are. Plain `echo` without `-e` prints the codes as text.

## Error messages

When something goes wrong, say so on the error stream rather than with the normal output. That way,
if someone sends your script's output to a file or a pipe, the errors still show on the screen.
Add `>&2` to send a line to the error stream. ShellCraft shows it in the error color, and it doesn't
change `$?`:

<!-- run -->
```
config=missing.toml
if [ ! -f $config ]; then
  echo "error: $config not found" >&2
fi
```

<!-- output -->
```
error: missing.toml not found
```

`[ ! -f $config ]` means "the config file is not there". `$?` is the exit status of the last
command: 0 means it worked, anything else means it failed. Printing an error message doesn't count
as failing, so the `echo` still ends with 0.

To stop a function with an error, print the message and `return 1`. See
[Functions](functions.md#check-the-arguments). For stopping a whole script on the first failure,
and cleaning up when it does, see [Errors and cleanup](errors.md).

## A notice in the footer

`alert` puts a message in the footer at the bottom of the screen, where it stays until you dismiss
it. It's a good way for a startup file to tell you something without it scrolling away, like a
backup that failed overnight or a VPN that isn't up:

```
alert -l info "shell ready"
alert -l warn "the VPN isn't connected"
alert -l crit "backup failed last night"
```

The level, `info`, `warn` or `crit`, sets the color. [Subshells, alerts and the clip](../subshells.md)
has the details.

## Save text to a file

A log of what your script did, a list of reminders, a report to send later: all of these are text
in a file. `>` writes the output to a file, replacing what was there. `>>` adds to the end:

<!-- run -->
```
echo "started at $(date +%H:%M)" > today.log
echo "checked the alerts" >> today.log
echo "pulled the code" >> today.log
wc -l today.log
```

<!-- output -->
```
3 today.log
```

The first line uses `>`, so it starts a fresh `today.log` each time the script runs. The next two use
`>>`, so they add lines below it. `wc -l` counts the lines. Mixing these up is a classic mistake:
`>` where you meant `>>` wipes the file and leaves only the last line.

`tee` prints the output and saves it at the same time, so you see it and keep it. Add `-a` to add
to the file instead of replacing it:

<!-- run -->
```
echo "deploy finished" | tee -a today.log
```

<!-- output -->
```
deploy finished
```

A whole loop can go to a file too. Put the `>` after `done`:

<!-- run -->
```
for n in 1 2 3; do echo "line $n"; done > numbers.txt
cat numbers.txt
```

<!-- output -->
```
line 1
line 2
line 3
```

## Clear the screen

`clear` empties the screen. Put it at the top of a script that draws a menu, so the menu starts
on a fresh screen. The shell already clears the screen when it starts. `settings clear_on_start off`
turns that off.

## Where to go next

[Conditions and choices](conditions.md) shows you how to print one thing or another depending on
what your script finds, and [Loops and menus](loops.md) how to print a line for every item.
