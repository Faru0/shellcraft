# Troubleshooting

← [Manual](README.md)

This page is for when something didn't do what you expected. It's organised by symptom: find the
message you're looking at, or the thing that's going wrong, and the fix is right under it. The
messages are quoted exactly as ShellCraft prints them, so you can search this page for a few
words of yours.

If your problem isn't here, the [bug report](#reporting-a-bug) section says what to send.

## How to read an error

When a command fails, ShellCraft draws a box around the error. The title says what kind of
failure it was, the first line names the command, and the last line is the message:

```
╭─ ✗ command failed ────────────╮
│ cat  (command)                │
│ cat: nofile.txt: no such file │
╰───────────────────────────────╯
```

In a pipe, the title says `✗ pipeline failed` and the first line says which segment broke, such
as `segment 2/3`. A `✗ parse error` box means ShellCraft couldn't even read the line, so nothing
ran. It points at the spot with a `^`.

After a failure, the prompt shows `[✗ N]`, where N is the exit status. Zero means success, and
anything else means something went wrong. `echo $?` prints the last one.

## Commands that aren't found

### `command not found: git (OS commands are off`

The full message ends by telling you to run `settings system_commands on`. You're trying to run a
program from your system, such as `git`, `python` or `docker`. Out of the box ShellCraft runs only
its own commands and modules, so a command behaves the same on Windows and Linux. To let it run
your system's programs too:

```
settings system_commands on
```

