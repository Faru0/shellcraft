# Troubleshooting

← [Manual](README.md)

Common problems and how to fix them. If yours isn't here, the [bug report](#reporting-a-bug)
section says what to send.

## "command not found" for a program I have

ShellCraft runs only its own commands and modules until you allow your system's programs:

```
settings system_commands on
```

`which -a NAME` shows everything a name could run. See
[Settings and themes](settings-and-themes.md#your-systems-programs).

## My module doesn't show up

1. Check where it is. Your modules belong in `~/.shellcraft/modules/`, with the file named after
   the command: `ghrepo.py` for `ghrepo`.
2. Run `reload`. It lists any module that failed to load, with the error.
3. Run the checker on it. It says exactly what's wrong:

   ```
   ingest check ~/.shellcraft/modules/ghrepo.py
   ```

4. Check that it isn't switched off: `modules -a` shows every module with an on or off column.
5. Check the name. A module can't have a builtin's name (`ls`, `grep`, `curl`…), because the builtin
   always wins.

## An AI client can't see my module

A module is only offered to AI clients when it has a `NAME.skill` file next to it, and the file
doesn't say `expose = false`. A server that was already running needs `mcp restart` to pick up a
module that's been switched on or off. See [Module reference](modules/reference.md#the-skill-file).

## The start is slow

```bash
shellcraft --timing
```

Just before the prompt, this prints how long each step took. The first line is the time before
ShellCraft's own code started: Python itself, its packages, and on Windows any antivirus that scans
the files. On a typical Linux machine the whole start takes under a quarter of a second. `diag`
keeps the last start's report, so you can paste it into a bug report.

## Windows

| Problem | Fix |
| --- | --- |
| No subshells, no footer, no mouse | Install `pywinpty`: `pip install pywinpty`. The banner says when it's missing |
| An Alt shortcut does nothing | Press `Esc`, then the key |
| PowerShell won't run the venv's activation script | Run `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser` once |
| Keys or the screen behave oddly | Try another console setup: `$env:SHELLCRAFT_CONSOLE="legacy-keys"` or `"native"`, then start ShellCraft. Tell us which one works |

## A key doesn't work

`diag` is a hidden command for checking keys, the mouse and the console. Turn it on once, then use
it:

```
settings diagnostics on
diag keys                    # press keys and see what reaches the shell
diag keys --raw              # what the terminal itself sends
diag keys --raw --mouse      # the same for the mouse
```

On Linux, Alt-v should show as `escape v`. `settings diagnostics off` hides `diag` again.

## Something crashed

ShellCraft keeps running after an unexpected error and writes the details to
`~/.shellcraft/crash.log`. The newest entries are at the end.

## Start fresh

To rule out your own setup, start with an empty home folder for one session:

```bash
SHELLCRAFT_HOME=$(mktemp -d) shellcraft
```

Or move yours aside to start over for good: `mv ~/.shellcraft ~/.shellcraft.old`.

## Reporting a bug

Open an issue on GitHub with:

- what you typed, what you expected, and what happened instead;
- the output of `diag` (turn it on with `settings diagnostics on`);
- the last entry in `~/.shellcraft/crash.log`, if there is one;
- your operating system and terminal.
