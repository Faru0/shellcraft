# Your ~/.shellcraft folder

← [Manual](README.md)

Everything ShellCraft keeps about you lives in one folder: `~/.shellcraft` in your home folder
(`C:\Users\you\.shellcraft` on Windows). This page shows what's in it, which files you can edit
by hand, and when ShellCraft notices your changes.

To keep it somewhere else, set `SHELLCRAFT_HOME` before you start ShellCraft:

```bash
export SHELLCRAFT_HOME=~/dotfiles/shellcraft
```

## The map

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

The first time ShellCraft starts without them, it writes these for you:

| File | What's in it |
| --- | --- |
| `rc` | A walkthrough in comments, with ready lines you can switch on by removing the `#` |
| `aliases` | A few example aliases, all commented out |
| `ai-policy.toml.sample` | Rules and roles for what an AI may run, all commented out. Copy it to `ai-policy.toml` to use it |
| `funcs/example.func.sample` | A complete example func |
| `startup/README.txt` | How startup files work |
| `startup/10-example.sc.sample` | A startup file that asks a question and sets up a project |

Nothing in them does anything as they come. A line starting with `#` is a comment, and a file
ending in `.sample` is never loaded. To use an example, copy it to a name without `.sample`:

```bash
cd ~/.shellcraft/funcs
cp example.func.sample recon.func
```

Each starter file is written only once. If you delete one, it stays deleted. To get the missing
ones back, or to see this version's examples:

```bash
shellcraft --init            # writes the starter files that are missing, never touches yours
shellcraft --init --force    # also refreshes the .sample and README files
```

(From a checkout without `pip install`, use `python main.py --init`.)

## Each file, and when ShellCraft reads it

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

API keys you save with `settings NAME` are stored here in plain text. On Linux and macOS the file
is only readable by you. If you'd rather not keep keys in a file, export them in your normal
shell instead (in `~/.bashrc`, for example). ShellCraft never stores those.

### rc and startup/

Both run when the interactive shell starts, and both hold ShellCraft commands, the same lines
you'd type at the prompt. `rc` runs first. `startup/` holds more `.sc` files like it, one per job,
run after it in name order.
[Startup](startup.md) covers both, with examples.

### aliases and history

[Aliases and history](aliases-and-history.md) covers the file format, examples, and how to keep
something out of your history.

### funcs/

One file per func, named `NAME.func`. [Funcs](funcs.md) shows the format.

### modules/

Your own commands. A module is a `NAME.py` file, usually with a `NAME.md` manual page and a
`NAME.skill` file for AI clients next to it. Put the files here and the command works at the
next prompt, no restart needed. A module here wins over a bundled one with the same name.
ShellCraft creates this folder the first time you ingest a script, or you can create it yourself.
See [Your first module](modules/first-module.md) and [Add modules you already have](modules/adding-modules.md).

### profiles/

One folder per profile, holding only what that profile changes. [Profiles](profiles.md) shows
how to write one by hand.

## Files ShellCraft manages

These are working files. You can look at them, but there's no need to edit them.

| File or folder | What it holds |
| --- | --- |
| `timing.txt` | How long the last start took, step by step (see `shellcraft --timing`) |
| `crash.log` | Unexpected errors, with details for a bug report |
| `jobs/` | The output of background commands (`cmd &`) |
| `records/` | Transcripts from `record` and `script` |
| `alerts/`, `bin/` | Alerts waiting to be shown, and the `alert` helper that scripts can call |
| `review/`, `briefs/`, `mainlink.json` | Messages between the shell and connected AI clients |
| `mcp-http.log` | The log of the MCP server started with `mcp start` |

## Start over

To get a clean setup, move the folder out of the way and start ShellCraft. It creates a new one
with fresh starter files:

```bash
mv ~/.shellcraft ~/.shellcraft.old
```
