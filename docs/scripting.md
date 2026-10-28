# Pipes and scripting

← [Manual](README.md)

This is the quick reference for ShellCraft command lines: pipes and redirection, variables, loops
and conditions, functions, error handling and exit status. Come here when you already know roughly
what you want and need the exact spelling. Each section starts with a line on when you'd use it,
then gives copy-paste lines.

If you're learning to script, start with the [scripting guide](scripting/README.md) instead. It
explains everything here step by step, with an example you can run for each idea. Aliases and
history have [their own page](aliases-and-history.md), and so do
[the rc file and startup files](startup.md) and [funcs](funcs.md).

## Pipes and redirection

Use a pipe when one command's output is the next one's input, like filtering a log. Use
redirection to save output to a file or read input from one.

- `a | b | c` passes each command's output to the next command as input. All of them run at once, and lines flow as they are made: `yes | head -3` stops `yes`, and `tail -f app.log | grep ERROR` prints each error the moment it is written (Ctrl-C stops it). A pipeline that finishes quickly looks as before, colors and pager included.
- `> file` writes the final output to a file, and `>> file` appends to it.
- Quote arguments that contain spaces or operators: `grep "a | b" notes.txt`. Backslashes are kept as typed, so Windows paths like `C:\data\log.txt` work.
- If any command fails, the pipeline stops, an error panel names the failing step, and nothing is written to the redirect file.
- `cd` only works on its own line. `cd x | pwd` is refused, because it would change the shell's directory from inside a pipeline.

To run several commands on one line, or one only when another worked:

