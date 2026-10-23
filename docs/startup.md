# Startup: the rc file and startup files

← [Manual](README.md)

When the interactive shell starts, it runs your setup before the first prompt, the way bash runs
`~/.bashrc` and then the files in `~/.bashrc.d/`:

1. **`~/.shellcraft/rc`**: your main setup. ShellCraft commands, one per line.
2. **`~/.shellcraft/startup/*.sc`**: more files of the same kind, one per job, in name order.
3. In a [profile](profiles.md), that profile's own `rc`, then its own `startup/*.sc`.

Every file holds the same thing: commands you could type at the prompt. They all run in your
shell, so whatever one file sets, the next file and your prompt can use.

None of this runs for `shellcraft --mcp`, or in a `sub shellcraft` subshell. To skip it for one
start, use `shellcraft --no-startup`. To turn it off for good, `settings startup off`.

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

To run it again without restarting:

```
source ~/.shellcraft/rc
```

If a line fails, ShellCraft tells you which one (`✗ rc:4: …`) and runs the rest.

## Startup files

Once your rc file grows, split it into a folder of small files: `~/.shellcraft/startup/`. Each
file ends in `.sc` and does one job. They run right after the rc file, in name order, so number
them:

```
~/.shellcraft/startup/10-vpn.sc
~/.shellcraft/startup/20-project.sc
~/.shellcraft/startup/30-reminders.sc
```

A startup file is written exactly like the rc file. Here's a pair that work together:

```
# 10-project.sc
project = input("Which project today? ")
env set PROJECT_ROOT=~/work/$project

# 20-go.sc
cd $PROJECT_ROOT
echo "working on $project"
```

`20-go.sc` can use `$project` and `$PROJECT_ROOT` because `10-project.sc` ran first, in the same
shell. When the prompt appears, you're in that folder with both set.

To start, copy the example that came with the folder:

```bash
cd ~/.shellcraft/startup
cp 10-example.sc.sample 10-project.sc
```

### Helper files

A file whose name starts with an underscore, like `_common.sc`, never runs on its own. Pull it into
the files that need it:

```
source ~/.shellcraft/startup/_common.sc
```

### When a file fails

A line that fails is reported with its file and line number, such as `✗ 20-go.sc:1: …`, and the
rest still run. Ctrl-C skips the rest of that file and moves on to the next.

### Something that keeps running

A startup file runs once and finishes. To keep a watcher, a tail or a tunnel running, start it in
the background from a file with `sub`. It shows in the footer, and Ctrl-] and its number switch
to it:

```
sub tail -f /var/log/syslog
```

### Manage them from the prompt

```
startup                  # every startup file, with how it went the last time it ran
startup run              # run them all again now
startup run 20-go        # just one
startup --folder         # where they live
```

## A profile's own

A profile can have its own startup folder, `~/.shellcraft/profiles/NAME/startup/`. Its `.sc` files
run after that profile's rc, both when you start in the profile and when you switch to it with
`profile use NAME`. `profile export`, `import`, `copy` and `save` carry them along.

## What runs, in order

1. `~/.shellcraft/rc`
2. `~/.shellcraft/startup/*.sc`, in name order
3. The active profile's `rc`, if you started in one
4. That profile's `startup/*.sc`, in name order

## Python startup scripts

Older versions ran Python files (`.py`) from this folder. They aren't run any more, and `startup`
lists any it finds. Move their commands into a `.sc` file. If you still need the Python, start it
from a `.sc` file: `sub python ~/.shellcraft/startup/old-script.py`.

`man startup` inside ShellCraft has every detail.