For one session only, start it with `shellcraft --allow-system` instead. `which -a NAME` shows
everything a name could run. See [Settings and themes](settings-and-themes.md#your-systems-programs).

### `command not found: gti`, with `did you mean 'git'?`

A typo. When a name is close to a command ShellCraft knows, the message ends with a suggestion.
Fix the spelling. If you make the same typo all the time, an [alias](aliases-and-history.md) can
catch it.

### `command not found: diag`

`diag` is hidden until you turn diagnostics on:

```
settings diagnostics on
```

`settings diagnostics off` hides it again.

### `module 'myip' is disabled`

The full message ends with `run: modules enable myip`. Someone, maybe you or a
[profile](profiles.md), switched the module off. Switch it back on:

```
modules enable myip
```

`modules -a` lists every module with an on or off column, including the ones that are off.

### A full-screen program like `vim` or `top` looks broken

A program that draws on the whole screen needs the whole terminal. Run it in a subshell:

```
sub vim notes.txt
```

See [Subshells](subshells.md).

## My module doesn't show up

You wrote a module, and typing its name gives `command not found: NAME`, or the module fails when
you run it. Work through these in order:

1. Check where it is. Your modules belong in `~/.shellcraft/modules/`, with the file named after
   the command: `ghrepo.py` for `ghrepo`. A name has to start with a letter and use only letters,
   digits, `-` and `_`.
2. Run it once. ShellCraft imports a module's code the first time you use it, so an error in the
   file shows up then, in the error box. For example:

   ```
   ╭─ ✗ command failed ────────────────────────────────────╮
   │ ghrepo  (command)                                     │
   │ SyntaxError: '(' was never closed (ghrepo.py, line 2) │
   ╰───────────────────────────────────────────────────────╯
   ```

   A `ModuleNotFoundError: No module named 'requests'` means your module imports a package that
   isn't installed. Install it with `pip install requests` in the same environment ShellCraft runs
   in.
3. Run `reload`. It loads the modules folder again and lists any module that failed, one line
   each, such as `⚠ ghrepo.py: SyntaxError: '(' was never closed (ghrepo.py, line 2)`. With
   `settings lazy_modules off` it catches import errors too, because every module is imported at
   once.
4. Run the checker on it. It tests everything and says exactly what's wrong:

   ```
   ingest check ~/.shellcraft/modules/ghrepo.py
   ```

   Each line is `✓ PASS`, `! WARN` or `✗ FAIL`, with the reason next to it.
5. Check that it isn't switched off: `modules -a` shows every module with an on or off column.
6. Check the name. A module can't have a builtin's name, such as `ls`, `grep` or `curl`, because
   the builtin always wins. The checker says so:
   `'ls' is a builtin command; builtins win, so the module could never run`. Rename the file.

[Your first module](modules/first-module.md) walks through writing one that works.

## A module asks for a key

Some modules call services that need an account. The message says where to get the key and how
to save it. For example:

```
queryDns: no DnsDumpster API key. Get a free one at https://dnsdumpster.com, then run: settings DNSDUMPSTER_API_KEY
```

```
queryCensys: no Censys API token. Create a Personal Access Token in the Censys Platform (user icon → API Access), then run: settings CENSYS_API_TOKEN
```

Run the `settings` line it gives you. It asks for the key with hidden typing, so the key never
shows on screen or lands in your history. `queryCensys` also needs the Censys SDK. If it's
missing, the message says `queryCensys: needs the Censys SDK: pip install censys-platform`.
[API keys](settings-and-themes.md#api-keys) lists where to get each key.

## An AI client can't see my module

A module is only offered to AI clients when it has a `NAME.skill` file next to it, and the file
doesn't say `expose = false`. A server that was already running needs `mcp restart` to pick up a
module that's been switched on or off. See [Module reference](modules/reference.md#the-skill-file).

## Mistakes on the command line

### `unterminated ' quote`

A `✗ parse error`. You opened a quote and never closed it, so ShellCraft can't tell where the text
ends. The `^` under the line points at the quote. Add the missing one:

```
echo 'it works'
```

To put an apostrophe inside, use double quotes around the text: `echo "it's fine"`.

### `alias x: an alias must be a single command (no '|', '>' or '>>')`

An alias stands for one command. For several commands joined by a pipe, write a
[function](scripting/functions.md) or a [func](funcs.md) instead.

### `settings: unknown setting 'NAME'` or `theme: unknown theme 'NAME'`

The name is misspelled. The message lists every name that would work, in brackets after `try:`.
`settings` and `theme` on their own show the lists too.

### `settings: expected on, off or toggle, got 'maybe'`

An on/off setting takes `on`, `off` or `toggle`. `yes`, `no`, `true`, `false`, `1` and `0` work
too.

### `profile: no profile 'NAME'` or `func: no func 'NAME'`

There's nothing by that name. The message lists the ones you have, or says
`none yet (profile new NAME)` when there are none. `profile` and `func list` show them.

### `NAME runs OS commands (system: yes): settings system_commands on`

The func's header says it runs programs from your system, and OS commands are off. Turn them on
with `settings system_commands on`, or start ShellCraft with `--allow-system`. See
[Funcs](funcs.md).

## Variables and scripts

### A variable prints nothing

A variable you never set is empty, as in bash, so `echo "hi $nmae"` prints `hi `. Check the
spelling. To catch a typo like this, put `set -u` at the top of the script: then an unset `$name`
is an error instead (see the next entry). To give a default when it's empty, write
`${name:-default}`.

### `nmae: unbound variable`

`set -u` is on and the variable has no value. Fix the name, set it first, or give it a default
with `${nmae:-default}`. `set +u` turns the check off. See
[Stop on errors](scripting/errors.md#stop-on-errors-with-set--e).

### `${!name}: bad substitution`

A `✗ parse error`, so nothing ran. The `${…}` form isn't one ShellCraft has. It has bash's
defaults (`:-` `:=` `:+` `:?`), the length `${#v}`, trimming (`#` `##` `%` `%%`), replacing
(`/` `//`), slices (`${v:0:3}`) and case (`^^` `,,` `^` `,`). `${!name}` (the value of the variable
whose name is in `name`) and `${v@Q}` aren't there. The table is in
[Defaults, lengths and trimming](scripting/variables-and-input.md#defaults-lengths-and-trimming).

### `name: parameter null or not set`

A `${name:?}` stopped the script, because `name` was empty or unset. That's what it's for: it
guards a value the rest needs. With your own message, `${name:?give a name}`, you see
`name: give a name` instead. In a function or func, `1: …` means the first argument was missing.
Set the variable, or pass the argument.

### `command not found: x` after `x = 5`

At the prompt and in bash-style lines, spaces around `=` make `x` a command. Write `x=5`. The
form with spaces works only inside a `-py` block or a `def`, see
[Python assignment](scripting.md#python-assignment).

### `can only concatenate str (not "int") to str (values set from bash are text: …)`

Python code got text where it expected a number. A value set the bash way (`x=5`, `read`, a loop
word, an argument) is text in Python, so write `int(x) + 1`. A value set in Python (`n = 5`,
`n = int(input())`) keeps its type. The same hint follows `'>' not supported between instances of
'str' and 'int'`. See [Text and numbers](scripting/python-style.md#text-and-numbers).

## curl and client certificates

### `curl: (58) the private key in client.pem is encrypted: …`

Your key has a password and curl wasn't given one. Add it after a colon,
`curl -E client.pem:PASSWORD URL`, or with `--pass PASSWORD`.

### `curl: (58) could not unlock the key in client.pem: wrong password?`

The password doesn't open the key. Check it, and check that the `-E` or `--key` file is the right
one. Status 58 also covers a certificate file that's missing, and a key password or `.p12` file
when the `cryptography` package isn't installed (`pip install cryptography`).
[Client certificates](curl.md#client-certificates) has the rest.

## Your rc file or a startup file fails

When a line in a startup file fails, ShellCraft says which file and which line, then shows the
error box:

```
✗ rc:2: cd /nonexist
╭─ ✗ command failed ───────────────╮
│ cd  (command)                    │
│ cd: no such directory: /nonexist │
╰──────────────────────────────────╯
```

Here `rc:2` means line 2 of `~/.shellcraft/rc`. The rest of the file still runs, so one bad line
doesn't stop your setup. If the file has `set -e` in it, the line ends with
`(set -e: the rest of the file was skipped)`, and nothing after it runs.

To fix it, open the file, correct the line, and run it again without restarting:

```
source ~/.shellcraft/rc
```

If the shell won't start properly because of a startup file, start without them:
`shellcraft --no-startup`. [Startup](startup.md) has more, and
[Errors and cleanup](scripting/errors.md) explains `set -e`.

## The start is slow

```bash
shellcraft --timing
```

Right before the prompt, this prints how long each step took. The first line is the time before
ShellCraft's own code started: Python itself, its packages, and on Windows any antivirus that scans
the files. On a typical Linux machine the whole start takes under a quarter of a second. `diag`
keeps the last start's report, so you can paste it into a bug report.

## The banner warns about a missing package

Some features need an extra package. When one is missing, the banner says what you lose and how
to install it. The messages start like this:

| Message | What's missing | Fix |
| --- | --- | --- |
| `pyte is not installed: switching between subshells replays their last output instead of redrawing their screen` | A clean redraw when you switch subshells | `pip install pyte` |
| ``the 'mcp' package is not installed: `mcp start` and --mcp won't work`` | The MCP server for AI clients | `pip install mcp` |
| ``pywinpty is not installed: `sub PROGRAM` runs in the foreground, with no footer and no job list`` | Windows only: subshells, the footer, the mouse | `pip install pywinpty` |

The banner prints the exact command, with the path of the Python ShellCraft runs in. Use that one,
so the package goes into the right environment.

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

On Linux, Alt-v should show as `escape v`. If it doesn't show at all, your terminal is keeping
the key for itself: Konsole's menu bar takes Alt-v, for example. Press `Esc` then `v` instead.
`settings diagnostics off` hides `diag` again.
[Keyboard and mouse](keyboard-and-mouse.md#linux-and-windows) has more on Alt keys.

## Odd numbers on the line after a Ctrl-C

Something like `<64;10;5M` or `12;34R` at the prompt is a mouse or cursor report the terminal
sent while a command was running, which older versions handed to the next prompt as text.
ShellCraft now filters them out of what was typed ahead, and keeps the terminal from cutting
a color code in half when a command is interrupted.

If you still see it, the shell has already recorded what happened. Right after it happens:

```
settings diagnostics on      # once
diag --save                  # writes ~/.shellcraft/diag.txt
```

The file's **recent interrupts** section says where each Ctrl-C of the session landed, what was
running, the console modes at that moment on Windows, and what the terminal delivered before
the next prompt. Its **system** line names the terminal hosting the shell (Windows Terminal,
conhost, VS Code). Send the file, and say whether you used the wheel or the mouse at the time.
On Windows, also run `python main.py --timing` once; its last line says the same about the host.

## Something crashed

ShellCraft keeps running after an unexpected error. You'll see the error box, then a grey line
like this one:

```
(an unexpected error in the prompt; details in /home/you/.shellcraft/crash.log)
```

That file, `~/.shellcraft/crash.log`, has the full details. The newest entries are at the end.
Include the last one in a [bug report](#reporting-a-bug).

## Start fresh

When nothing makes sense, check whether your own setup is the cause. This starts ShellCraft with
an empty home folder for one session, and leaves yours alone:

```bash
SHELLCRAFT_HOME=$(mktemp -d) shellcraft
```

If the problem goes away, it's something in your `~/.shellcraft` folder. To skip only your startup
files, use `shellcraft --no-startup`.

Or move yours aside to start over for good. ShellCraft makes a new folder with fresh starter files
at the next start:

```bash
mv ~/.shellcraft ~/.shellcraft.old
```

## Reporting a bug

Open an issue on GitHub with:

- what you typed, what you expected, and what happened instead;
- the output of `diag` (turn it on with `settings diagnostics on`);
- the last entry in `~/.shellcraft/crash.log`, if there is one;
- your operating system and terminal.
