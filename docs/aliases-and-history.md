# Aliases and history

← [Manual](README.md)

This page is about typing less. An alias is a short name for a command you type often. Your
history is every command you've typed, so you can find and run them again. After reading it you'll
be able to make aliases from the prompt or in a file, find an old command in a few keystrokes, and
keep passwords out of your history. Both live in plain text files in `~/.shellcraft` that you can
edit in any editor.

## Aliases

### Add one from the prompt

Reach for an alias when you notice you keep typing the same command with the same switches. For
example, a long listing:

```
alias ll='ls -l'
ll
```

The first line answers `✓ alias ll → ls -l`, with the file it saved it in. From then on, `ll` runs
`ls -l`, in this session and every later one.

Get in the habit of putting single quotes around the value. ShellCraft joins `alias ll=ls -l`
for you, but without quotes the line is read like any other command first. So a `$` in it is
replaced straight away. `alias here=echo $PWD` saves the folder you're in right now, while
`alias here='echo $PWD'` saves `$PWD` itself, which is looked up each time you run `here`. Single
quotes keep the text exactly as you typed it.

Anything you type after an alias is added to the end, so `ll ~/projects` runs `ls -l ~/projects`.
Aliases work in every part of a pipeline, so `cat app.log | errs` works when `errs` is an alias.

These commands look after them:

```
alias                 # list them
alias ll              # show one
unalias ll            # remove one
unalias -a            # remove them all
\ls                   # a backslash skips the alias, only this once
which ll              # says whether a name is an alias, a command or a module
```

`which ll` answers `ll: aliased to 'ls -l'`, which is handy when a command does something you
didn't expect.

### Change a command's defaults

An alias can have the same name as the command it runs. Then it changes what that command does by
default:

```
alias ls='ls -a'
```

Now `ls` always shows hidden files too. When you want the plain command for a moment, put a
backslash in front: `\ls`.

### The aliases file

Every alias lives in `~/.shellcraft/aliases`, one per line, written exactly like bash's
`~/.bash_aliases`. If you have a bash aliases file already, most of it works as it is. Editing the
file is the quickest way to add several at once:

```
# my shortcuts
alias ll='ls -l'
alias la='ls -a -l'
alias errors='grep -i error'
alias today='date +%Y-%m-%d'
alias api='curl -s -H "Accept: application/json"'
```

The last one shows how to put quotes inside an alias: the outside quotes are single, so the double
quotes inside are part of the value.

Lines starting with `#` are comments, and so is a ` # note` at the end of a line (see
[Comments](scripting.md#comments)). Edit it in any editor. The change applies to the next command
you type, with no restart. `alias` and `unalias` at the prompt edit the same file and leave your
comments alone. `alias --file` prints its path.

### What an alias can't do

An alias stands for one command. It can't hold a pipe or a redirect, because then it wouldn't be
clear what the words you type after it should be added to. Trying gives:

```
alias x: an alias must be a single command (no '|', '>' or '>>')
```

For a pipe, use a [function](scripting/functions.md) or a [func](funcs.md) instead.

A line in the file that ShellCraft can't use, such as one with a `|` in it or an unclosed quote, is
skipped. `alias` lists it with its line number so you can fix it.

### Aliases in a profile

While a [profile](profiles.md) is active, `alias` and `unalias` change that profile's own file,
`~/.shellcraft/profiles/NAME/aliases`. Its aliases are added on top of your main ones, and win when
both have the same name. Use `alias -g` to change your main aliases from inside a profile.

AI clients never see your aliases. A command they run means exactly what it says, so an alias of
yours can't change what an AI thinks it's running.

## History

Everything you type is saved to `~/.shellcraft/history`, and you get it back in every session.
The fastest way to rerun something is usually not to retype it.

### Find an old command

There are three ways, depending on how well you remember it:

- If it was recent, press ↑ until you reach it. ↓ goes back down.
- If you remember a piece of it, press **Ctrl-R** and type that piece. The newest match appears.
  Press Ctrl-R again for older ones, and Enter to run it.
- If you're typing it again anyway, the rest appears in grey after the cursor as you type. Press
  **→** to take it.

### Run it again with `!`

Bash's `!` shortcuts work too. They're expanded before the line runs:

```
!!          # the last command again
sudo !!     # the last command, with sudo in front
!42         # command 42 from the history list
!git        # the last command that started with git
cat !$      # !$ is the last word of the previous command
```

`!$` saves typing in a common pattern: you `ls` a long path, then want to `cat` the same path.

### All the history commands

| Do this | To |
| --- | --- |
| ↑ and ↓ | Step through earlier commands |
| Ctrl-R, then type | Search everything you've typed |
| → | Take the grey suggestion after the cursor |
| `history` | List it, numbered |
| `history 20` | Only the last 20 |
| `history \| grep ssh` | Find an old command |
| `!!` | Run the last command again (`sudo !!` works too) |
| `!42` | Run command 42 from `history` |
| `!git` | Run the last command that started with `git` |
| `!$` | The last word of the previous command: `cat !$` |
| `history -c` | Clear it |

### Keep something out of history

History is a plain file on disk, so anything you type ends up there. For passwords and keys,
that's a problem. There are two ways around it:

- Start the line with a space. It runs normally but is never saved.
- `settings NAME VALUE` and `env set -s NAME VALUE` are saved with the value replaced by `••••`.
  Better still, type `settings NAME` and enter the value when it asks: it's never shown or saved.

### The history file

It's a text file. Each command has a timestamp line, then the command with a `+` in front:

```
# 2026-10-20 22:27:27.192524
+git status

# 2026-10-20 22:27:35.836777
+man hotkeys
```

You can edit or trim it in any editor while ShellCraft isn't running.

### Separate history per profile

Each [profile](profiles.md) can keep its own history in `~/.shellcraft/profiles/NAME/history`, so a
name you typed in one project never shows up as a suggestion in another. That's the default for a
new profile. To share the main history instead:

```
profile set NAME history shared
```
