# Your ~/.shellcraft folder

← [Manual](README.md)

Everything ShellCraft keeps about you lives in one folder: `~/.shellcraft` in your home folder,
which is `C:\Users\you\.shellcraft` on Windows. Read this page when you want to know which file
holds what, which ones you can edit by hand, and when ShellCraft notices your edits. It also helps
when you want to back up your setup or copy it to another machine, because the whole setup is this
one folder.

## Why plain files

Your settings, aliases, startup commands and funcs are all plain text or JSON. That means you can
open them in any editor, keep them in git, read them in a diff, and copy them with `cp`. Every
command that changes your setup, such as `alias`, `theme` or `settings`, writes to the same files,
so you can mix the two ways of working freely.

## Keep it somewhere else

To keep the folder in another place, for example inside a dotfiles repository, set
`SHELLCRAFT_HOME` before you start ShellCraft:

```bash
export SHELLCRAFT_HOME=~/dotfiles/shellcraft
```

Put that line in your normal shell's startup file, such as `~/.bashrc`, so it's set every time.
ShellCraft creates the folder if it isn't there.

## The map

Here's what you'll find inside. The comment next to each name says what it is:

```
~/.shellcraft/
├── config.json              your settings, theme, API keys and more
├── rc                       commands that run every time the shell starts
├── aliases                  your shortcuts
├── history                  the commands you typed
├── startup/                 more files like rc, one per job, run after it
│   ├── README.txt
│   └── 10-example.sc.sample
├── funcs/                   named lists of commands you run on demand
│   └── example.func.sample
├── ai-policy.toml.sample    rules and roles for what an AI may run (copy to ai-policy.toml)
├── modules/                 your own commands (Python)
└── profiles/                one folder per profile
    └── NAME/
        ├── profile.json
        ├── rc
        ├── aliases
        ├── history
        └── funcs/
```

