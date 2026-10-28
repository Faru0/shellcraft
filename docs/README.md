# The ShellCraft manual

← [Back to the README](../README.md)

This is the full manual for ShellCraft. It's written for three kinds of reader: someone opening
the shell for the first time, someone who wants to turn a Python script into a command, and
someone who already uses it every day and wants to tune it. Find yourself below and follow the
links in order, or jump straight to the page you need. Every page stands on its own.

You don't have to leave the shell to get help. `help` lists every command, `man NAME` opens the
manual for one, and `hotkeys` prints the keyboard shortcuts.

## Where to start

Pick the line that sounds most like what you're trying to do.

| You want to | Read |
| --- | --- |
| Try ShellCraft for the first time | [Getting started](getting-started.md) |
| Know where your settings, aliases and history live | [Your ~/.shellcraft folder](your-shellcraft-folder.md) |
| Write an rc file, a startup file or a func with loops, menus and questions | [Scripting guide](scripting/README.md) |
| Make your own command out of a Python script | [Your first module](modules/first-module.md) |
| Turn a script you already have into a command | [Ingest a script](modules/ingest.md) |
| Copy and paste the common setups without reading much | [Cheat sheet](cheatsheet.md) |
| Fix something that isn't working | [Troubleshooting](troubleshooting.md) |

## If you're new

Start with [Getting started](getting-started.md). It's one path from installing ShellCraft to
running your first pipe, and each step shows what to type and what you'll see. It takes about
fifteen minutes.

After that, these three pages cover what most people want next:

1. [Aliases and history](aliases-and-history.md), so you can give long commands short names and
   find the ones you typed yesterday.
2. [Keyboard and mouse](keyboard-and-mouse.md), for Tab completion, the history search and the
   viewer that `man` opens.
3. [Settings and themes](settings-and-themes.md), to change the colors, the prompt, and whether
   ShellCraft may run programs like `git` from your system.

When something goes wrong, [Troubleshooting](troubleshooting.md) is organised by the message you
see on the screen.

## If you want to write your own commands

A module is a Python file that becomes a ShellCraft command. It works in pipes, has a manual page,
completes with Tab, and AI clients can call it. You don't need to know anything about ShellCraft's
insides to write one.

- [Your first module](modules/first-module.md): from a three-line file to a small web API client, step by step. Start here.
- [Ingest a script](modules/ingest.md): a wizard that converts a script you already have.
- [Add modules you already have](modules/adding-modules.md): drop the files in, several at once.
- [Module reference](modules/reference.md): every rule, file and option in one place, for when you know what you're looking for.

## If you use it every day

These pages are about making the shell yours. Everything lives in plain files in `~/.shellcraft`,
so you can edit them in any editor, keep them in git, or copy them to another machine. Each page
also names the command that does the same thing from the prompt.

- [Your ~/.shellcraft folder](your-shellcraft-folder.md): what every file and folder is for, and when ShellCraft reads it
- [Aliases and history](aliases-and-history.md): your shortcuts, and the commands you typed
- [Startup](startup.md): the `rc` file and the startup files that run when the shell starts
- [Funcs](funcs.md): named lists of commands you run when you need them
- [Profiles](profiles.md): separate setups for different kinds of work, switched in one command
- [Settings and themes](settings-and-themes.md): every setting, API keys, colors and the prompt
- [Subshells, alerts and the clip](subshells.md): running programs side by side, the footer, alerts
- [MCP and the AI co-pilot](mcp-and-ai.md): letting AI clients use your shell, and what they're allowed to do

## Write scripts

Your rc file, startup files and funcs are scripts. If you've written a bash script before, most
of it carries over. If you haven't, the guide starts from the beginning and has an example for
everything.

- [Scripting guide](scripting/README.md): where scripts live, how to try them, and how ShellCraft differs from bash
- [Variables and input](scripting/variables-and-input.md): variables, quoting, defaults, arithmetic, asking the user
- [Printing text](scripting/printing.md): `echo`, several lines, `printf` columns, colors, files
- [Conditions and choices](scripting/conditions.md): `if`, tests, `&&` and `||`, `case`
- [Loops and menus](scripting/loops.md): `for`, `while`, reading a file, `break`, `select` menus
- [Functions](scripting/functions.md): arguments, defaults, `local`, `return`, giving back a value
- [Errors and cleanup](scripting/errors.md): `set -e`, `trap`, `try`
- [Complete examples](scripting/examples.md): rc files, startup files and funcs to copy
- [Bash style and Python style](scripting/python-style.md): every block written both ways, side by side

## Using the shell

- [Pipes and scripting](scripting.md): pipes, redirects, comments, environment variables, exit status, and a summary of the syntax
- [curl](curl.md): web requests, and pasting a browser's "Copy as cURL"
- [Keyboard and mouse](keyboard-and-mouse.md): shortcuts, completion, the viewer

## Reference

These are for looking things up, not for reading top to bottom.

- [Cheat sheet](cheatsheet.md): copy and paste blocks for everything on one page
- [Command line](command-line.md): `shellcraft` options and environment variables
- [Troubleshooting](troubleshooting.md): error messages and their fixes, a slow start, Windows, bug reports

## Elsewhere

| Document | For |
| --- | --- |
| [ai/README.md](../ai/README.md) | Agents and skills that teach an AI client to work in ShellCraft |
| [DEVELOPMENT.md](../DEVELOPMENT.md) | Working on ShellCraft itself |
| [CHANGELOG.md](../CHANGELOG.md) | What changed in each version |
