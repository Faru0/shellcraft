# Scripting guide

← [Manual](../README.md)

This guide shows you how to write scripts for ShellCraft: your `rc` file, your startup files, your
funcs, and anything you'd type at the prompt. Every block can be written in **bash style**
(`do` … `done`) or **Python style** (a `:` and an indented body), and you can mix them. You'll learn how to keep values in variables, ask the
user a question, print text, make decisions, repeat things, offer a menu, and wrap it all up in a
function. Every example is a real script you can copy, and the test suite runs each one to make sure
it still does what the page says.

## Pages

| Page | You'll learn |
| --- | --- |
| [Variables and input](variables-and-input.md) | Variables, quoting, default values, arithmetic, asking the user with `input()` and `read`, environment variables |
| [Printing text](printing.md) | `echo`, several lines at once, `printf` for columns and numbers, errors, writing to files |
| [Conditions and choices](conditions.md) | `if`, `elif`, `else`, every test, `&&` and `||`, `case` for picking between choices, Python conditions |
| [Loops and menus](loops.md) | `for`, `while`, `until`, nested loops, `break` and `continue`, reading a file line by line, `select` menus |
| [Functions](functions.md) | Making your own commands: arguments, defaults, `local`, `return`, giving back a value, `def`, stopping on errors with `set -e` |
| [Bash style and Python style](python-style.md) | Every kind of block written both ways, side by side: `if`, `for`, `while`, `case`, `select`, asking the user, menus, functions |
| [Complete examples](examples.md) | Whole rc files, startup files and funcs you can copy and adapt |

If you're new to this, read them in order. Each page starts simple and builds up.

## Where scripts live

The same lines work in every one of these places. Pick the one that matches when you want it to run.

| Where | When it runs | Read more |
| --- | --- | --- |
| The prompt | When you press Enter. A block over several lines waits for you to finish it | [Pipes and scripting](../scripting.md) |
| `~/.shellcraft/rc` | Every time the shell starts | [Startup](../startup.md) |
| `~/.shellcraft/startup/*.sc` | Right after the rc file, in name order | [Startup](../startup.md#startup-files) |
| `~/.shellcraft/funcs/NAME.func` | When you type `func run NAME` (or `NAME`, with `command: yes`) | [Funcs](../funcs.md) |
| Any file | When you type `source FILE` | `man source` |

All of them run in your shell, as if you typed each line. So a variable or a function that your rc
file sets is still there at the prompt, and a startup file can use what the rc file set up.

## Try things out

The quickest way to try a script is to put it in a file and run it with `source`. Make a file called
`try.sc` in any folder, paste an example into it, and run:

```
source try.sc
```

Change the file and run it again. Nothing about your setup changes until you put the lines in your
rc file or a startup file. If a line fails, ShellCraft prints the file name and line number, like
`✗ try.sc:4: …`, and carries on with the next line.

You can also type any example straight at the prompt. A `for`, `if`, `case` or function that isn't
finished yet makes the prompt wait for the next line, so you can paste a whole block. After a
Python-style line ending in `:`, the prompt indents the next line for you, and an empty line runs
the block.

## Check a script before you run it

`source -n FILE` reads a file and runs nothing. It tells you about every line that won't work,
with the file name and line number, so you can fix them all before the first real run. Here the
`printf` lines write a small file with three mistakes in it: a line indented wrongly, a `for` with
no `done`, and a typo in a command name.

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

An error is a line that won't parse, so it can't run at all. A warning is a command that nothing
answers to: no function in the file or the shell, no alias, builtin, module, func or program on
your PATH has that name. A clean file gives one line, like `ok: 12 lines, no problems`.

The status tells a script what happened: 0 when the file is clean, 1 when there are only warnings,
and 2 when something won't parse. `source -n ~/.shellcraft/rc` checks your rc file, and
`func check NAME` checks a func.

## Two styles

Here's the same loop both ways. Use whichever you find easier to read:

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

The pages show both. [Bash style and Python style](python-style.md) puts every kind of block side
by side.

## ShellCraft is not bash

ShellCraft reads scripts that look like bash, and most of what you know from bash works. But the
commands are ShellCraft's own builtins and modules (`help` lists them), not Linux programs. A few
bash features aren't there, and each one has a ShellCraft way to do the same thing:

| In bash you'd write | In ShellCraft | Where |
| --- | --- | --- |
| `cat <<EOF` (a heredoc) | `echo -e "line 1\nline 2"`, `printf`, or keep the text in a file and `cat` it | [Printing text](printing.md#several-lines-at-once) |
| A quoted string over several lines | `echo -e` with `\n`, or one `echo` per line | [Printing text](printing.md#several-lines-at-once) |
| `while read line; do … done < file` | `for -py line in (cat file):` and an indented body | [Loops and menus](loops.md#go-through-a-file-line-by-line) |
| `read` from a pipe | `read` always asks the person at the keyboard | [Variables and input](variables-and-input.md#ask-the-user) |
| Arrays: `list=(a b c)` | A plain variable with spaces, looped over: `list="a b c"` | [Loops and menus](loops.md#loop-over-a-list) |
| `${#name}`, `${name^^}`, `${name/a/b}` | A Python condition, `tr`, `cut` or a module | [Variables and input](variables-and-input.md#what-you-can-do-with-a-value) |
| `(( n > 3 ))` and `let` | `[ $n -gt 3 ]` and `n=$((n + 1))` | [Conditions and choices](conditions.md#comparing-numbers) |
| `{ cmd1; cmd2; }` to group | An `if` block, or a function | [Conditions and choices](conditions.md#if-then-else) |
| `sed`, `awk` and other Linux programs | ShellCraft's own `grep`, `cut`, `tr`, `sort`, `jq`… OS programs only run with `settings system_commands on` | [Pipes and scripting](../scripting.md) |

`man for`, `man if`, `man case`, `man test`, `man read` and `man function` have the details of each one.
