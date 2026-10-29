# Scripting guide

← [Manual](../README.md)

This guide is for you if you want ShellCraft to do things for you: greet you when it starts, set up
your project, ask which server to deploy to, or turn five commands you type every day into one. You
don't need to have written a script before. By the end you'll be able to keep values in variables,
ask the user a question, print text, make decisions, repeat things, offer a menu, wrap it all up in
a function, and deal with errors.

Every example is a real script you can copy, and the test suite runs each one to make sure it still
does what the page says.

## What a script is

A script is a list of the same commands you type at the prompt, written down so ShellCraft can run
them for you, one line after another. If you can type `cd ~/projects` and `echo hello` at the
prompt, you can already write a script: put those two lines in a file.

What makes a script more than a list is that it can decide and repeat. It can keep a value in a
variable, run a line only if a file exists, do something once for every file in a folder, or stop
and ask you a question. The pages in this guide cover each of those, one at a time.

## Where scripts live

The same lines work in every one of these places. Pick the one that matches when you want them to
run:

| Where | When it runs | Read more |
| --- | --- | --- |
| The prompt | When you press Enter. A block over several lines waits for you to finish it | [Pipes and scripting](../scripting.md) |
| `~/.shellcraft/rc` | Every time the shell starts | [Startup](../startup.md) |
| `~/.shellcraft/startup/*.sc` | Right after the rc file, in name order | [Startup](../startup.md#startup-files) |
| `~/.shellcraft/funcs/NAME.func` | When you type `func run NAME` (or `NAME`, with `command: yes`) | [Funcs](../funcs.md) |
| Any file | When you type `source FILE` | `man source` |

Some ways to choose. Things you want every time, like a greeting or a few functions, go in the rc
file. When the rc file gets long, split it into startup files: `10-project.sc`, `20-todo.sc` and so
on, which run in that order. A job you run now and then, like setting up a new project, makes a
good func. A file you `source` is for anything else, and it's also the easiest way to try things
out.

All of them run in your shell, as if you typed each line. So a variable or a function that your rc
file sets is still there at the prompt, and a startup file can use what the rc file set up. A `cd`
in a script moves you too.

## Two styles

Every block can be written in **bash style** or **Python style**, and you can mix them in the same
file.

Bash style marks a block with words: `do` … `done`, `then` … `fi`. If you've seen a bash script,
this looks familiar, and you can paste most bash examples from the web.

Python style marks a block with a `:` at the end of the first line and an indented body, the way
Python does. A `-py` after the keyword says "this one is Python style", and the conditions are
Python conditions, like `n == "2"` or `name.startswith("dev")`. If you know Python, this may feel
more natural.

Here's the same loop both ways:

<!-- run -->
```
for n in 1 2 3; do
  if [ $n -eq 2 ]; then echo "two"; else echo "$n"; fi
done

for -py n in 1 2 3:
    if n == "2":
        echo "two"
    else:
        echo "$n"
```

<!-- output -->
```
1
two
3
1
two
3
```

In the first loop, `[ $n -eq 2 ]` is a test command that compares numbers, and `fi` and `done` close
the blocks. In the second, `n == "2"` is a Python comparison. There's no `$` in front of `n` there,
because Python conditions use names the way Python does. Inside the `echo` line you still write
`$n`, because that's a normal shell command.

Use whichever you find easier to read. The pages show both, and
[Bash style and Python style](python-style.md) puts every kind of block side by side.

## Pages, and what order to read them in

If you're new to this, read them in this order. Each page starts simple and builds on the ones
before it:

| Page | You'll learn |
| --- | --- |
| [Variables and input](variables-and-input.md) | Variables, quoting, default values, `${…}` trimming and lengths, arithmetic, asking the user with `input()` and `read`, environment variables |
| [Printing text](printing.md) | `echo`, several lines at once, `printf` for columns and numbers, colors, errors, writing to files |
| [Conditions and choices](conditions.md) | `if`, `elif`, `else`, every test, `&&` and `||`, `case` for picking between choices, Python conditions |
| [Loops and menus](loops.md) | `for`, `while`, `until`, nested loops, `break` and `continue`, reading a file line by line, `select` menus |
| [Functions](functions.md) | Making your own commands: arguments, defaults, `local`, `return`, giving back a value, `def`, options like `-v` |
| [Errors and cleanup](errors.md) | Stopping on the first failure with `set -e`, `set -u`, `set -x` and `pipefail`, cleaning up with `trap`, catching failures with `try`, `except` and `finally` |
| [Bash style and Python style](python-style.md) | Every kind of block written both ways, side by side: `if`, `for`, `while`, `case`, `select`, asking the user, menus, functions, `try` |
| [Complete examples](examples.md) | Whole rc files, startup files and funcs you can copy and adapt |

Variables come first because everything else uses them. If you only want something working today,
skim the first two pages and then go to [Complete examples](examples.md): find the one closest to
what you want, copy it and change it. Come back to the other pages when a line in it puzzles you.

## Try things out

The quickest way to try a script is to put it in a file and run it with `source`. Make a file called
`try.sc` in any folder, paste an example into it, and run:

```
source try.sc
```

Change the file and run it again. Nothing about your setup changes until you put the lines in your
rc file or a startup file. `. try.sc`, bash's short way of writing it, works too.

If a line fails, ShellCraft prints the file name and line number, like `✗ try.sc:4: …`, and carries
on with the next line. That's on purpose: one broken line in your rc file shouldn't stop the shell
from starting. If you'd rather a script stop at the first failure, see
[Errors and cleanup](errors.md).

You can also type any example straight at the prompt. A `for`, `if`, `case` or function that isn't
finished yet makes the prompt wait for the next line, so you can paste a whole block. After a
Python-style line ending in `:`, the prompt indents the next line for you, and an empty line runs
the block.

## Check a script before you run it

A script that deletes files or deploys something is a bad thing to test by running it. `source -n
FILE` reads a file and runs nothing. It tells you about every line that won't work, with the file
name and line number, so you can fix them all before the first real run.

Here the `printf` lines write a small file with three mistakes in it: a line indented wrongly, a
`for` with no `done`, and a typo in a command name.

<!-- run -->
```
printf '%s\n' 'echo "Backing up"' > backup.sc
printf '%s\n' 'for -py name in notes todo:' '    cp $name.txt backup/' '  echo "copied $name"' >> backup.sc
printf '%s\n' 'for f in *.log; do' '    ecoh "found $f"' >> backup.sc
cat backup.sc
source -n backup.sc
```

<!-- output -->
```
echo "Backing up"
for -py name in notes todo:
    cp $name.txt backup/
  echo "copied $name"
for f in *.log; do
    ecoh "found $f"
backup.sc:4: unindent does not match any outer indentation level
backup.sc:5: missing 'done': the block that starts here never ends
backup.sc:6: warning: unknown command 'ecoh' (did you mean 'echo'?)
3 problems (2 errors, 1 warning)
```

Line 4 is indented by two spaces, but the block above it uses four, so ShellCraft can't tell which
block it belongs to. Line 5 starts a loop that never reaches a `done`. Line 6 has a typo, and the
check even suggests the fix.

An error is a line that won't parse, so it can't run at all. A warning is a command that nothing
answers to: no function in the file or the shell, no alias, builtin, module, func or program on
your PATH has that name. A clean file gives one line, like `ok: 12 lines, no problems`.

Every command finishes with an exit status, a number that says how it went. 0 means success, and
anything else means some kind of failure. Scripts use it to decide what to do next, and you can see
the last one with `echo $?`. For `source -n`, the status is 0 when the file is clean, 1 when there
are only warnings, and 2 when something won't parse.

`source -n ~/.shellcraft/rc` checks your rc file, and `func check NAME` checks a func. It's worth
running after every edit to your rc file, since a mistake there shows up every time the shell
starts.

## ShellCraft is not bash

ShellCraft reads scripts that look like bash, and most of what you know from bash works. But the
commands are ShellCraft's own builtins and modules (`help` lists them), not Linux programs. A few
bash features aren't there, and each one has a ShellCraft way to do the same thing. If you're
copying a script from the web and a line fails, look for it here:

| In bash you'd write | In ShellCraft | Where |
| --- | --- | --- |
| `cat <<EOF` (a heredoc) | `echo -e "line 1\nline 2"`, `printf`, or keep the text in a file and `cat` it | [Printing text](printing.md#several-lines-at-once) |
| A quoted string over several lines | `echo -e` with `\n`, or one `echo` per line | [Printing text](printing.md#several-lines-at-once) |
| `while read line; do … done < file` | `for -py line in (cat file):` and an indented body | [Loops and menus](loops.md#go-through-a-file-line-by-line) |
| `read` from a pipe | `read` always asks the person at the keyboard | [Variables and input](variables-and-input.md#ask-the-user) |
| Arrays: `list=(a b c)` | A plain variable with spaces, looped over: `list="a b c"` | [Loops and menus](loops.md#loop-over-a-list) |
| `(( n > 3 ))` and `let` | `[ $n -gt 3 ]` and `n=$((n + 1))` | [Conditions and choices](conditions.md#comparing-numbers) |
| `{ cmd1; cmd2; }` to group | An `if` block, or a function | [Conditions and choices](conditions.md#if-then-else) |
| `sed`, `awk` and other Linux programs | ShellCraft's own `grep`, `cut`, `tr`, `sort`, `jq`… OS programs only run with `settings system_commands on` | [Pipes and scripting](../scripting.md) |

One smaller difference catches people out: a variable stays one piece wherever you use it, spaces
and all, except in the list of a `for` loop.
[Variables and input](variables-and-input.md#if-it-doesnt-work) explains it.

`man for`, `man if`, `man case`, `man test`, `man read` and `man function` have the details of each one.
