---
name: shellcraft-scripting
description: How to write, check and run ShellCraft scripts - rc and startup files, funcs, bash-style and Python-style (-py) blocks - and the mistakes an AI makes most (text vs numbers, elif, top-level assignment, lists). Load it when asked to write, fix or review a ShellCraft script, func, rc file or startup file.
---

# Writing ShellCraft scripts

ShellCraft scripts mix two styles in one file: **bash style** (`if …; then`, `$name`, `${x:-d}`)
and **Python style** (`if -py cond:` with an indented body, `print(f"…")`, `def`, `try:`,
`match`). Both are real, tested features. This skill covers what you get wrong if you write them
from memory. The full guide is `docs/scripting/README.md` in the ShellCraft folder, and
`man if`, `man def`, `man print`, `man set`, `man trap`, `man try`, `man getopts`, `man arrays` inside
the shell.

## Where a script lives

| File | Runs | How you run it |
| --- | --- | --- |
| `~/.shellcraft/rc` | at every start | `source ~/.shellcraft/rc` |
| `~/.shellcraft/startup/NN-name.sc` | after rc, in name order | `source FILE` |
| `~/.shellcraft/funcs/NAME.func` | `NAME ARGS`, or `func run NAME ARGS` (needed when a builtin, module or alias has the same name) | `shellcraft func run NAME ARGS` from a terminal or cron (the exit code is the func's status) |
| any file | when sourced | `source FILE` |

A func starts with `# func: NAME`, `# description: one line` and `# usage: NAME ARGS` comment
lines, then one step per line; it stops at the first step that fails. Every func runs when you type
its name, and `help` lists them; a builtin, module, alias or shell function of the same name wins,
so use `func run NAME ARGS` then. An old `# command: yes` line is ignored.

## Bash that works (so do not avoid it)

Here-docs and here-strings (`cat <<EOF`, `<<'EOF'`, `<<-EOF`, `read a <<< "x y"`), `read` from a pipe or a
file (`while IFS= read -r line; do …; done < file`, `cmd | while read …`), a trailing `\` to join
lines, brace expansion (`{a,b}{1,2}`, `{1..5}`) and backticks on any line, `VAR=x cmd`, arithmetic
(`(( n++ ))`, `if (( a > b ))`, `for ((i=0; i<3; i++))`, `let`, `$((n++))`, `$RANDOM`, `$SECONDS`),
`readonly`, `declare -i -r -x`, `printf -v VAR`, `[ -t 1 ]`, groups (`{ a; b; } | cmd`,
`( cd dir; cmd )`), `eval` and `exec`, arrays and dicts (`a=(x y)`, `a+=(z)`, `"${a[@]}"`,
`${#a[@]}`, `declare -A m`, `read -a`, `mapfile -t`). A plain command line does not split words at
spaces (a loop's `for x in $list` does).

## The loop: write, check, run

1. **Write** the file with your own file tools. No MCP tool writes files.
2. **Check** it before it runs: `run_in_shell("source -n FILE")`, or `func check NAME` for a func.
   Nothing executes. You get `FILE:LINE: message` lines and `N problem(s)`, or
   `ok: N lines, no problems`. It finds syntax, indentation and unknown commands. It does
   **not** find everything: a wrong value or a missing file shows only when it runs. Say what you
   did not test.
3. **Run** it only when the user wants it run, and say what it does first (files written,
   things deleted, hosts contacted). Put the risk in `run_in_shell`'s `reason`; use
   `request_human_review` for anything destructive.
4. **Fix and check again.** Read the error's line number; do not guess.

A script that asks a question (`input()`, `read -p`, `select`) needs the user at the keyboard.
You cannot answer it, so do not run it yourself: stage the command with `set_clip` and an `info`
alert, or write the script so it takes its values as arguments.

## What goes wrong, and the fix

- **Python style needs `-py` on the outer block.** `if n == "3":` at the top of a file is read
  as bash and stops with `missing 'then'`. Write `if -py n == "3":`. Inside a Python-style block
  or a `def`, `-py` is not needed.
- **`elif` takes no `-py`.** `elif -py X:` fails (`command not found: -py`). Write `elif X:`.
- **Text vs numbers.** A value set the bash way (`n=3`, `read`, a `for` word, a function
  argument, a `def` default like `def f(n=5)`) is text, so `n == 3` is false and `n + 1` fails
  with `can only concatenate str`. Use `int(n) == 3`, or compare to `"3"`. A value set in Python
  (`n = 3` inside a `-py` block or `def`, or `int(input(...))`) keeps its type; bash reads it
  as the text `3`.
- **`x = 5` works anywhere** (the prompt, a file, bash blocks, `-py` blocks, `def`), with the same
  typed values. It is a command instead when `x` is a builtin, module, alias, function or `def`
  (`echo = 5` runs echo); then use another name or write `x=5` with no spaces. `x=5` is the bash form.
- **`$name` is not expanded inside a Python string.** Write `f"hi {name}"` (no `$` inside the
  braces). `echo f"…"` is a bash line and prints the `f`. Use `print(f"…")` inside a block, or
  `echo "hi $name"`.
- **`print(...)` and `add(1, 2)` work anywhere** on a line of their own (a bare call shows what the
  `def` returned or printed). `print hello` without parentheses is not a command: use `echo`. A call
  with empty parentheses (`hi()`) works when `hi` is already defined; it must be alone on its line.
- **Lists and dicts work in both styles and share one storage** (`man arrays`). Bash:
  `hosts=(web01 db01)`, `hosts+=(x)`, `"${hosts[@]}"`, `declare -A port; port[web]=80`. Python:
  `hosts = ["web01"]`, `hosts.append("x")`, `port = {"web": 80}`, `for -py h in hosts:`,
  `for -py k, v in port.items():`. What differs from what you know:
  - Elements set the bash way are text: `sum(map(int, sizes))`, not `sum(sizes)`.
  - No list in a list, no comprehension, no `lambda`, no `key=`. Write a loop with `.append()`.
  - No gaps: `a[9]=x` past the end and `unset 'a[0]'` in the middle are errors; `del a[0]` removes
    an element and moves the rest up.
  - `b = a` copies, and a function gets a copy: `hosts = add(hosts, "x")` with `return items`.
  - A method that changes the list (`.append`, `.pop`, `.sort`) goes on a line of its own or in
    `x = hosts.pop()`, never inside a condition.
  - `export hosts` is refused: `export HOSTS="${hosts[*]}"`. `$hosts` is the first element only.
- **An unset variable is empty,** as in bash. Use `${v:-default}`, `: ${v:=default}` to set it
  once, `${v:?message}` to stop when it is missing, `set -u` to make any unset name an error.
- **Stopping on errors.** `set -e` stops the script (never the shell) at the first failing
  command; `try:` / `except as rc:` / `finally:` catches one; `trap 'rm -f $tmp' EXIT` cleans
  up however the script ends. In an rc file, end a `set -e` with `set +e`.
- **Indentation is syntax.** A block indented wrongly stops the whole file: `expected an indented
  block`. Use spaces, not tabs, and the same width throughout.

## A small example

```
# func: logs
# description: List the .log files in a folder
# usage: logs [DIR]
dir=${1:-.}
for -py name in (ls $dir):
    if -py name.endswith(".log"):
        echo "log: $name"
```

Check it with `func check logs`, then run `logs .`. Ask yourself whether each line
needs the user's say-so before it runs.

## Never

- Put a secret in a script, rc or func. Use `env set -s NAME` (asks without echoing) and `$NAME`.
- Add `exit` to an rc or startup file: it ends ShellCraft at that line. A user stuck there starts
  with `shellcraft --no-startup`.
- Change the user's rc or startup files without showing the change first and running
  `source -n` on the result.
- Claim a script works because `source -n` was quiet. It checks the shape, not the behaviour.
