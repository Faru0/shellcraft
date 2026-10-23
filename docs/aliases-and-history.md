# Aliases and history

← [Manual](README.md)

An alias is a short name for a command you type often. Your history is every command you've typed,
so you can find and run them again. Both are plain text files in `~/.shellcraft` that you can edit
in any editor.

## Aliases

### Add one from the prompt

```
alias ll='ls -l'
ll
```

`ll` now runs `ls -l`. Anything you type after an alias is added to the end, so `ll ~/projects`
runs `ls -l ~/projects`. Aliases work in every part of a pipeline.

```
alias                 # list them
alias ll              # show one
unalias ll            # remove one
unalias -a            # remove them all
\ls                   # a backslash skips the alias, just this once
which ll              # says whether a name is an alias, a command or a module
```

An alias can also change what a command does by default:

```
alias ls='ls -a'
```

### The aliases file

Every alias lives in `~/.shellcraft/aliases`, one per line, written exactly like bash's
`~/.bash_aliases`:

```
# my shortcuts
alias ll='ls -l'
alias la='ls -a -l'
alias errors='grep -i error'
alias today='date +%Y-%m-%d'
alias api='curl -s -H "Accept: application/json"'
```

Edit it in any editor. The change applies to the next command you type, with no restart. `alias`
and `unalias` at the prompt edit the same file and leave your comments alone. `alias --file`
prints its path.

A line ShellCraft can't use, such as one with a `|` in it or an unclosed quote, is skipped. `alias`
lists it with its line number so you can fix it. An alias can't hold a pipe: for that, use a
[function](scripting.md#functions) or a [func](funcs.md).

### Aliases in a profile

While a [profile](profiles.md) is active, `alias` and `unalias` change that profile's own file,
`~/.shellcraft/profiles/NAME/aliases`. Its aliases are added on top of your main ones, and win when
both have the same name. Use `alias -g` to change your main aliases from inside a profile.

AI clients never see your aliases: a command they run means exactly what it says.

## History

Everything you type is saved to `~/.shellcraft/history`, and you get it back in every session:

| Do this | To |
| --- | --- |
| ↑ and ↓ | Step through earlier commands |
| Ctrl-R, then type | Search everything you've typed |
| → | Take the grey suggestion after the cursor |
| `history` | List it, numbered |
| `history 20` | Just the last 20 |
| `history \| grep ssh` | Find an old command |
| `!!` | Run the last command again (`sudo !!` works too) |
| `!42` | Run command 42 from `history` |
| `!git` | Run the last command that started with `git` |
| `!$` | The last word of the previous command: `cat !$` |
| `history -c` | Clear it |

### Keep something out of history

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
