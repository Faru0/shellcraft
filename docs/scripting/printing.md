# Printing text

← [Scripting guide](README.md)

This page shows you how to print text from a script: one line, several lines, neat columns and
numbers, colors, error messages, and text saved to a file.

## One line with echo

`echo` prints its words with a space between them, then ends the line:

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

Put the text in double quotes when it has a `$variable`, more than one space in a row, or a
character like `|`, `;`, `&`, `<` or `>`. Without quotes, those mean something to the shell.

## Several lines at once

ShellCraft has no heredoc (`cat <<EOF`), and a quoted string can't run across several lines of a
script. There are four other ways to print a block of text. Pick the one that reads best to you.

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

An `echo` with nothing after it prints an empty line.

**`echo -e` with `\n`.** The `-e` makes `\n` start a new line and `\t` insert a tab:

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

Without `-e`, `\n` is printed as the two characters `\` and `n`.

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

`printf` takes a pattern and fills in its `%` places with the words after it, in order. Unlike
`echo`, it doesn't end the line for you, so finish the pattern with `\n`.

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

Widths line things up into a table:

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

## A table from a loop

Put `printf` inside a loop to print one row per item. Each row is one output line:

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

## Colors

`\e[…m` codes color the text. Use them with `echo -e` or `printf`, and end with `\e[0m` to go back
to the normal color:

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
copied from a bash script work as they are.

## Error messages

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

To stop a function with an error, print the message and `return 1`. See
[Functions](functions.md#check-the-arguments).

## A notice in the footer

`alert` puts a message in the footer at the bottom of the screen, where it stays until you dismiss
it. It's a good way for a startup file to tell you something without it scrolling away:

```
alert -l info "shell ready"
alert -l warn "the VPN isn't connected"
alert -l crit "backup failed last night"
```

The level, `info`, `warn` or `crit`, sets the color. [Subshells, alerts and the clip](../subshells.md)
has the details.

## Save text to a file

`>` writes the output to a file, replacing what was there. `>>` adds to the end:

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

`tee` prints the output and saves it at the same time. Add `-a` to add to the file instead of
replacing it:

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
on a fresh screen. The shell already clears the screen when it starts; `settings clear_on_start off`
turns that off.
