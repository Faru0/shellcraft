# Input, here-docs and pipes

← [Scripting guide](README.md)

So far the only way to hand a script some text was to ask the person at the keyboard. This page is
about the other sources: a file, a string you write in the script, a block of text, or the output of
another command. After it you'll be able to read a file line by line with `read`, split a line into
named pieces, write a block of text right in your script, feed a loop from a pipe, and join a long
command over several lines.

## Read a line from a file

`read` takes one line and keeps it in a variable. With nothing attached it asks you, as
[Variables and input](variables-and-input.md#ask-the-user) shows. Give it input and it takes the
next line of that instead. The usual way to go through a file is a `while read` loop with `< file`
after `done`:

<!-- run -->
```
printf "web1 frontend\nweb2 frontend\ndb1 database\n" > hosts.txt
while read host role; do
  echo "$host is a $role server"
done < hosts.txt
```

<!-- output -->
```
web1 is a frontend server
web2 is a frontend server
db1 is a database server
```

Each time round the loop, `read` takes the next line, splits it at the spaces, and puts the first
word in `host` and the rest in `role`. When the lines run out, `read` stops with status 1, and a
`while` loop stops when its command fails. That is how the loop ends by itself.

When you name fewer variables than a line has words, the last one gets everything that is left.
With one name you get the whole line, minus the spaces at both ends:

<!-- run -->
```
read first rest <<< "one two three four"
echo "first=$first rest=$rest"
```

<!-- output -->
```
first=one rest=two three four
```

With no name at all the line goes into `REPLY`, exactly as it was, spaces and all.

### Splitting on something other than spaces

`read` splits at the characters in the variable `IFS`, which is a space, a tab and a new line until
you change it. Set it to `:` for a colon-separated line:

<!-- run -->
```
IFS=:
read user shell <<< "ada:/bin/zsh"
echo "$user uses $shell"
IFS=" "
```

<!-- output -->
```
ada uses /bin/zsh
```

Put the old value back afterwards (the last line), or every later `read` keeps splitting on colons.
Or put the setting in front of the `read`, which changes it for that command only:
`IFS=: read user shell <<< "ada:/bin/zsh"`. A single space (or any run of spaces) between words counts as one separator. Any other separator
character counts once each, so `a::b` has an empty middle field.

### Options

| Option | What it does |
| --- | --- |
| `-r` | Keep backslashes as they are. Without it, `\x` becomes `x` and a backslash at the end of a line joins the next one. Use `-r` when you read file contents. |
| `-n N` | Stop after N characters. |
| `-d DELIM` | End the line at DELIM instead of at a new line. |
| `-p PROMPT`, `-s` | Only matter when `read` asks you. |
| `-t SECONDS` | Accepted when there is input. It doesn't work when `read` asks you. |

`read -a` (arrays) isn't supported, and neither is `read -u` (file descriptors). `man read` has the
whole list.

### A last line with no new line

If a file doesn't end with a new line, `read` still stores the last line, but returns status 1, as
bash does. So `while read line` skips it. To keep it, add a test: `while read line || [ -n
"$line" ]; do`.

## Pipe into a loop

Anything a command prints can be the input of a loop, if you put the pipe before it:

<!-- run -->
```
printf "x 1\ny 2\n" | while read name n; do
  echo "$name has $n"
done
```

<!-- output -->
```
x has 1
y has 2
```

The same works for `for`, `until`, `if` and `case`. The loop's `read` commands take the lines in
turn. One thing to know: a loop that is piped into runs on a copy of your variables, as it does in
bash. Whatever it sets is gone when it ends:

<!-- run -->
```
total=none
printf "a\nb\n" | while read line; do total=$line; done
echo "after the pipe: $total"
while read line; do total=$line; done <<< "c"
echo "after a redirect: $total"
```

<!-- output -->
```
after the pipe: none
after a redirect: c
```

With `< file` or `<<<` on the loop itself there is no copy, so the variables last. If you need a
value out of a piped loop, print it inside and capture the loop with `$( )`, or use `< file`.

A small difference from bash: `echo hi | read x` sets `x` here. In bash the pipe hides it, so
`x` stays empty after it.

## Here-strings

`<<<` gives a command one line of text, with a new line added. It's the short way to feed `read`
or a filter from a variable or a literal:

<!-- run -->
```
who=Ada
read first last <<< "$who Lovelace"
echo "$first"
tr a-z A-Z <<< "shout"
```

<!-- output -->
```
Ada
SHOUT
```

The word after `<<<` is expanded like any other word, so `$who` and `$( )` work, and quotes keep
spaces together.

## Here-docs

A here-doc is a block of text written inside your script, for a welcome message or a config file
you want to write out. After `<<` put a word of your choice, then the lines, then that word alone on
a line to end it:

<!-- run -->
```
project=shellcraft
cat <<EOF
Project: $project
Today: $(echo Tuesday)
Sum: $((1 + 2))
A price: \$5
EOF
```

<!-- output -->
```
Project: shellcraft
Today: Tuesday
Sum: 3
A price: $5
```

The text is expanded first: `$project`, `${project}`, `$( )` and `$(( ))` are replaced, and `\$` is a
plain dollar sign. Quote marks in the text are just text. Everything else is kept exactly, including
blank lines, spaces and lines that start with `#`.

To stop all expansion, quote the word after `<<`: `<<'EOF'` or `<<"EOF"`:

<!-- run -->
```
project=shellcraft
cat <<'EOF'
Use $project in your script.
Backslashes \ and $(commands) stay as typed.
EOF
```

<!-- output -->
```
Use $project in your script.
Backslashes \ and $(commands) stay as typed.
```

With `<<-` instead of `<<`, tabs at the start of each line are removed, and so are tabs before the
ending word. That lets you indent a here-doc inside a block and still end it. (Spaces are not
removed, only tabs.)

A here-doc goes to the command on its line, so `cat <<EOF > notes.txt` writes the text to a file and
`cat <<EOF | sort` sorts it. It also works on a loop, which is a neat way to loop over a few lines
without a file:

<!-- run -->
```
while read key value; do
  echo "$key -> $value"
done <<EOF
host example.com
port 8080
EOF
```

<!-- output -->
```
host -> example.com
port -> 8080
```

Here-docs work at the prompt too. After you press Enter on `cat <<EOF`, the shell shows a
continuation prompt and keeps reading lines until you type the ending word. They also work in `.sc`
files, in funcs and in files you `source`, and `source -n` counts the lines inside them correctly.
If the ending word never comes, the text runs to the end of the file (bash does the same, with a
warning) and `source -n` warns about it.

## Group commands

Sometimes one pipe or one redirect should cover several commands. Put them in braces, or in
parentheses, and the group acts as a single command:

<!-- run -->
```
{ echo "banana"; echo "apple"; echo "cherry"; } | sort
{ echo "first"; echo "second"; } > list.txt
cat list.txt
```

<!-- output -->
```
apple
banana
cherry
first
second
```

`{ cmd1; cmd2; }` runs in the shell as it is: variables it sets are still set afterwards. Write the
space after `{` and the `;` (or a new line) before `}`. `( cmd1; cmd2 )` is a **subshell**: it runs
on a copy, so its variables, its `cd`, its `set` options and the functions it defines are gone when it
ends, and an `exit` inside only ends the group:

<!-- run -->
```
x=1
{ x=2; }
echo "after braces: $x"
( x=3; cd /tmp; echo "inside: $x in $(pwd)" )
echo "after parentheses: $x"
( exit 4 )
echo "the subshell's status was $?"
```

<!-- output -->
```
after braces: 2
inside: 3 in /tmp
after parentheses: 2
the subshell's status was 4
```

A group can be piped into (`echo hi | { read a; echo "got $a"; }`), followed by `2>&1`, and joined
with `&&` and `||` like any command (`{ make; make test; } && echo done`). `break` and `continue`
inside `{ }` act on the loop around it. A Python-style block, `for -py x in a b { … }`, keeps its own
braces and is not a group.

## Join a long command over several lines

A backslash as the last character of a line joins it with the next one. That's how you keep a long
command readable:

<!-- run -->
```
echo one \
  two \
  three
for n in 1 2; do \
  echo "round $n"; \
done
```

<!-- output -->
```
one two three
round 1
round 2
```

This works in `.sc` files, funcs and `source`, as well as at the prompt. Inside double quotes the
backslash and the new line both disappear; inside single quotes nothing is joined. A backslash at
the end of a `#` comment doesn't continue it. Two backslashes in a row are a literal backslash, so
only an odd number at the end of the line joins.

Error messages still name the first line of a joined command, and later lines keep their own
numbers.

## If it doesn't work

**`read` asks me instead of reading the pipe.** A command only reads input that is attached to it.
Put the pipe, `<` or `<<<` on the loop or the `read`, not on a later line.

**The variable is empty after my loop.** The loop was piped into, so it ran on a copy. Use
`done < file` or `done <<EOF`, or print inside the loop.

**A variable in front of a command, like `IFS= read -r line`, does what it says.** It is set for that
one command and put back afterwards, so `while IFS= read -r line; do … done < file` keeps the spaces
at the start and end of each line. See
[Environment variables](variables-and-input.md#environment-variables).

**`{ echo hi }` says it is missing `}`.** The closing brace is a word of its own, and a command before
it ends with `;` or a new line: `{ echo hi; }`.

**A quoted string over several lines is cut at the end of a line.** In a script file each line is
read on its own. Use a here-doc or `echo -e "line 1\nline 2"` for a block of text.

## Where to go next

[Loops and menus](loops.md) has more on `while`, `for` and `select`. [Printing text](printing.md)
covers `echo` and `printf`. [Variables and input](variables-and-input.md) covers asking the user.
