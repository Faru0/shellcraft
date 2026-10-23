# Startup: the rc file and startup scripts

← [Manual](README.md)

Two things run every time the interactive shell starts, before your first prompt:

- **`~/.shellcraft/rc`**: ShellCraft commands, like `~/.bashrc` in bash. Use it for your theme, a
  starting folder, environment variables, functions, anything you'd type at the prompt.
- **`~/.shellcraft/startup/`**: Python scripts, for what a command can't do. Use them to check a VPN
  or a mount, work out a variable, or keep a watcher running in the background.

Neither runs for `shellcraft --mcp`, or in a ShellCraft subshell. To skip both for one start, use
`shellcraft --no-startup`. To turn them off for good, `settings startup off`.

## The rc file

ShellCraft created `~/.shellcraft/rc` for you, full of commented examples. Open it in your editor
and remove the `#` in front of the lines you want. Each line runs as if you typed it:

```
theme nord
cd ~/projects
env set PROJECT_ROOT=~/projects
alert -l info "shell ready"
```

Anything you can type works, including loops, `if` blocks and functions over several lines:

```
backup() {
  cp -r "$1" "$1.bak" && echo "saved $1.bak"
}

for host in web1 web2; do
  echo "checking $host"
done
```

To run it again without restarting:

```
source ~/.shellcraft/rc
```

If a line fails, ShellCraft tells you which one (`✗ rc:4: …`) and runs the rest.

### Ask a question when the shell starts

The rc file can ask you something and use the answer later:

```
name = input("What is your name? ")
echo "hello $name"

count = int(input("How many servers? "))      # asks again until it gets a whole number
read -s -p "API token: " token                # the bash way; -s hides what you type
```

### Secrets

`env set -s NAME` in the rc file asks for the value with hidden typing every time the shell
starts. The value is never written to a file or to your history:

```
env set -s GITHUB_TOKEN
```

### Split it into several files

As in bash, the rc file can pull in other files. A file that doesn't exist is skipped:

```
[ -f ~/.shellcraft/rc.work ] && source ~/.shellcraft/rc.work

for f in ~/.shellcraft/rc.d/*.rc; do
  [ -f "$f" ] && source "$f"
done
```

That way you can keep one file per project and switch one on by adding a line.

## Startup scripts

Every `.py` file in `~/.shellcraft/startup/` runs once at startup, in name order. Number them to
control the order: `10-env.py`, `20-vpn.py`. A file named `_name.py` is a helper the others can
import. What a script prints shows up under the banner.

The folder comes with two examples that don't run while their names end in `.sample`. To try one:

```bash
cd ~/.shellcraft/startup
cp 10-example.py.sample 10-example.py
```

### Set variables for the shell

A startup script runs as its own process, so changing `os.environ` inside it doesn't reach the
shell. Instead, write `NAME=value` lines to the file named in `$SHELLCRAFT_ENV_FILE`:

```python
import os

with open(os.environ["SHELLCRAFT_ENV_FILE"], "a") as f:
    f.write("PROJECT_ROOT=/srv/app\n")
    f.write("OLD_SETTING=\n")          # nothing after the = removes the variable
```

The shell, every subshell and every module then see `PROJECT_ROOT`. The variables apply only if
the script finishes without an error.

### When something goes wrong

A script that fails, or takes longer than 30 seconds, is reported in one line and the shell starts
anyway. To give a slow script more time, put this line at the top:

```python
# shellcraft: timeout=120
```

### Scripts that keep running

A watcher, a tunnel or a periodic check should start in the background so the shell never waits
for it. Put this line at the top of the script:

```python
# shellcraft: background, restart
```

It then runs as a subshell, shown in the footer as `startup: 30-watcher.py`. Press Ctrl-] and its
number to look at it. `restart` starts it again if it crashes, giving up after 5 crashes in 10
minutes. A background script can't set variables for the shell, because it starts after the shell
is ready. It can send alerts: the `alert` command is on its `PATH`.

`30-watcher.py.sample` in the startup folder is a complete example.

### Manage them from the prompt

```
startup                  # the scripts, in order, with their last result
startup run              # run them all again now
startup run 10-env.py    # just one
startup --folder         # where they live
startup stop NAME        # stop a background script
startup start NAME       # start it again
startup restart NAME
```

## What runs, in order

1. The startup scripts in `~/.shellcraft/startup/`, in name order
2. `~/.shellcraft/rc`, which sees the variables the scripts set
3. The active [profile](profiles.md)'s own `rc`, if you started in one
4. The background startup scripts, once the prompt is ready

`man startup` inside ShellCraft has every detail.
