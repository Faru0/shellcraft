# Profiles

← [Manual](README.md)

A profile is a named setup for one kind of work: bug bounty, OSINT, a client project. Switching
to it changes the theme, settings, modules, environment variables, aliases, rc file and command
history all at once, in the running shell. Switching back undoes all of it.

A profile only holds what it changes. Everything else comes from your main setup. Your API keys,
the MCP token, the prefix key, and your main aliases and rc file are shared by every profile.

```
profile use recon        # switch to it now
profile off              # back to your main setup
profile                  # the list; ● marks the active one
```

ShellCraft always starts in your main setup. To start in a profile:

```bash
shellcraft --profile recon
```

## Start from a ready-made one

Three starters come with ShellCraft: `bugbounty`, `osint` and `soc`. Make a profile from one, then
change what you like:

```
profile starters                       # what each one sets
profile new recon --template osint
profile use recon
```

Or answer a few questions instead:

```
profile new acme
```

The wizard asks for a description, what to start from, the theme, modules to switch on or off,
environment variables, aliases, and whether it keeps its own history. Press Enter to keep the value
shown in brackets, and Ctrl-C to stop without creating anything. Then it shows what it will write
and offers to switch to it.

## Write one by hand

A profile is a folder: `~/.shellcraft/profiles/NAME/`. Every file in it is optional.

```
~/.shellcraft/profiles/recon/
├── profile.json     what it changes
├── rc               commands to run when you switch to it
├── aliases          aliases added on top of your main ones
├── history          its own command history (ShellCraft writes this)
├── funcs/           its own funcs
└── startup/         more .sc files like rc, run after it
```

### profile.json

```json
{
  "schema": 1,
  "description": "OSINT: domain, certificate, DNS and IP lookups",
  "color": "#05d9e8",
  "theme": "nord",
  "settings": {"links": true},
  "modules": {"enable": ["queryCert", "queryDns"], "disable": ["queryCensys"]},
  "env": {"OSINT_ROOT": "~/osint"},
  "history": "own"
}
```

| Key | What it does |
| --- | --- |
| `description` | One line, shown by `profile list` |
| `color` | The color of its `◆ NAME` badge in the prompt, as `#rrggbb` |
| `theme` | The theme while it's active (`sub_theme` for its subshells) |
| `prompt` | The prompt segments while it's active, as `prompt` sets them |
| `settings` | Only the settings it changes, such as `{"links": true}` |
| `modules` | Modules to switch on (`enable`) and off (`disable`) |
| `env` | Environment variables. `~` and `$VARS` are expanded |
| `history` | `own` (the default) keeps its own history; `shared` uses your main one |

### rc

ShellCraft commands, run when you switch to the profile, after your main rc file. The same
format as [your main rc](startup.md#the-rc-file):

```
mkdir -p $OSINT_ROOT
cd $OSINT_ROOT
env set -s SHODAN_API_KEY        # a secret: asked with hidden typing when the profile starts
```

### aliases

The same format as [your main aliases file](aliases-and-history.md#the-aliases-file). These are
added on top of your main aliases, and win when both have the same name:

```
alias certs='queryCert'
alias subs='queryCert -s'
alias whereis='ip2geo'
```

### funcs/

Funcs that only exist in this profile. See [Funcs](funcs.md).

### startup/

More files like the profile's rc, ending in `.sc`, one per job. They run after the profile's rc,
in name order, when you start in the profile and when you switch to it. See
[Startup files](startup.md#startup-files).

After editing a profile's files, apply them to the running shell with `profile reload`. Or open
one in your editor straight from the prompt with `profile edit NAME rc` (or `aliases`, `config`).

## Save what you've set up

Set things up the usual way with `theme`, `settings`, `modules disable`, `env set` and `prompt`,
then keep it as a profile:

```
profile save acme
```

It saves the theme, the settings that differ from the defaults, the prompt, the modules you
switched off, and the variables you set. A secret is saved as an `env set -s NAME` line in the
profile's rc, never as its value, so it's asked for when the profile starts.

## While a profile is active

- The prompt shows `◆ NAME` in the profile's color.
- `theme`, `settings`, `prompt`, `alias` and `modules enable` or `disable` change the profile, not
  your main setup. `alias -g` changes your main aliases. API keys are always shared.
- `env` lists the profile's variables as `from profile:NAME`. Switching away removes them.
- `history`, ↑, Ctrl-R and the grey suggestions use the profile's own history, so a target you typed
  in one project never shows up in another.
- Subshells and background commands started from it run in the same profile.

## Every profile command

| Command | What it does |
| --- | --- |
| `profile` or `profile list` | The profiles, with what each one sets |
| `profile show NAME` | Everything a profile sets, and its rc |
| `profile use NAME` | Switch to it now. `profile off` goes back to your main setup |
| `profile new NAME` | The wizard. `--template T` starts from a starter, `--from P` copies a profile, `--empty` sets nothing, `-d DESC` a description, `--use` switches to it |
| `profile starters` | The ready-made starters |
| `profile save NAME` | This session's setup as a profile |
| `profile edit NAME [rc\|aliases\|config]` | Open one of its files in your editor |
| `profile set NAME KEY VALUE` | Change `description`, `color`, `theme` or `history` |
| `profile env NAME set VAR VALUE` | Set one of its variables (`unset VAR` removes it) |
| `profile diff A B` | What differs between two profiles, or between one and your main setup |
| `profile copy SRC DST`, `rename OLD NEW`, `delete NAME` | Manage them |
| `profile reload` | Apply the active profile again after editing its files |
| `profile export NAME FILE`, `profile import FILE` | One JSON file to back up or share. No history, no secrets |
| `profile path NAME` | Its folder |
