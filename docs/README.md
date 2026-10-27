# The ShellCraft manual

← [Back to the README](../README.md)

This manual covers everything ShellCraft does, from your first command to writing your own. Every
page stands on its own, so jump to the one you need. Inside the shell, `man NAME` has the manual
for any command, and `help` lists them all.

## Where to start

| You want to | Read |
| --- | --- |
| Try ShellCraft for the first time | [Getting started](getting-started.md) |
| Know where your settings, aliases and history live | [Your ~/.shellcraft folder](your-shellcraft-folder.md) |
| Write an rc file, a startup file or a func with loops, menus and questions | [Scripting guide](scripting/README.md) |
| Make your own command out of a Python script | [Your first module](modules/first-module.md) |
| Turn a script you already have into a command | [Ingest a script](modules/ingest.md) |
| Copy and paste the common setups, no reading | [Cheat sheet](cheatsheet.md) |
| Fix something that isn't working | [Troubleshooting](troubleshooting.md) |

## Make it yours

These pages show how to customize ShellCraft by editing plain files in `~/.shellcraft`. Each one
also names the command that does the same from the prompt.

- [Your ~/.shellcraft folder](your-shellcraft-folder.md): what every file and folder is for, and when it's read
- [Aliases and history](aliases-and-history.md): your shortcuts, and the commands you typed
- [Startup](startup.md): the `rc` file and the startup files that run when the shell starts
- [Funcs](funcs.md): named lists of commands you run when you need them
- [Profiles](profiles.md): separate setups for different kinds of work
- [Settings and themes](settings-and-themes.md): every setting, API keys, colors and the prompt

## Write your own commands

A module is a Python file that becomes a ShellCraft command. It works in pipes, has a manual
page, completes with Tab, and AI clients can call it.

- [Your first module](modules/first-module.md): from a three-line file to a small web API client, step by step
- [Ingest a script](modules/ingest.md): the wizard that converts a script you already have
- [Add modules you already have](modules/adding-modules.md): drop the files in, several at once
- [Module reference](modules/reference.md): every rule, file and option in one place

## Write scripts

Your rc file, startup files and funcs are scripts. The scripting guide teaches you to write them,
with an example for everything.

- [Scripting guide](scripting/README.md): where scripts live, how to try them, and how ShellCraft differs from bash
- [Variables and input](scripting/variables-and-input.md): variables, quoting, defaults, arithmetic, asking the user
- [Printing text](scripting/printing.md): `echo`, several lines, `printf` columns, colors, files
- [Conditions and choices](scripting/conditions.md): `if`, tests, `&&` and `||`, `case`
- [Loops and menus](scripting/loops.md): `for`, `while`, reading a file, `break`, `select` menus
- [Functions](scripting/functions.md): arguments, defaults, `local`, `return`, giving back a value
- [Complete examples](scripting/examples.md): rc files, startup files and funcs to copy
- [Bash style and Python style](scripting/python-style.md): every block written both ways, side by side

## Using ShellCraft

- [Pipes and scripting](scripting.md): pipes, redirects, comments, environment variables, exit status, and a summary of the syntax
- [curl](curl.md): web requests, and pasting a browser's "Copy as cURL"
- [Subshells, alerts and the clip](subshells.md): running programs side by side, the footer, alerts
- [Keyboard and mouse](keyboard-and-mouse.md): shortcuts, completion, the viewer
- [MCP and the AI co-pilot](mcp-and-ai.md): letting AI clients use your shell

## Reference

- [Cheat sheet](cheatsheet.md): copy and paste blocks for everything on one page
- [Command line](command-line.md): `shellcraft` options and environment variables
- [Troubleshooting](troubleshooting.md): a slow start, Windows, a module that won't load, bug reports

## Elsewhere

| Document | For |
| --- | --- |
| [ai/README.md](../ai/README.md) | Agents and skills that teach an AI client to work in ShellCraft |
| [DEVELOPMENT.md](../DEVELOPMENT.md) | Working on ShellCraft itself |
| [CHANGELOG.md](../CHANGELOG.md) | What changed in each version |
