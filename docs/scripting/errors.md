# Errors and cleanup

← [Scripting guide](README.md)

This page is for you once your scripts do more than one thing in a row, and a step in the middle
can go wrong: a file that isn't there, a server that doesn't answer, a typo in a name. After
reading it you'll be able to tell whether a command worked, stop a script at the first failure,
clean up a temporary file however the script ends, catch a failure and carry on, and see what a
script really did when it surprises you.

## What happens when a command fails

By default, ShellCraft reports a command that fails and then runs the next line anyway. That's the
same as bash, and it's what you want at the prompt: one mistake shouldn't throw away the rest of
your work.

When the command has something to say, you see it. At the prompt it shows in a red box marked
`✗ command failed`, with the command's own message, such as `cat: notes.txt: no such file`. In a
file you run with `source`, in your rc file or in a startup file, you also get a line with the file
name and line number, like `✗ deploy.sc:4: cat notes.txt`, so you know where to look.

Some commands fail quietly. A test like `[ -f notes.txt ]` or `grep -q` prints nothing at all. It
only answers "no". Here, the function goes on after the test said no, and tells you something
that isn't true:

<!-- run -->
```
check_settings() {
  echo "checking"
  [ -f settings.conf ]
  echo "settings found"
}
check_settings
echo "the next line runs too"
```

<!-- output -->
```
checking
settings found
the next line runs too
```

There's no `settings.conf`, but nothing stopped the next line. The rest of this page is about the
ways to notice that "no" and do something about it.

## Exit status and $?

Every command ends with a number called its exit status. `0` means it worked. Anything else means
it didn't. It's that way round because there's one way to succeed and many ways to fail, so the
other numbers are left free to say what went wrong. Most commands use `1` for "no" or "that
didn't work", and some use higher numbers for different problems.

`$?` is the status of the last command:

<!-- run -->
```
true
echo "true gave $?"
false
echo "false gave $?"
[ -f notes.txt ]
echo "the test gave $?"
echo "some notes" > notes.txt
[ -f notes.txt ]
echo "and now it gives $?"
```

<!-- output -->
```
true gave 0
false gave 1
the test gave 1
and now it gives 0
```

`true` and `false` do nothing except succeed and fail, which makes them handy for trying things
out. The test fails the first time because `notes.txt` isn't there yet, and works once the `echo`
has made it.

`$?` changes after every command, the `echo` included. If you need the status later, copy it
into a variable straight away:

<!-- run -->
```
printf "name=web\n" > app.conf
grep -q "^port=" app.conf
status=$?
echo "grep gave $status"
```

<!-- output -->
```
grep gave 1
```

