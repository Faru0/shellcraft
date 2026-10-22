# Pipes, scripting and startup

← [README](../README.md) · [All docs](README.md)

How command lines work in ShellCraft: pipes and redirection, variables, loops and conditions,
functions, exit status and history, aliases, and the files that run at startup.

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
(below): `env` then shows them as coming `from rc`, and `settings` lists them under
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

**`-py`** is ShellCraft's own syntax for when quoting gets in the way: the loop is parsed by
ShellCraft, `$x` is always **one** argument whatever it contains, and conditions are Python.

```
for -py ip in (cat ips.txt) {                              # one item per line of output
  if (ip.startswith("10.")) { echo internal $ip }
  elif (ip in ("8.8.8.8", "1.1.1.1")) { echo dns $ip }
  else { ip2geo $ip }
}
for -py n in 1..5 { if (int(n) % 2 == 0) { echo even $n } }
if -py (match(r"^v\d+", ver)) { echo versioned }
```

- Python conditions are checked and evaluated by ShellCraft (no `eval`): comparisons,
  `and`/`or`/`not`, `in`, slicing, string methods, `len`, `int`, `match(regex, text)`,
  `exists(path)` and a few more; imports and other functions are refused. `status` is `$?`.
- Inside a `-py` block, nested `for`/`if` use the same syntax without the flag.

In both: `break`/`continue`; a failing command shows its error and the loop goes on, as in bash;
loop variables and `x=1` assignments outlive the loop. `man for` and `man if` have the details.
Loops also work over MCP, with the same restrictions.

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
```
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

## Aliases

```
alias ll='ls -l'        # a shorter name for a command you type often
alias ls='ls -a -l'     # or change what a command does by default
alias                   # list your aliases
unalias ll              # remove one (unalias -a removes all)
\ls                     # a leading backslash skips the alias
```

An alias replaces the first word of a command, and anything you type after it is added at the end.
Aliases work in every step of a pipeline and apply in the shell, never to AI clients over MCP.
`which ls` shows whether a name is an alias.

They are kept in **`~/.shellcraft/aliases`**, a plain text file like bash's `~/.bash_aliases`:

```
# my shortcuts
alias ll='ls -l'
alias errors='grep -i error'
```

Edit it in any editor and the change applies to the next command, no restart needed. `alias` and
`unalias` edit the same file and leave your comments alone, `alias --file` prints its path, and a
line that can't be used (a `|` in the value, an unclosed quote) is skipped and listed by `alias`
with its line number. Aliases from older versions, saved in `config.json`, move into the file the
first time the new version starts.

## Startup: the rc file and scripts

**`~/.shellcraft/rc`** is ShellCraft's `~/.bashrc`: a file of ShellCraft command lines that run when
the interactive shell starts, as if you typed them. Anything you can type works there:

```
theme nord
env set PROJECT_ROOT=~/projects
env set -s GITHUB_TOKEN              # a secret, asked with hidden typing at each start
cd ~/projects
sub find ~/projects -name "*.log" -mtime -1
alert -l info "shell ready"
```

It can also ask you something, Python style, and use the answer later:

```
name = input("What is your name? ")
echo "hello $name"
count = int(input("How many servers? "))   # asks again until it gets a whole number
for n in $(seq $count); do echo server-$n; done
read -s -p "API token: " token      # the bash spelling; -s hides what you type
```

As in bash, the rc file can pull in other files, so you can keep one per use case:

```
if [ -f ~/.shellcraft/rc.work ]; then
    . ~/.shellcraft/rc.work
fi
for f in ~/.shellcraft/rc.d/*.rc; do [ -f "$f" ] && source "$f"; done
```

A failing line is reported with its line number (`✗ rc:4: …`) and the rest still run.
`source FILE` runs any such file by hand (`source ~/.shellcraft/rc` reruns it). It never runs for
`--mcp` / `--mcp-http`. `templates/startup/rc.template` is a short starter (a question, a variable, `myip`, a loop) and `templates/startup/rc.example` a longer commented one.

For things a command line can't do, Python scripts in **`~/.shellcraft/startup/`** run when the interactive shell starts, in name order
(`10-env.py`, `20-mounts.py`), before the first prompt. Use them to set environment variables for
everything ShellCraft starts later, check a mount or a VPN, or print a reminder. A script is its own
process, so it hands variables back by appending `NAME=value` lines to the file named in
`$SHELLCRAFT_ENV_FILE`:

```python
import os
with open(os.environ["SHELLCRAFT_ENV_FILE"], "a") as f:
    f.write("PROJECT_ROOT=/srv/app\n")
```

The shell, every subshell and every module then see `PROJECT_ROOT`. A script that fails or runs
longer than 30 s is reported in one line and the shell starts anyway. They run before the rc file,
so its lines see their variables. `startup` lists them with their last result, `startup run` runs
them again, `settings startup off` or `--no-startup` skips both the scripts and the rc file.

A script that should keep running (a watcher, a tunnel, a periodic check) starts in the
background instead, with a header line, so the shell never waits for it:

```python
# shellcraft: background, restart
```

It becomes a subshell (`startup: 30-watcher.py` in the panel; Ctrl-] N attaches, **Ctrl-] x** ends
it), `restart` starts it again after a crash (giving up after 5 crashes in 10 minutes, with an
alert), and `startup stop / start / restart NAME` control it. `# shellcraft: timeout=120` gives a
normal script more than 30 s. `templates/startup/30-watcher.example.py` is an example. `templates/startup/10-example.py` is a commented example; `man startup` has the details.