- `a ; b` runs one command after the other. `a && b` runs `b` only if `a` succeeded, and `a || b` only if it failed. `! a` negates (see [Loops and conditions](#loops-and-conditions)).
- `cmd < file` feeds a file to the first command.

Error messages go to a separate stream from normal output, so you can save or filter them on
their own:

- `cmd 2> err.txt` (or `2>>`) saves the failure message. `cmd 2>&1 | grep …` turns the message into the command's output, and the pipeline goes on. `cmd &> both.txt` saves both.
- `cmd >&2` (or `1>&2`) sends a command's output to the error stream instead: `echo "error: no host given" >&2`
  shows in the error style and leaves `$?` alone. A `2> file` on the same line, or on the function call or
  loop around it (`myfn 2> err.txt`, `done 2>&1 | grep …`), decides where it goes.

ShellCraft also fills in parts of a line for you before it runs:

- `*.log`, `?` and `[abc]` expand to matching file names. The word stays as typed when nothing matches.
- `$(command)` is replaced by the command's output.
- `$((n * 2))` is arithmetic.

A trailing `&` runs the line in the background, as a subshell job (see [Subshells](subshells.md)):
`grep -r TODO . &`. An `&` anywhere else is rejected with a parse error rather than passed on as an
argument. Quote it to use it as text.

## Comments

Comments are notes for you. Use them in your rc file and funcs to say why a line is there.

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

A variable keeps a value you want to use again, like a host name you type in several commands.
You set it without a `$` and read it with one: `host=x` stores, `$host` reads.

```
host=example.com                 # a shell variable for this session
queryDns $host                   # $name expands anywhere outside single quotes
echo "$HOME $PATH"               # environment variables work too (never over MCP)
export API_URL=https://x.test    # ... and export makes a variable one; unset removes it
n=$((n + 1))                     # integer arithmetic: + - * / % ** comparisons, && || !
```

There are no spaces around the `=`. `x = 5` at the prompt runs a command named `x` and fails with
`command not found: x`. Python-style blocks are the exception, see
[Python assignment](#python-assignment).

A value with spaces stays one argument on a plain command line (`f="my file"; cat $f`). Inside
bash-style blocks it is split at whitespace unless quoted, as in bash.

[Variables and input](scripting/variables-and-input.md) has more, including asking the user
with `input()` and `read`.

## Environment variables

Use `env` for values that programs and modules should see too, like an API URL or a token. A
plain variable stays inside ShellCraft's scripts; an environment variable is handed to every
command you run.

`env` manages the environment variables you set in ShellCraft. The rest of the environment stays
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

ShellCraft's value wins over your shell's. `env unset` brings your shell's back, while the bash
`unset NAME` removes the variable for the session. Variables set with `export` are listed too.

They last for the session. To set them at every start, put the lines in `~/.shellcraft/rc`
([Startup](startup.md)): `env` then shows them as coming `from rc`, and `settings` lists them under
"Environment (this session)". `man env` has the details.

## Loops and conditions

Use a loop to do the same thing for many items, like every host in a file. Use a condition to do
something only when it makes sense, like reading a file only if it exists.

There are two syntaxes, and you can mix them. **Bash style** is the default, and works the way
you'd type it in bash:

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

- Expansion inside these blocks is bash's: an unquoted `$x` or `$(…)` is split at whitespace.
  Quote it to keep it whole: `"$x"`. The `for` list gets brace expansion and globs.
- Conditions are commands. A command that succeeds (status 0) counts as true. You can use
  `[ … ]`, `test`, `grep -q`, `true` and `false`, and `[[ … ]]`, which also understands `==`
  patterns, `=~` regexes, `<` / `>` / `<=` / `>=` text order, Python's `in` / `not in` / `is` /
  `is not`, and `&&`, `||`, `!` and `( … )` inside: `[[ -f $f && ( $x == a || $x == b ) ]]`.
  `!`, `&&` and `||` work anywhere. `man test` lists the tests.

### Python style

Reach for Python style (`-py`) when quoting gets in the way, for example with values that contain
spaces. The block is parsed by ShellCraft, `$x` is always **one** argument whatever it contains,
and conditions are Python. A header ends in `:` and the block is the indented lines under it:

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
select -py env in dev prod:                                # a numbered menu
    break
name = input("Your name? ")                                # asking works in any block
```

At the prompt, the next line starts indented for you and an empty line runs the block. The brace
form is the same thing on one line: `for -py n in 1..5 { if (int(n) % 2 == 0) { echo even $n } }`.

- Python conditions are checked and evaluated by ShellCraft (no `eval`): comparisons,
  `and`/`or`/`not`, `in`, slicing, string methods, `len`, `int`, `match(regex, text)`,
  `exists(path)` and a few more, plus the shell functions you define (`add(1, 2)`). Imports and
  other functions are refused. `status` is `$?`.
- Inside a `-py` block, nested `for`/`if` use the same syntax without the flag. Bash lines
  (`if …; then … fi`) work inside one too, and a Python-style block works inside a bash function.
- A Python-style condition that isn't Python runs as a command: `if -py grep -q x f:`.

### Pick one of several values with match

Use `match` when one value decides between several actions, like what to do for `dev`, `test` or
`prod`. It's the Python-style `case`:

```
match f:                                                   # case "a" | "b":, case "*.py":, case _:
    case "*.py":
        echo python
    case "dev" | "test":                                   # either value
        echo safe
    case _:                                                # anything else
        echo other
case -py $f:                                               # the other spelling: "a":, _:
    "*.py": echo python
    _: echo other
```

`match f:` and `match $f:` both work. The first `case` that fits runs, and the rest are skipped. `case "*.py":` is a file-name pattern, as in bash's `case`.

### Python assignment

Inside a `-py` block or a `def`, you can set a variable the Python way, with spaces around the
`=` and a Python value on the right:

```
for -py f in a.txt b.txt:
    count = 0                                              # = += -= *= /= //= %=
    count += 1
    label = f.upper() + "!"
    msg = f"{f:<10} {int(count):>3}"                       # f-strings, "{}-{}".format(a, b)
    print(msg, "done", sep=" | ")                          # print: like echo, sep= and end=
```

The value is stored as text, the same as `x=5` stores `5`. At the prompt and in bash blocks
`x = 5` stays a command named `x`, so it fails with `command not found: x`. `x=5` works
everywhere. f-strings and `.format()` work wherever Python is read, with the whole format
language (`{price:.2f}`, `{n:,}`, `{name:<10}`), and `input(f"Age of {who}? ")` too. `print(…)`
works inside a `-py` block or a `def` only. `man print` has the details.

### What both styles share

`break` and `continue` work in both, also after `&&` / `||`. A failing command shows its error and
the loop goes on, as in bash. Loop variables and `x=1` assignments outlive the loop. Loops also
work over MCP, with the same restrictions.

`man for`, `man if`, `man while`, `man case` and `man select` have the details.
[Conditions and choices](scripting/conditions.md) and [Loops and menus](scripting/loops.md) teach
them with examples.

## Functions

Write a function when you keep typing the same few commands with a different file or host each
time. It becomes a command of your own, for this session or, from your rc file, for every session.

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

Inside a function `$1`…`$9` are its arguments, `$#` how many and `$@` all of them. `return N` sets
its status: 0 means it worked, anything else means it failed. Variables are the shell's own unless
declared `local`, as in bash, so a function can change a variable you use later by accident.

A function wins over a builtin or module of the same name, but not over `exit`, `export`, `unset`,
`source` and a few other shell builtins. `\name` runs the command itself. Functions last for the
session (put them in `~/.shellcraft/rc` to keep them) and are never available to AI clients over
MCP. `man function`.

Inside a function, `"$@"` keeps each argument its own word, `shift` drops the first one, and
`${1:-default}` gives a default when an argument is missing:

```
each() { while [ $# -gt 0 ]; do echo "arg: $1"; shift; done; }
greet() { echo "hello ${1:-stranger}"; }
need() { [ -n "$1" ] || return 1; echo "got $1"; }
total=$(add 2 3)                             # a function's printed output as a value
```

### Python style: def

Use `def` when you'd rather name the arguments than count them. Each argument is a `local`:

```
def greet(name, greeting="hi"):                            # named arguments, with defaults
    msg = greeting.title() + " " + name                    # Python assignment works in a def
    echo "$msg"
def add(a, b):
    return int(a) + int(b)
def is_weekend(day):
    return day in ("Sat", "Sun")
def is_big(n):
    return add(n, 1) > 100                                 # call a function from Python, with brackets
if -py is_weekend("Sun"): echo rest
```

In a `def`, `return EXPR` gives back a Python value. What the caller sees depends on the value:

| `return` gives | Run as a command (`add 1 2`) | Called from Python (`add(1, 2)`) |
| --- | --- | --- |
| A number from 0 to 255 | That's the status: `add 1 2` sets `$?` to 3 | The number |
| `True` / `False` | Status 0 / 1, so it works in `if` and `&&` | `True` / `False` |
| Anything else | Printed: `add 300 4` prints `304` | The value |

Python code calls a function with brackets: `total = add(1, 2)` inside a `-py` block or a `def`.
[Functions](scripting/functions.md) teaches them step by step, and
[Bash style and Python style](scripting/python-style.md) shows every block both ways.

## Shell options with set

Turn these on at the top of a function, func or startup file when you want it to stop at the
first problem instead of carrying on, or to see each command as it runs while you debug.

```
set -e                    # a failing command stops the line, function, sourced file, rc or func
set -u                    # an unset $name is an error (and stops the same way)
set -x                    # show each command, expanded, before it runs: + echo hi
set -o pipefail           # a pipe's status is its last failing part's, not the last part's
set -euo pipefail         # several at once;  set +x turns one off;  set -o lists them
set -- a b c              # replace the arguments: $1 is a, $# is 3
```

`set -e` never closes the shell. It doesn't stop on the test of an `if` / `while`, a command
before `&&` / `||` or after `!`, because there a failure is an answer, not a problem. See
[Stop on errors](scripting/errors.md#stop-on-errors-with-set--e) and `man set`.

## Clean up with trap

Use a trap when something has to happen however a script ends, like removing a temp file or a lock,
even if a step fails or you press Ctrl-C.

```
trap 'rm -f $tmp' EXIT    # when this function, sourced file or func ends (at the prompt: when you leave)
trap 'echo stopped' INT   # on Ctrl-C, then it stops as usual
trap 'echo "failed: $?"' ERR   # after a command fails (not in tests, before && / ||, or after !)
trap - EXIT               # remove it;  trap '' INT does nothing on Ctrl-C;  trap lists them
```

The command is in single quotes so `$tmp` is read when the trap runs, not when you set it. A trap
belongs to the function, sourced file or func that set it. One set at the prompt, in the rc file
or in a startup file belongs to the session. See
[Clean up with trap](scripting/errors.md#clean-up-with-trap) and `man trap`.

## Catch failures with try

Use `try` when you expect a step might fail and want to handle it yourself, like reporting a failed
copy and carrying on, instead of stopping the whole script.

```
try { cp a b; echo copied } except as rc { echo "failed: $rc" } finally { rm -f lock }
```

The same in Python style:

```
try:                                                       # the first failing command jumps to except
    cp app.conf backup/
except as rc:                                              # rc (and $?): the failing status
    echo "copy failed: $rc"
finally:                                                   # always runs, also on break / return
    rm -f run.lock
```

The first command in the `try` block that fails (as `set -e` would see it) skips the rest and runs
`except`, with the status in NAME and `$?`. `finally` always runs. Inside a bash function the
blocks stay bash. A failure that `except` handles doesn't run the ERR trap or stop `set -e`. See
[Catch failures with try](scripting/errors.md#catch-failures-with-try) and `man try`.

## Options with getopts

Use `getopts` when a function or func should take switches like a real command: `-v` to be
chatty, `-f FILE` to pick a file.

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

Run a check after you edit your rc file, a startup file or a func, before it runs for real. It
finds typos and unfinished blocks without changing anything.

```
source -n deploy.sc           # every line that won't parse, every unknown command; nothing runs
source -n ~/.shellcraft/rc    # your rc file (or a startup file)
func check recon              # a func; line numbers are its file's, as in func edit
```

Each problem is one line, `FILE:LINE: message`, then a summary like `3 problems (1 error, 2
warnings)`. A clean file says `ok: 12 lines, no problems`. An error is a line that won't parse (a
`for` with no `done`, a colon block indented wrongly). A warning is a command nothing answers to.
The status is 0 when clean, 1 for warnings only, 2 for errors. See
[Check a script before you run it](scripting/README.md#check-a-script-before-you-run-it).

## Funcs and running from outside

A func is a script saved under a name in `~/.shellcraft/funcs/`. Use one for a routine you run on
demand, and run it from bash or cron with `shellcraft func run`:

```
func run recon example.com                    # inside ShellCraft
shellcraft func run recon example.com         # from bash or cron; the exit code is the func's status
```

[Funcs](funcs.md) explains how to write, check and schedule them.

## Exit status and history

Every command ends with a number, its exit status. Scripts use it to decide what to do next, and
the prompt shows it when something went wrong.

- `$?` is the last command's exit status: 0 for success, 1 for a failure, 2 for a syntax error,
  127 for an unknown or disabled command, 130 after Ctrl-C, and an OS program's own code. `grep`
  returns 1 when nothing matches, without an error panel, so `if grep -q …` works.
- The prompt shows `[✗ 127]` after a command fails.
- The prompt shows `⏱ 4.2s` after a command that took more than two seconds. `time CMD` prints
  the exact time of one command.

History expansion saves retyping:

- `!!` is the previous command, `!$` its last word and `!*` its other words, as in bash: `sudo !!`,
  `cat !$`.
- `!N` is command N of `history` (`!-2`: two back) and `!prefix` the last command that starts with
  `prefix`.
- The expanded line is printed before it runs and saved in the history. Nothing is expanded inside
  single quotes, and `!=`, `hi!` and `[[ ! -f x ]]` are left alone.

## Aliases and startup files

These have their own pages:

- [Aliases and history](aliases-and-history.md): shortcuts, the aliases file, and your command history
- [Startup](startup.md): the rc file and the startup files that run when the shell starts
