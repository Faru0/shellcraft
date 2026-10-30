# Getting started

← [Manual](README.md)

This page is for you if you've never used ShellCraft. It's one path from start to finish: you'll
install it, start it, make a small practice folder, and use it to try the things you'll do every
day. That means running commands, piping them together, saving results to a file, making a
shortcut, finding help, and changing the colors. Every step is something to type, followed by what
you'll see. Plan on about fifteen minutes.

You don't need to know bash. If you do, most of it will feel familiar.

## 1. Install it

You need Python 3.11 or newer. Check with `python3 --version`.

Get the code and install what it needs into a virtual environment. A virtual environment is a
private folder of Python packages, so ShellCraft's packages don't mix with the rest of your system:

```bash
git clone https://github.com/Faru0/shellcraft.git
cd shellcraft
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On Windows, use `py -m venv .venv` and activate it with `.venv\Scripts\Activate.ps1`. If PowerShell
refuses to run that script, run `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser` once and try
again.

To get a `shellcraft` command that works from any folder, also run `pip install .`. If you plan to
change ShellCraft's own code, use `pip install -e .` instead, so your edits take effect without
installing again.

## 2. Start it

From the `shellcraft` folder:

```bash
python main.py
```

If you ran `pip install .`, typing `shellcraft` anywhere does the same.

You'll see the banner and a loading bar for a moment, then the prompt:

```
╭─ ⚡shellcraft ─ you@laptop ─ ~/shellcraft ─ ⎇ main
╰─❯
```

The top line tells you where you are: your user and machine, the folder you're in, and the git
branch when the folder is a git repository. You type on the second line, after `❯`.

The first time it starts, ShellCraft creates a `~/.shellcraft` folder with a few starter files in
it. Nothing in them runs until you change them. [Your ~/.shellcraft folder](your-shellcraft-folder.md)
explains each one, but you don't need it yet.

To leave at any time, type `exit` or press Ctrl-D on an empty line. Ctrl-C never closes the shell.
It only clears the line, or stops a command that's running.

## 3. Make a practice folder

ShellCraft has its own versions of the commands you know from Linux, written in Python, so they
work the same on Windows, macOS and Linux. Use a few of them to make a folder to play in:

```
cd ~
mkdir practice
cd practice
pwd
```

`cd ~` goes to your home folder, `mkdir` makes a folder, and `cd practice` goes into it. `pwd`
prints where you are now, so you'll see the full path of the new folder, ending in `/practice`.

## 4. Make a file to work with

Most of what you do in a shell is reading and filtering text. So you need some text. This line
writes a small, fake log file:

```
printf 'INFO start\nERROR disk full\nINFO retry\nERROR disk full\nERROR timeout\n' > app.log
```

`printf` prints its text, and each `\n` in it starts a new line. The `>` at the end sends that
output into a file called `app.log` instead of the screen. Nothing appears, which is normal: a
command that succeeds often prints nothing.

Check that it worked:

```
ls
cat app.log
```

`ls` lists the folder and shows `app.log`. `cat` prints the file:

```
INFO start
ERROR disk full
INFO retry
ERROR disk full
ERROR timeout
```

## 5. Find lines with grep

`grep` keeps the lines that contain a word and drops the rest. To see only the errors:

```
grep ERROR app.log
```

```
ERROR disk full
ERROR disk full
ERROR timeout
```

## 6. Pipe commands together

A pipe, written `|`, sends the output of one command into the next one as its input. It's how
you build an answer out of small steps. Count the error lines:

```
grep ERROR app.log | wc -l
```

```
3
```

`grep` finds the three lines and hands them to `wc -l`, which counts lines. Now group them by
message, to see which error happens most:

```
grep ERROR app.log | sort | uniq -c
```

```
      2 ERROR disk full
      1 ERROR timeout
