# Startup: the rc file and startup files

← [Manual](README.md)

This page is for you if you want ShellCraft to be set up the way you like every time it opens: your
theme, your project folder, your API URL, your own functions. After reading it you'll know which
file to put each thing in, in what order they run, what happens when a line in one of them fails,
and how to check them after an edit.

It works the way bash runs `~/.bashrc` and then the files in `~/.bashrc.d/`. Every file holds the
same thing: commands you could type at the prompt.

## What runs, in order

When the interactive shell starts, it runs these before the first prompt:

1. `~/.shellcraft/rc`: your main setup. ShellCraft commands, one per line.
2. `~/.shellcraft/startup/*.sc`: more files of the same kind, one per job, in name order.
3. The active [profile](profiles.md)'s own `rc`, if you started in one.
4. That profile's own `startup/*.sc`, in name order.

They all run in your shell, one after another, so whatever one file sets, the next file and your
prompt can use. A variable from the rc file is there in `20-go.sc`, and a folder a startup file
`cd`s into is where your first prompt opens.

None of this runs for `shellcraft --mcp`, or in a `sub shellcraft` subshell. It does run before
[`shellcraft func run`](funcs.md#run-a-func-from-outside-shellcraft), so a func sees the same setup
you do.

To skip it for one start, use `shellcraft --no-startup`. To turn it off for good,
`settings startup off`.

## Which file should I use?

- Put a line in **the rc file** when it's short and you want it every time: a theme, an `env set`,
  a small function. Most people only ever need this one.
- Move things into **startup files** once the rc file gets long, or for a job that's easier to keep
  apart: connecting a VPN, asking which project you're on, starting a log watcher. One job per
  file means you can switch one off by renaming it.
- Use **a profile's rc** for setup that belongs to one client or project only, like that project's
  API key or folder. It runs only in that profile. See [Profiles](profiles.md).

## The rc file

ShellCraft created `~/.shellcraft/rc` for you, full of commented examples. Open it in your editor
and remove the `#` in front of the lines you want. Each line runs as if you typed it:

```
theme nord
cd ~/projects
env set PROJECT_ROOT=~/projects
alert -l info "shell ready"
```

Lines starting with `#` are comments, and so is a ` # note` after a command (see
[Comments](scripting.md#comments)). Anything you can type works, including loops, `if` blocks and
functions over several lines:

```
backup() {
  cp -r "$1" "$1.bak" && echo "saved $1.bak"
}

for host in web1 web2; do
  echo "checking $host"
done
```

The `backup` function is defined each time the shell starts, so `backup notes.txt` works at every
prompt. That's how you keep a function: a function you type at the prompt is gone when you leave.

To run the rc file again after you change it, without restarting:

```
source ~/.shellcraft/rc
```

The [scripting guide](scripting/README.md) shows everything you can write here: variables,
questions with `input()` and `read`, `if` and `case`, loops, menus and functions. Its
[complete examples](scripting/examples.md) include whole rc and startup files to copy.

### Check it after an edit

A typo in the rc file shows up every time you open the shell. To check the file without running
anything, for example after a big edit:

```
source -n ~/.shellcraft/rc
```

It lists every line that won't parse and every command name nothing answers to, with the line
number:

```
rc:2: missing 'done': the block that starts here never ends
rc:3: warning: unknown command 'querDns' (did you mean 'queryDns'?)
2 problems (1 error, 1 warning)
```

A clean file says something like `ok: 24 lines, no problems`. `source -n` works on a startup file
too (`source -n ~/.shellcraft/startup/10-project.sc`).

### When a line fails

If a line fails, ShellCraft tells you which one and runs the rest. Say line 2 of your rc file is
`cat nosuch.txt`. When the shell starts, you see:

```
✗ rc:2: cat nosuch.txt
shellcraft: cat: nosuch.txt: no such file
```

and the lines after it still run. Carrying on is the safe default here: one broken line shouldn't
cost you your theme and your functions.

One kind of mistake stops the whole file: wrong indentation in a Python-style block. Like Python,
ShellCraft won't run a file it can't read, so none of the rc file runs and you see
`✗ rc:4: …` with `unindent does not match any outer indentation level`. Fix that line, or find it
first with `source -n`.

### Stop at the first error with set -e

Sometimes later lines only make sense if earlier ones worked. If `cd ~/work/acme` fails, you don't
want the next line deleting files in whatever folder you're in. Put `set -e` at the top to stop at
the first line that fails:

```
set -e
cd ~/work/acme
rm -f *.tmp
set +e
```

The failing line is reported with `(set -e: the rest of the file was skipped)` after it. The shell
still starts.

The option stays on afterwards, in the startup files that run next and at the prompt too. That's
why the example ends with `set +e`: make it the last line if you only want it for this file.
`set -u` (an unset `$name` is an error) stops the file the same way. See
[Stop on errors](scripting/errors.md#stop-on-errors-with-set--e).

### Clean up when you leave with trap EXIT

A `trap '…' EXIT` in the rc file or a startup file runs when you leave ShellCraft, with `exit` or
Ctrl-D. Use it to tidy up after the session:

```
trap 'rm -rf ~/.cache/myscratch; echo "bye"' EXIT
```

It also runs when `shellcraft func run` finishes, since that's a session too.

Traps set here belong to the session, the same as ones you type at the prompt, so an `ERR` or
`INT` trap here applies at the prompt too. A file you run with `source` has its own traps, which
run when that file ends. See [Clean up with trap](scripting/errors.md#clean-up-with-trap).

### exit in the rc file

`exit` in the rc file ends ShellCraft right there, with that status (`exit 1`). It's rarely what you
want at the prompt, but it's handy with `shellcraft func run` from a script, when a missing setting
should stop everything:

```
[ -n "$PROJECT_ROOT" ] || exit 1
```

If ShellCraft closes the moment it opens, look for an `exit` in your rc file or a startup file.
Start with `shellcraft --no-startup` to get a prompt and fix it.

## Startup files

Once your rc file grows, split it into a folder of small files: `~/.shellcraft/startup/`. Each
file ends in `.sc` and does one job. They run right after the rc file, in name order, so number
them:

```
~/.shellcraft/startup/10-vpn.sc
~/.shellcraft/startup/20-project.sc
~/.shellcraft/startup/30-reminders.sc
```

Leaving gaps between the numbers lets you add a file in between later without renaming the rest.

A startup file is written exactly like the rc file. Here's a pair that work together:

```
# 10-project.sc
project = input("Which project today? ")
env set PROJECT_ROOT=~/work/$project

# 20-go.sc
cd $PROJECT_ROOT
echo "working on $project"
```

`10-project.sc` asks you a question when the shell starts and keeps the answer in `$project`. It
then sets `PROJECT_ROOT` as an environment variable, so modules and programs can see it too.
`20-go.sc` can use `$project` and `$PROJECT_ROOT` because `10-project.sc` ran first, in the same
shell. When the prompt appears, you're in that folder with both set.

To start, copy the example that came with the folder:

```bash
cd ~/.shellcraft/startup
cp 10-example.sc.sample 10-project.sc
```

Only files ending in `.sc` run, so the `.sample` file itself is never run.

### Helper files

A file whose name starts with an underscore, like `_common.sc`, never runs on its own. Use one for
functions or settings that several startup files share, and pull it into the files that need it:

```
source ~/.shellcraft/startup/_common.sc
```

### When a file fails

It works as in the rc file. A line that fails is reported with its file and line number, such as
`✗ 20-go.sc:1: …`, and the rest still run. With `set -e` in the file, or left on by the rc file,
the first failing line skips the rest of that file. The next file still runs.

Ctrl-C skips the rest of the file that's running and moves on to the next. That's your way out of
a startup file that hangs, for example on a VPN that won't connect.

### Something that keeps running

A startup file runs once and finishes. To keep a watcher, a tail or a tunnel running, start it in
the background from a file with `sub`. It shows in the footer, and Ctrl-] and its number switch
to it:

```
sub tail -f /var/log/syslog
```

Without `sub`, the shell would wait for `tail -f` to finish, and your prompt would never appear.

### Manage them from the prompt

```
startup                  # every startup file, with how it went the last time it ran
startup run              # run them all again now
startup run 20-go        # only that one
startup --folder         # where they live
```

`startup` is the place to look when the shell started with warnings and they scrolled away.

## A profile's own

A profile can have its own rc file and its own startup folder,
`~/.shellcraft/profiles/NAME/startup/`. Its `.sc` files run after that profile's rc, both when you
start in the profile and when you switch to it with `profile use NAME`. They run after your main rc
and startup files, so a profile can change what those set up. `profile export`, `import`, `copy`
and `save` carry them along. See [Profiles](profiles.md).

## Python startup scripts

Older versions ran Python files (`.py`) from this folder. They aren't run any more, and `startup`
lists any it finds. Move their commands into a `.sc` file. If you still need the Python, start it
from a `.sc` file: `sub python ~/.shellcraft/startup/old-script.py`.

`man startup` inside ShellCraft has every detail.