A function has a status too. It's the number after `return`, or the status of its last command.
See [Succeed or fail with return](functions.md#succeed-or-fail-with-return).

## Act on a failure with || and if !

Most of the time you don't need to look at `$?` yourself. `&&` runs the next command only when
the one before it worked, and `||` runs it only when it failed. `if !` runs a block when a
command fails, which is handy when there's more than one thing to do about it.

Say you're reading a small config file and want sensible defaults for what's missing:

<!-- run -->
```
printf "name=web\nport=80\n" > app.conf
grep -q "^port=" app.conf && echo "port is set"
grep -q "^host=" app.conf || echo "no host, using localhost"
if ! [ -d backups ]; then
  echo "making the backups folder"
  mkdir backups
fi
```

<!-- output -->
```
port is set
no host, using localhost
making the backups folder
```

Read `||` as "or else": "find a host line, or else say we're using localhost". `!` turns a
command's answer around, so `if ! [ -d backups ]` means "if there's no backups folder".

Inside a function, `|| return 1` is the short way to say "if this fails, stop here and fail too".
[Check the arguments](functions.md#check-the-arguments) uses it. For every test you can write
after `if`, see [Conditions and choices](conditions.md).

## Stop on errors with set -e

Checking every command with `||` gets long when a script has ten steps and each one needs the one
before it. `set -e` turns the checking around: after it, the first command that fails stops the
function it is in, the line that called it, and the rest of the file. Here's the same function as
at the top of the page, with `set -e` first:

<!-- run -->
```
set -e
check_settings() {
  echo "checking"
  [ -f settings.conf ]
  echo "settings found"
}
check_settings
echo "this line doesn't run"
```

<!-- output -->
```
checking
✗ example.sc:7: check_settings (set -e: the rest of the file was skipped)
```

The test fails, so `echo "settings found"` never runs, and neither does anything after the call.
The message names line 7, the call to `check_settings`, because that's the line in the file that
stopped. When you try this yourself, you'll see your own file's name in place of `example.sc`.

`set -e` never closes ShellCraft. It stops what is running: the line you typed at the prompt, a
function, a `source`d file, your rc file, a startup file or a func. `$?` keeps the status of the
command that failed. `set +e` turns it off again.

### Tests don't stop the script

A failure doesn't stop anything when it is a question rather than an error: the test of an `if`,
`while` or `until`, a command before `&&` or `||`, and a command after `!`. So the usual checks
still work:

<!-- run -->
```
set -e
[ -f settings.conf ] && echo "found it"
if [ -f settings.conf ]; then
  echo "using settings.conf"
else
  echo "no settings.conf, using the defaults"
fi
echo "still running"
```

<!-- output -->
```
no settings.conf, using the defaults
still running
```

The same goes for a function used as a test: `if check_settings; then` runs the whole function,
because its failure is the answer you asked for.

That rule has a catch you should know about. In `cd build && make`, the `cd` is "before `&&`", so
if `build` doesn't exist the line ends false and the script carries on, without `set -e`
stopping it. When a step must work, put it on its own line, or say what to do with
`cd build || return 1`.

### set -e in a def function

`def` functions behave the same way:

<!-- run -->
```
set -e
def check_settings():
    echo "checking"
    [ -f settings.conf ]
    echo "settings found"

check_settings
echo "this line doesn't run"
```

<!-- output -->
```
checking
✗ example.sc:7: check_settings (set -e: the rest of the file was skipped)
```

## More options for careful scripts

`set` has three more options that help when you write a script. This table is the short version.
The sections after it show each one at work.

| Option | What it does |
| --- | --- |
| `set -u` | Using a variable that isn't set is an error that stops the script, so a typo like `$fiel` doesn't slip through. `${name:-default}` is still fine |
| `set -x` | Show each command, with its variables filled in, before it runs. Good for finding out what a script really does |
| `set -o pipefail` | A pipe fails when any part of it fails, not only the last one |

You can turn on several at once, as in bash: `set -euo pipefail`. A `+` turns one off (`set +x`).
`set -o` on its own shows which ones are on:

<!-- run -->
```
set -eu
set -o
```

<!-- output -->
```
errexit        on
nounset        on
pipefail       off
xtrace         off
```

Those long names are what the letters stand for: `-e` is `errexit`, `-u` is `nounset`, `-x` is
`xtrace`. `set -o pipefail` only has the long name.

### Catch typos with set -u

A variable that was never set is empty. So a typo quietly turns into nothing, and the script
carries on:

<!-- run -->
```
file=notes.txt
echo "saving $fiel"
```

<!-- output -->
```
saving 
```

In a script that goes on to copy or delete `$fiel`, that's the kind of mistake you want to catch
early. With `set -u`, the typo stops the script with a message:

```
set -u
file=notes.txt
echo "saving $fiel"         # fiel: unbound variable
```

When a variable may honestly be missing, give it a default and `set -u` leaves it alone:

<!-- run -->
```
set -u
echo "hello ${name:-friend}"
```

<!-- output -->
```
hello friend
```

For one variable a script can't do without, `${name:?message}` stops with your own message when
it's empty or not set, with or without `set -u`: `: ${API_URL:?set API_URL first}`. See
[Defaults, lengths and trimming](variables-and-input.md#defaults-lengths-and-trimming).

### Notice failures in a pipe with set -o pipefail

A pipe's status is normally the status of its last command. That hides a failure earlier in the
line. Here `grep` finds nothing, but `wc` works, so the pipe succeeds until `pipefail` is on:

<!-- run -->
```
printf "apple\nbanana\n" | grep cherry | wc -l
echo "status: $?"
set -o pipefail
printf "apple\nbanana\n" | grep cherry | wc -l
echo "status: $?"
```

<!-- output -->
```
0
status: 0
0
status: 1
```

With `pipefail` and `set -e` together, a pipe where any step fails stops the script.

### Set the arguments with set --

`set -- A B C` replaces the arguments: after it, `$1` is `A` and `$#` is 3. That's not about
errors, but it lives in the same command. See `man set` for everything.

## Clean up with trap

A function that makes a temporary file should remove it, even when a step fails halfway. Without
help, `set -e` would stop the function before it got to the `rm` at the end. `trap` runs a
command when the function ends, however it ends:

<!-- run -->
```
set -e
make_report() {
  local tmp=report.tmp
  trap 'rm -f $tmp; echo "removed $tmp"' EXIT
  echo "draft" > $tmp
  echo "writing the report"
  grep -q "final" $tmp
  echo "report done"
}
make_report
```

<!-- output -->
```
writing the report
removed report.tmp
✗ example.sc:10: make_report (set -e: the rest of the file was skipped)
```

Here's what happens. The `trap` line doesn't run anything yet: it says "when this function ends,
run these commands". Then `grep` fails, because the draft doesn't say "final". `set -e` stops the
function, so `report done` never prints. The function is ending, so the `EXIT` trap runs and
removes the file. Then the `✗` line tells you the call stopped the file.

The trap runs inside the function, so it sees its `local` variables and `$1`.

A trap belongs to the function that set it and goes away when the function ends. A file you run
with `source` and a func have their own traps too. A trap set at the prompt, in your rc file or
in a startup script belongs to the session: its `EXIT` trap runs when you leave ShellCraft.

### Single quotes, not double quotes

Put the trap's command in single quotes, so `$tmp` is filled in when the trap runs, not when you
set it. With double quotes, the variable is filled in straight away, and a later change doesn't
reach the trap:

<!-- run -->
```
first_try() {
  local tmp=draft.tmp
  trap "echo removing $tmp" EXIT
  tmp=final.tmp
}
second_try() {
  local tmp=draft.tmp
  trap 'echo removing $tmp' EXIT
  tmp=final.tmp
}
first_try
second_try
```

<!-- output -->
```
removing draft.tmp
removing final.tmp
```

`first_try` removes the wrong file. If you ever wonder what a trap will run, `trap` on its own
prints it, as it was stored.

### The three signals

The word at the end of the `trap` line says when the command runs:

| Signal | When the command runs |
| --- | --- |
| `EXIT` | When the function, file or func ends: at the end, on `return`, when `set -e` stops it, on Ctrl-C |
| `INT` | When you press Ctrl-C. Then it stops as usual |
| `ERR` | After a command fails, where `set -e` would stop (not in an `if` test, before `&&` or `\|\|`, or after `!`) |

`EXIT` is the one you'll use most, because it covers every way out. Reach for `INT` when pressing
Ctrl-C needs something extra, such as telling the user what was left half done:

```
trap 'echo "stopped by you, the backup is incomplete"' INT
```

An `ERR` trap is handy for logging. Inside it, `$?` is the failing command's status:

<!-- run -->
```
trap 'echo "failed with status $?"' ERR
echo "step one"
grep -q "needle" /dev/null
echo "step two"
if grep -q "needle" /dev/null; then echo "found"; fi
echo "done"
```

<!-- output -->
```
step one
failed with status 1
step two
done
```

The first `grep` fails and the trap reports it. The `grep` inside the `if` is a question, so it
doesn't count as an error. Notice that there's no `set -e` here, so the script carries on after
the trap. When a function has no trap of its own for `INT` or `ERR`, the one of whatever called it
runs.

### trap in a def function

`trap` works the same in a `def` function:

<!-- run -->
```
def backup(name):
    trap 'echo "backup of $name finished"' EXIT
    echo "copying $name"
    return 0

backup notes.txt
```

<!-- output -->
```
copying notes.txt
backup of notes.txt finished
```

### Look at and remove traps

`trap` on its own lists the traps set here, `trap - EXIT` removes one, and `trap '' INT` sets one
that does nothing. See `man trap`.

## Catch failures with try

`set -e` stops everything at the first failure. Sometimes you want to stop only a few steps and
then do something about it, such as print a message and go on with the next file. `try` does that.
Put the steps in `try { … }`, what to do on a failure in `except { … }`, and what must always
happen in `finally { … }`:

<!-- run -->
```
deploy() {
  try {
    echo "checking $1"
    [ -f "$1" ]
    echo "deploying $1"
  } except as rc {
    echo "$1 is missing (status $rc), skipped"
  } finally {
    echo "done with $1"
  }
}
touch site.tgz
deploy site.tgz
deploy other.tgz
```

<!-- output -->
```
checking site.tgz
deploying site.tgz
done with site.tgz
checking other.tgz
other.tgz is missing (status 1), skipped
done with other.tgz
```

The first time, every step works, so `except` is skipped and `finally` runs. The second time, the
test fails: `deploying other.tgz` is skipped, `except` prints its message, and `finally` still runs.
Both calls finish, which `set -e` alone wouldn't allow.

Here are the rules:

- The first command in the `try` block that fails skips the rest of the block and runs `except`.
- `except as NAME` puts the failing status in NAME, and `$?` holds it too. Plain `except` works
  when you don't need the number.
- `finally` always runs last, also after `return`, `break`, `continue` or `exit` leaves the block.
  That makes it a good place to delete a lock file or a temporary folder.
- You need `except`, `finally` or both, in that order, and each goes after the `}` before it.
- Inside a bash function the lines in the blocks stay bash. `try` works at the prompt and at the
  top of a file too, not only in a function.
- A function you call in the block counts as one command. If it returns a failing status,
  `except` runs.

As with `set -e`, a test doesn't count as a failure: an `if` condition, a command before `&&` or
`||`, or one after `!`.

### try in Python style

In the Python style you write `try:`, `except:` and `finally:`, each with an indented body. It's
the same thing, written the way the rest of a `def` is written. Here a check reports what it
found, fails when the file is missing, and always says it's done:

<!-- run -->
```
def check(name):
    try:
        [ -f "$name" ]
        echo "$name is there"
    except as rc:
        echo "$name is missing (status $rc)"
        return 1
    finally:
        echo "checked $name"

touch a.txt
check a.txt
check b.txt
echo "status: $?"
```

<!-- output -->
```
a.txt is there
checked a.txt
b.txt is missing (status 1)
checked b.txt
status: 1
```

Notice that `finally:` runs even though `except:` ended with `return 1`, and the function still
fails with that status. A Python line that goes wrong counts as a failure too, such as a condition
with an unknown name. A short body fits after the colon: `try: grep -q ERROR app.log`. There's
more on this form in [Handle errors with try](python-style.md#handle-errors-with-try).

### try, set -e and the ERR trap

A failure that `except` handles is dealt with. It doesn't run the `ERR` trap and doesn't stop a
`set -e` script:

<!-- run -->
```
set -e
trap 'echo "the ERR trap ran"' ERR
try {
  false
} except {
  echo "handled it"
}
echo "still running"
```

<!-- output -->
```
handled it
still running
```

With no `except`, the failure goes on after `finally`, as if there were no `try`. So with
`set -e`, the cleanup runs and then the script stops:

<!-- run -->
```
set -e
try {
  false
} finally {
  echo bye
}
echo "this line doesn't run"
```

<!-- output -->
```
bye
✗ example.sc:2: try { false ; } finally { echo bye ; } (set -e: the rest of the file was skipped)
```

### If try doesn't work

A `try` with neither `except` nor `finally` is refused before it runs anything:

```
try { echo "working" }        # try: missing 'except' or 'finally'
```

There are no error types like in Python, because a command only gives back a number. So
`except ValueError:` stops with
`except ValueError: there are no error types here; use 'except:', or 'except as status:' for the failing status`.
To act on one kind of failure, look at the number: `except as rc:`, then `if int(rc) == 2:`. The
status arrives as text, like a value set the bash way, so `int()` makes it a number to compare.

## Which one to use

They all deal with failure, but each fits a different job.

For one command that might fail, where you know what to do instead, use `||` or `if !`. It's the
smallest tool and says exactly what happens on that line.

For a script where every step needs the one before it, such as building and then copying, put
`set -e` at the top. Then you don't write a check for each line.

For a few steps you want to handle together before carrying on, use `try` with `except`. This is
the one for a loop over files or servers, where one bad item shouldn't end the whole run.

For something that must happen however things end, such as deleting a temporary file, use a
`trap` on `EXIT` or a `finally`. `finally` suits a few steps in the middle of a function. The trap
suits a whole function, file or func that should clean up after itself.

They work together. A common start for a script that matters is `set -euo pipefail`, an `EXIT`
trap for cleanup, and a `try` around the one part that's allowed to fail.

## Find out what a script does

When a script doesn't do what you expected, the quickest way to see why is to watch it run.
`set -x` prints each command to the error stream, starting with `+`, after its variables are
filled in and before it runs:

<!-- run -->
```
set -x
name=world
echo "hello $name"
set +x
echo "no trace"
```

<!-- output -->
```
+ name=world
+ echo 'hello world'
hello world
+ set +x
no trace
```

Because you see the command with the values in it, you can tell at once when a variable was
empty or had a value you didn't expect. It follows the script into functions and loops too:

<!-- run -->
```
set -x
greet() { echo "hi $1"; }
greet Ada
total=$(( 2 + 3 ))
for f in a b; do echo $f; done
set +x
```

<!-- output -->
```
+ greet Ada
+ echo 'hi Ada'
hi Ada
+ total=5
+ echo a
a
+ echo b
b
+ set +x
```

Put `set -x` right before the part you're curious about and `set +x` after it, so the trace stays
short enough to read.

Other things that help:

- `echo "status: $?"` right after a command shows how it ended.
- `set -o` shows which options are on, in case an rc file turned one on for you.
- `trap` on its own shows the traps that will run.
- `source -n FILE` checks a whole file without running it: every line that won't parse and every
  command name nothing answers to, with line numbers. See
  [Check a script before you run it](README.md#check-a-script-before-you-run-it).