```

Read it left to right. `grep` keeps the errors, `sort` puts identical lines next to each other,
and `uniq -c` collapses each run of identical lines into one, with a count in front. `uniq` only
merges lines that are next to each other, which is why `sort` comes first.

## 7. Save results to a file

You've already used `>` once. It writes a command's output to a file, replacing whatever was in
it. `>>` adds to the end instead:

```
grep ERROR app.log > errors.txt
echo 'INFO done' >> app.log
tail -n 2 app.log
```

`errors.txt` now holds the three error lines. The `echo` line added one more line to the end of
`app.log`, and `tail -n 2` shows the last two lines, so you'll see:

```
ERROR timeout
INFO done
```

Colored output on screen turns into plain text when you pipe it or write it to a file, so
`ls | wc -l` and `grep -n TODO *.py > todo.txt` give clean results.

## 8. Let Tab and your history do the typing

Type `gr` and press **Tab**. ShellCraft completes it to `grep`. Tab also completes switches, with a
short description of each, and file names. Try it after a dash:

```
grep -<Tab>
```

A menu opens listing grep's switches. Press Backspace to remove the dash, or Ctrl-C to drop the
line.

Now start typing `grep ERR`. A grey suggestion from your history appears after the cursor. Press
**→** to take it. Press **Ctrl-R** and type `uniq` to search everything you've typed before, and
press Enter to run what it finds. ↑ and ↓ step through earlier commands one at a time.

## 9. Make a shortcut

If you type `grep ERROR` a lot, give it a short name. That's an alias:

```
alias errs='grep ERROR'
errs app.log
```

The first line answers with `✓ alias errs → grep ERROR` and where it saved it. The second runs
`grep ERROR app.log`, because whatever you type after an alias is added to its end. The quotes
around `grep ERROR` keep the two words together as one value.

Aliases are saved, so `errs` still works next time you start ShellCraft.
[Aliases and history](aliases-and-history.md) has more.

## 10. Find help

You never need to remember a command's switches. Ask:

```
help grep
```

```
grep: Print lines matching a pattern
usage: grep [-i -v -c -n -F -l -L -q -w -x -o -r -h -H] [-m N] [-A N] [-B N] [-C N] [-e PAT]... PATTERN [FILE...]
```

In a usage line, anything in `[ ]` is optional, and `...` means you can give more than one.
For the full story, with examples:

```
man grep
```

`man` opens a viewer you can scroll with the arrow keys or the mouse. Press `/` to search and `q`
to close it. Two more you'll use:

```
help          # every command, grouped by what it's for
hotkeys       # every keyboard shortcut
```

## 11. Change the colors

```
theme
```

You'll see every theme with a preview of its colors. Pick one:

```
theme nord
```

```
✓ theme set to Nord
```

The prompt changes color at once, and the choice is saved for next time. `theme reset` goes back
to the default. While you're here, `settings` lists every on/off setting with what it does.
[Settings and themes](settings-and-themes.md) explains them.

## 12. Try a module

Modules are extra commands added on top of the built in ones. This one looks up where an IP
address is. It needs an internet connection:

```
ip2geo 8.8.8.8
```

You'll see one row per fact: the country, the city, the ISP, and whether the address belongs to a
proxy or a hosting provider. `modules` lists every module, and `man ip2geo` explains this one.

## 13. Open another program

`sub` runs another program while keeping ShellCraft open. Its name appears in the footer at the
bottom of the screen. For example, on Linux or macOS with `htop` installed:

```
sub htop
```

Press **Ctrl-]** to come back to the prompt while it keeps running, and **Ctrl-] 1** to go back
to it. [Subshells](subshells.md) has the rest.

## 14. Clean up

When you're done practicing, go home and delete the folder:

```
cd ~
rm -r practice
```

`rm` refuses to delete a filesystem root, your home folder, or the folder you're in, which is why
you `cd ~` first.

## What's built in

You've used about fifteen commands. Here are all of them, grouped the way `help` groups them.
Each one has a manual: `man ls`, `man grep`.

| Group | Commands |
| --- | --- |
| Text | `echo` `printf` `cat` `grep` `head` `tail` `wc` `sort` `uniq` `cut` `tr` `tee` `diff` `seq` `yes` `jq` `less` `more` |
| Files | `ls` `find` `tree` `du` `touch` `mkdir` `cp` `mv` `rm` `basename` `dirname` `realpath` `stat` |
| System | `date` `which` `type` `command` `env` `whoami` `hostname` `uname` `curl` |
| Shell | `cd` `pwd` `exit` `clear` `help` `man` `hotkeys` `theme` `prompt` `settings` `alias` `history` `modules` `reload` `startup` `profile` `func` `source` `:` `export` `read` `sleep` `time` `xargs` `test` |
| Scripting | `for` `while` `until` `if` `case` `select` `function` `set` `trap` `getopts` |
| Subshells and alerts | `sub` `jobs` `alert` `clip` `record` `script` |
| AI and MCP | `mcp` `ai` `watch` `tasks` `findings` `mark` `tell` `review` `ingest` |

And the modules that come with ShellCraft:

| Module | What it does |
| --- | --- |
| `myip` | Your public IP, or where any IP is. `-p PORT` checks whether a port is open |
| `ip2geo` | Where an IP or domain is: country, city, ISP, and whether it's a proxy or a hosting provider |
| `queryDns` | A domain's DNS records with each IP's owner (DnsDumpster, needs a free key) |
| `queryCert` | Certificates and subdomains from Certificate Transparency logs |
| `queryCensys` | Open ports and software on a host (Censys, needs a token) |

## If a step didn't work

- **`command not found: git`** or another program you have installed. Out of the box ShellCraft
  only runs its own commands. Run `settings system_commands on` to let it run your system's
  programs too.
- **Nothing happened after `printf ... > app.log`.** That's right. The output went into the file.
  `cat app.log` shows it.
- **`unterminated ' quote`.** A quote was opened and never closed. Check that every `'` has a
  partner.

[Troubleshooting](troubleshooting.md) has the rest.

## Where to go next

- Run commands every time the shell starts: [Startup](startup.md)
- Write a script with questions, loops and choices: [Scripting guide](scripting/README.md)
- Write your own command in Python: [Your first module](modules/first-module.md)
- Copy ready-made setups: [Cheat sheet](cheatsheet.md)