The files you're meant to edit are in the tree above. ShellCraft also keeps some working files
there, listed [at the end](#files-shellcraft-manages).

## The starter files

The first time ShellCraft starts without them, it writes these for you. They're there to show you
the format, so you can learn by changing a working example instead of starting from an empty file.

| File | What's in it |
| --- | --- |
| `rc` | A walkthrough in comments, with ready lines you can switch on by removing the `#` |
| `aliases` | A few example aliases, all commented out |
| `ai-policy.toml.sample` | Rules and roles for what an AI may run, all commented out. Copy it to `ai-policy.toml` to use it |
| `funcs/example.func.sample` | A complete example func |
| `startup/README.txt` | How startup files work |
| `startup/10-example.sc.sample` | A startup file that asks a question and sets up a project |

Nothing in them does anything as they come. Two rules make sure of that. A line starting with `#`
is a comment, so the shell skips it. And a file ending in `.sample` is never loaded. To use an
example, copy it to a name without `.sample`:

```bash
cd ~/.shellcraft/funcs
cp example.func.sample recon.func
```

Now `recon` is a func you can run with `func run recon`, and you can edit the copy freely. The
`.sample` file stays as a reference.

### Getting them back

Each starter file is written only once. If you delete one, it stays deleted, because ShellCraft
assumes you meant it. To get the missing ones back, or to see the examples from a newer version:

```bash
shellcraft --init            # writes the starter files that are missing, never touches yours
shellcraft --init --force    # also refreshes the .sample and README files
```

From a checkout without `pip install`, use `python main.py --init`.

`--force` only replaces the `.sample` and `README` files, which are ShellCraft's examples. It never
overwrites your `rc`, `aliases` or anything else you've written.

## Each file, and when ShellCraft reads it

The question people usually have is "I edited the file, why didn't anything change?" This table
answers it. The last column says when an edit takes effect, and how to apply it right away when
it doesn't.

| File | What it is | Edit it with | Your change applies |
| --- | --- | --- | --- |
| `config.json` | Settings, the theme, API keys, custom themes, disabled modules | `settings`, `theme`, `modules`, or any editor | at once from a command; at the next start from an editor |
| `rc` | ShellCraft commands, run at every start | any editor | at the next start, or now with `source ~/.shellcraft/rc` |
| `aliases` | `alias name='command'` lines | `alias` and `unalias`, or any editor | at the next command |
| `history` | Everything you typed | `history -c` clears it | at once |
| `startup/*.sc` | More files like rc, one per job, run after it | any editor | at the next start, or now with `startup run` |
| `funcs/*.func` | Funcs that work in every profile | `func new`, `func edit`, or any editor | at once |
| `modules/*.py` | Your own commands | any editor, or `ingest` | at once (hot reload) |
| `profiles/NAME/` | One profile's own settings, rc, aliases, history and funcs | `profile` commands, or any editor | when you switch to it, or with `profile reload` |

### config.json

A JSON file. You rarely need to edit it by hand, because every command that changes a setting
saves it right away. Hand editing is useful for a [custom theme](settings-and-themes.md#make-your-own-theme)
or to copy your setup to another machine. Here is a small one:

```json
{
  "theme": "nord",
  "settings": {"system_commands": true, "links": true},
  "env": {"DNSDUMPSTER_API_KEY": "your-key-here"},
  "disabled_modules": ["queryCensys"]
}
```

Reading it from the top: the theme is Nord, two settings are switched on, one API key is stored,
and the `queryCensys` module is switched off. Anything not in the file has its default value.

If you edit it while ShellCraft is running, the shell doesn't see your change until it starts
again. Your edit is safe in the meantime: when a command saves a setting, it writes only the
entries it changed and keeps the rest of the file as it is on disk.

API keys you save with `settings NAME` are stored here in plain text. On Linux and macOS, once a
key is stored, the file is only readable by you. If you'd rather not keep keys in a file, export
them in your normal shell instead, in `~/.bashrc` for example. ShellCraft never stores those.

### rc and startup/

Both run when the interactive shell starts, and both hold ShellCraft commands, the same lines
you'd type at the prompt. `rc` runs first. `startup/` holds more `.sc` files like it, one per job,
run after it in name order. That's why the example is called `10-example.sc.sample`: the number
at the front decides the order.

Why two places? `rc` is good for a handful of lines. Once you have several separate jobs, such as
connecting a VPN, setting up a project and printing a reminder, a file per job is easier to read,
switch off and share. [Startup](startup.md) covers both, with examples.

### aliases and history

`aliases` holds one `alias name='command'` per line, the same format as bash's `~/.bash_aliases`.
`history` holds everything you typed, with a timestamp for each command.
[Aliases and history](aliases-and-history.md) covers the formats, examples, and how to keep
something out of your history.

### funcs/

One file per func, named `NAME.func`. A func is a named list of commands you run when you ask
for it, unlike `rc`, which runs at every start. [Funcs](funcs.md) shows the format.

### modules/

Your own commands. A module is a `NAME.py` file, usually with a `NAME.md` manual page and a
`NAME.skill` file for AI clients next to it. Put the files here and the command works at the
next prompt, no restart needed. A module here wins over a bundled one with the same name.
ShellCraft creates this folder the first time you ingest a script, or you can create it yourself.
See [Your first module](modules/first-module.md) and [Add modules you already have](modules/adding-modules.md).

### profiles/

One folder per profile, holding only what that profile changes. Anything a profile doesn't set
comes from the files above. [Profiles](profiles.md) shows how to write one by hand.

## Files ShellCraft manages

These are working files. You can look at them, and some are useful in a bug report, but there's
no need to edit them.

| File or folder | What it holds |
| --- | --- |
| `timing.txt` | How long the last start took, step by step (see `shellcraft --timing`) |
| `crash.log` | Unexpected errors, with details for a bug report |
| `jobs/` | The output of background commands (`cmd &`) |
| `records/` | Transcripts from `record` and `script` |
| `alerts/`, `bin/` | Alerts waiting to be shown, and the `alert` helper that scripts can call |
| `review/`, `briefs/`, `mainlink.json` | Messages between the shell and connected AI clients |
| `tasks/`, `findings/` | The task board and the case files your AI agents share (`tasks`, `findings`) |
| `ai-audit.jsonl` | What AI clients did (`ai log`) |
| `cache/` | Module answers remembered for a few minutes, so two agents asking the same thing don't spend two API calls |
| `mcp-http.log` | The log of the MCP server started with `mcp start` |

## Back it up or move it

To copy your setup to another machine, copy the files you edit: `config.json`, `rc`, `aliases`,
`startup/`, `funcs/`, `modules/` and `profiles/`. Leave out `history` if you'd rather not carry
your commands along, and the working files above, which ShellCraft recreates. Remember that
`config.json` may hold API keys.

## Start over

To get a clean setup, move the folder out of the way and start ShellCraft. It creates a new one
with fresh starter files:

```bash
mv ~/.shellcraft ~/.shellcraft.old
```

Your old files are still in `~/.shellcraft.old`, so you can copy back the pieces you want.
