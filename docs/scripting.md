# Pipes and scripting

← [Manual](README.md)

How command lines work in ShellCraft: pipes and redirection, variables, loops and conditions,
functions, and exit status. This page is the quick reference. To learn scripting step by step,
with an example for everything, read the [scripting guide](scripting/README.md). Aliases and
history have [their own page](aliases-and-history.md), and so do
[the rc file and startup files](startup.md).

## Pipes and redirection

- `a | b | c` passes each command's output to the next command as input. All of them run at once, and lines flow as they are made: `yes | head -3` stops `yes`, and `tail -f app.log | grep ERROR` prints each error the moment it is written (Ctrl-C stops it). A pipeline that finishes quickly looks as before, colors and pager included.
- `> file` writes the final output to a file, and `>> file` appends to it.
- Quote arguments that contain spaces or operators: `grep "a | b" notes.txt`. Backslashes are kept as typed, so Windows paths like `C:\data\log.txt` work.
- If any command fails, the pipeline stops, an error panel names the failing step, and nothing is written to the redirect file.
- `cd` only works on its own line. `cd x | pwd` is refused, because it would change the shell's directory from inside a pipeline.

- `a ; b` runs one command after the other; `a && b` runs `b` only if `a` succeeded, `a || b` only if it failed; `! a` negates (see [Loops and conditions](#loops-and-conditions)).
- `cmd < file` feeds a file to the first command. A command's failure message goes where you send it: `cmd 2> err.txt` (or `2>>`), `cmd 2>&1 | grep …` (the message becomes the command's output, and the pipeline goes on), `cmd &> both.txt`.
- `cmd >&2` (or `1>&2`) sends a command's output to the error stream instead: `echo "error: no host given" >&2`
  shows in the error style and leaves `$?` alone. A `2> file` on the same line, or on the function call or
  loop around it (`myfn 2> err.txt`, `done 2>&1 | grep …`), decides where it goes.
- `*.log`, `?` and `[abc]` expand to matching file names (the word stays as typed when nothing matches); `$(command)` is replaced by the command's output; `$((n * 2))` is arithmetic.

A trailing `&` runs the line in the background, as a subshell job (see [Subshells](subshells.md)):
`grep -r TODO . &`. An `&` anywhere else is rejected with a parse error rather than passed on as an
argument; quote it to use it as text.

## Comments

A line that starts with `#` is a comment. So is everything after a `#` that has a space or tab
before it and after it:

```
# this whole line is ignored
cd ~/projects        # so is this note
theme nord           ## any number of # works
```

The same rule applies at the prompt, in the rc file, in funcs, in profiles and in the aliases file.

A `#` that touches other text is not a comment, so these keep working without quotes:

```
profile set work color #05d9e8
grep #TODO notes.py
echo $#
curl https://example.com/page#top
```

Text in quotes is never a comment: `echo "a # b"` prints `a # b`. The one thing to quote is a `#`
standing alone as an argument, as in bash: `cut -d '#' -f2`. The prompt shows comments in a muted
color, so you can see what will be ignored.

## Variables

```
host=example.com                 # a shell variable for this session
queryDns $host                   # $name expands anywhere outside single quotes
echo "$HOME $PATH"               # environment variables work too (never over MCP)
export API_URL=https://x.test    # ... and export makes a variable one; unset removes it
n=$((n + 1))                     # integer arithmetic: + - * / % ** comparisons, && || !
```

A value with spaces stays one argument on a plain command line (`f="my file"; cat $f`); inside
bash-style blocks it is split at whitespace unless quoted, as in bash.

[Variables and input](scripting/variables-and-input.md) has more, including asking the user
with `input()` and `read`.

## Environment variables

`env` manages the environment variables you set in ShellCraft; the rest of the environment stays
out of the way until you ask for it:

```
env set API_URL=https://api.example.test     # set one for this session (several: env set A=1 B=2)
env set -s GITHUB_TOKEN                      # a secret: hidden typing, masked, never in the history
env                                          # what this session set: name, full value, where from
env -a                                       # the whole environment (env -a | grep -i proxy)
env unset API_URL                            # drop it; a value your shell had comes back
```

A variable set here goes into ShellCraft's own environment at once, so modules see it straight
away, and OS programs, `sub` programs, lite subshells and `cmd &` see it when they start. A
server or subshell that is already running keeps its old environment (`mcp restart` passes a
change on), and an AI client's own `--mcp` server is a separate process that never sees it.
ShellCraft's value wins over your shell's; `env unset` brings your shell's back, while the bash
`unset NAME` removes the variable for the session. Variables set with `export` are listed too.

They last for the session. To set them at every start, put the lines in `~/.shellcraft/rc`
([Startup](startup.md)): `env` then shows them as coming `from rc`, and `settings` lists them under
"Environment (this session)". `man env` has the details.

## Loops and conditions

Two syntaxes. **Bash** is the default, and works the way you'd type it in bash:

```
for ip in 1.1.1.1 8.8.8.8; do ip2geo $ip; done
for h in $(cat hosts.txt); do queryDns $h >> dns.txt; done
for n in {1..5}; do echo $n; done                         # {a,b}, {01..10}, {a..e} too
for f in *.log; do if grep -q ERROR "$f"; then echo "$f"; fi; done

for n in {1..10}                                          # an unfinished loop continues on the next line
do
  if [ $n -gt 5 ]; then echo big $n; elif [[ $n == 1* ]]; then echo ten-ish; else echo $n; fi
done

n=0; while [ $n -lt 3 ]; do echo $n; n=$((n+1)); done     # while and until
case $f in *.py) echo python;; *.md|*.txt) echo text;; *) echo other;; esac
select env in dev staging prod; do echo "deploy to $env"; break; done   # a numbered menu ($PS3, $REPLY)
for f in *.log; do wc -l $f; done | sort -n | tail -3     # a whole loop can be piped or redirected
[ -f hosts.txt ] && echo found || echo missing
```

- Expansion inside these blocks is bash's: an unquoted `$x` or `$(…)` is split at whitespace
  (quote it: `"$x"`), and the `for` list gets brace expansion and globs.
- Conditions are commands: `[ … ]`, `[[ … ]]` (with `==` patterns, `=~` regexes, `<` / `>` / `<=` / `>=`
  text order, Python's `in` / `not in` / `is` / `is not`, and `&&`, `||`,
  `!` and `( … )` inside: `[[ -f $f && ( $x == a || $x == b ) ]]`), `test`, `grep -q`,
  `true`/`false`. `!`, `&&` and `||` work anywhere. `man test` lists the tests.

**Python style** (`-py`) is ShellCraft's own syntax for when quoting gets in the way: the loop is
parsed by ShellCraft, `$x` is always **one** argument whatever it contains, and conditions are
Python. A header ends in `:` and the block is the indented lines under it:

```
for -py ip in (cat ips.txt):                               # one item per line of output
    if ip.startswith("10."):
        echo internal $ip
    elif ip in ("8.8.8.8", "1.1.1.1"):
        echo dns $ip
    else:
        ip2geo $ip
for -py n in 1..5: echo $n                                 # a short block on one line
if -py match(r"^v\d+", ver):                               # if / elif / else, while / until
    echo versioned
case -py $f:                                               # "a" | "b":, "*.py":, _: (anything)
    "*.py":
        echo python
    _:
        echo other
select -py env in dev prod:                                # a numbered menu
    break
def greet(name, greeting="hi"):                            # named arguments, with defaults
    msg = greeting.title() + " " + name                    # Python assignment: = += -= *= /= //= %=
    echo "$msg"
def add(a, b):
    return int(a) + int(b)                                 # 0-255: the status, True/False: 0/1, else printed
def is_big(n):
    return add(n, 1) > 100                                 # call a function from Python: add(), slug()
name = input("Your name? ")                                # asking works in any block
```

At the prompt, the next line starts indented for you and an empty line runs the block. The brace
form is the same thing on one line: `for -py n in 1..5 { if (int(n) % 2 == 0) { echo even $n } }`.

- Python conditions are checked and evaluated by ShellCraft (no `eval`): comparisons,
  `and`/`or`/`not`, `in`, slicing, string methods, `len`, `int`, `match(regex, text)`,
  `exists(path)` and a few more, plus the shell functions you define (`add(1, 2)`); imports and
  other functions are refused. `status` is `$?`.
- Inside a `-py` block, nested `for`/`if` use the same syntax without the flag. Bash lines
  (`if …; then … fi`) work inside one too, and a Python-style block works inside a bash function.
- A Python-style condition that isn't Python runs as a command: `if -py grep -q x f:`.
- Inside a `-py` block or a `def`, `name = value` and `count += 1` take a Python value, stored as
  text. At the prompt and in bash blocks `x = 5` stays a command named `x`; `x=5` works everywhere.

In both: `break`/`continue` (also after `&&` / `||`); a failing command shows its error and the
loop goes on, as in bash; loop variables and `x=1` assignments outlive the loop. `man for`,
`man if`, `man while`, `man case` and `man select` have the details. Loops also work over MCP,
with the same restrictions. [Conditions and choices](scripting/conditions.md) and
[Loops and menus](scripting/loops.md) teach them with examples.

## Functions

```
greet() { echo "hello $1, you gave $# argument(s)"; }
greet Ada

backup() {                                   # over several lines, at the prompt or in ~/.shellcraft/rc
  local stamp=$(date +%Y%m%d)                # local: put back when the function returns
  cp -r "$1" "$1.$stamp" && echo "saved $1.$stamp" || return 1
}
countdown() { local n=$1; while [ $n -gt 0 ]; do echo $n; n=$((n - 1)); done; }
countdown 3 | sort -n                        # a function is a command: pipes, redirects, && / ||
declare -f · unset -f backup · type greet    # list, forget, inspect
```

Inside a function `$1`…`$9` are its arguments, `$#` how many and `$@` all of them; `return N` sets
its status. Variables are the shell's own unless declared `local`, as in bash. A function wins over
a builtin or module of the same name (not over `exit`, `export`, `unset`, `source` and a few other
shell builtins), and `\name` runs the command itself. Functions last for the session (put them in
`~/.shellcraft/rc` to keep them) and are never available to AI clients over MCP. `man function`.

Inside a function, `"$@"` keeps each argument its own word, `shift` drops the first one, and
`${1:-default}` gives a default when an argument is missing:

```
each() { while [ $# -gt 0 ]; do echo "arg: $1"; shift; done; }
greet() { echo "hello ${1:-stranger}"; }
need() { [ -n "$1" ] || return 1; echo "got $1"; }
```

`def name(a, b="x"):` writes one in the Python style, with named arguments (each a `local`).
In a `def`, `return EXPR` gives back a Python value, and Python code can call a function with
brackets: `total = add(1, 2)`.
[Functions](scripting/functions.md) teaches them step by step, and
[Bash style and Python style](scripting/python-style.md) shows every block both ways.

## Shell options with set

```
set -e                    # a failing command stops the line, function, sourced file, rc or func
set -u                    # an unset $name is an error (and stops the same way)
set -x                    # show each command, expanded, before it runs: + echo hi
set -o pipefail           # a pipe's status is its last failing part's, not the last part's
set -euo pipefail         # several at once;  set +x turns one off;  set -o lists them
set -- a b c              # replace the arguments: $1 is a, $# is 3
```

`set -e` never closes the shell. It doesn't stop on the test of an `if` / `while`, a command
before `&&` / `||` or after `!`. See [Stop on errors](scripting/functions.md#stop-on-errors-with-set--e)
and `man set`.

## Clean up with trap

```
trap 'rm -f $tmp' EXIT    # when this function, sourced file or func ends (at the prompt: when you leave)
trap 'echo stopped' INT   # on Ctrl-C, then it stops as usual
trap 'echo "failed: $?"' ERR   # after a command fails (not in tests, before && / ||, or after !)
trap - EXIT               # remove it;  trap '' INT does nothing on Ctrl-C;  trap lists them
```

A trap belongs to the function, sourced file or func that set it. One set at the prompt, in the
rc file or in a startup file belongs to the session. See
[Clean up with trap](scripting/functions.md#clean-up-with-trap) and `man trap`.

## Options with getopts

```
while getopts "vf:" opt; do   # -v, and -f with a value; "vf:" lists the letters
  case $opt in
    v) verbose=yes ;;
    f) file=$OPTARG ;;        # the value is in $OPTARG
    \?) return 2 ;;           # an unknown option (getopts prints a message)
  esac
done
shift $((OPTIND - 1))         # drop the options: $1 is the first word after them
```

`-vf FILE` and `-fFILE` work too. Start the letters with `:` (`":vf:"`) to print your own
messages. Each function call and func starts with `$OPTIND` at 1. See
[Options like -v and -f FILE](scripting/functions.md#options-like--v-and--f-file) and `man getopts`.

## Check a script without running it

```
source -n deploy.sc           # every line that won't parse, every unknown command; nothing runs
source -n ~/.shellcraft/rc    # your rc file (or a startup file)
func check recon              # a func; line numbers are its file's, as in func edit
```

Each problem is one line, `FILE:LINE: message`, then a summary like `3 problems (1 error, 2
warnings)`. An error won't parse (a `for` with no `done`, a colon block indented wrongly); a
warning is a command nothing answers to. The status is 0 when clean, 1 for warnings only, 2 for
errors. See [Check a script before you run it](scripting/README.md#check-a-script-before-you-run-it).

## Exit status and history

- `$?` is the last command's exit status: 0 for success, 1 for a failure, 2 for a syntax error,
  127 for an unknown or disabled command, 130 after Ctrl-C, and an OS program's own code. `grep`
  returns 1 when nothing matches, without an error panel, so `if grep -q …` works.
- The prompt shows `[✗ 127]` after a command fails.
- `!!` is the previous command, `!$` its last word and `!*` its other words, as in bash: `sudo !!`,
  `cat !$`. `!N` is command N of `history` (`!-2`: two back) and `!prefix` the last command that
  starts with `prefix`. The expanded line is printed before it runs and saved in the history (not
  inside single quotes; `!=`, `hi!` and `[[ ! -f x ]]` are left alone).
- The prompt shows `⏱ 4.2s` after a command that took more than two seconds; `time CMD` prints
  the exact time of one command.

## Aliases and startup files

These have their own pages now:

- [Aliases and history](aliases-and-history.md): shortcuts, the aliases file, and your command history
- [Startup](startup.md): the rc file and the Python scripts that run when the shell starts
