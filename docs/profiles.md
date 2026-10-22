# Profiles

← [README](../README.md) · [All docs](README.md)

Named setups for different kinds of work (bug bounty, OSINT, SOC…), switched in the running
shell with `profile use NAME`. A profile layers on top of your default setup and keeps only what
it changes: the theme, settings, prompt, modules on or off, environment variables, aliases, an rc
file, and its own command history. Your API keys, the MCP token, the prefix key and your base
aliases and rc are shared by every profile.

ShellCraft always starts in the default setup; `shellcraft --profile NAME` (or
`python main.py --profile NAME`) starts in a profile.

## Quick start

```
profile starters                       # bugbounty, osint, soc: ready-made starting points
profile new recon --template osint     # a profile from a starter
profile new acme                       # or the wizard: it asks what the profile should set
profile use recon                      # switch now: theme, settings, modules, variables, aliases, history
profile                                # the list; ● marks the active one
profile off                            # back to the default setup
```

## The commands

| Command | What it does |
| --- | --- |
| `profile` / `profile list` | The profiles: description, theme, variables, aliases, modules ±, history, last used |
| `profile show [NAME]` | Everything a profile sets, and its rc (the active profile by default) |
| `profile use NAME` / `profile switch NAME` | Switch to it now; `profile use default` or `profile off` goes back |
| `profile new NAME` | The wizard (below). `--template T` starts from a starter, `--from P` copies a profile, `--empty` sets nothing, `-d DESC` a description, `--use` switches to it |
| `profile save [NAME]` | This session's setup as a profile: asks the name, shows what it will write, asks before replacing one |
| `profile edit [NAME] [rc\|aliases\|config]` | Opens a profile file in your `$EDITOR` (in a subshell), then applies it again if it is active |
| `profile set NAME KEY VALUE` | `description`, `color` (`#rrggbb`), `theme`, `history` (`own` / `shared`) |
| `profile env NAME [set VAR VALUE \| unset VAR]` | A profile's plain environment variables |
| `profile diff [A] [B]` | What differs: the active profile (or A) against the default, or A against B, rc lines included |
| `profile copy SRC DST` · `rename OLD NEW` · `delete NAME [-y]` | Manage them; the active one can't be renamed or deleted |
| `profile reload` | Apply the active profile again after editing its files |
| `profile export NAME [FILE]` · `import FILE [NAME] [-f]` | One JSON file to back up or share: no history, no secrets |
| `profile path [NAME]` | The folder |

## The wizard

`profile new NAME` with no `--template`, `--from` or `--empty` asks one question at a time; Enter
keeps the value in brackets and Ctrl-C stops without creating anything:

1. a description;
2. what to start from: nothing, the current setup, a starter or another profile;
3. the theme;
4. modules to turn off, and modules to turn on;
5. environment variables: `NAME=VALUE`, or just `NAME` for a secret (asked when the profile starts,
   then kept in the OS keyring);
6. aliases: `NAME=COMMAND`;
7. its own command history or the shared one, and the color of its prompt badge;

then it shows what it will write, creates it, and offers to switch to it.

## `profile save`

Customize a session the usual way (`theme`, `settings`, `modules disable`, `env set`, `prompt`),
then `profile save NAME` keeps it: the theme, the settings that differ from their defaults, the
prompt, the modules off, the variables set with `env set` / `export` / an rc, and the active
profile's rc and aliases. A secret is saved as an `env set -s NAME` line in the rc, never as a
value; its value goes to the OS keyring.

## While a profile is active

- The prompt shows `◆ NAME` in the profile's color (the `profile` segment), and the banner names it.
- `theme`, `settings KEY on|off`, `prompt`, `alias` and `modules enable/disable` change **the
  profile**, not the default setup. `alias -g` / `unalias -g` change the shared aliases; API keys
  (`settings NAME VALUE`) and the MCP token are always shared. `settings` says which profile it
  is changing in its title.
- `env` lists the profile's variables as `from profile:NAME`. Switching away removes them and
  brings back any value your shell had; variables you set yourself with `env set` stay.
- `history`, ↑, Ctrl-R and the grey suggestions use the profile's own history, so a target name
  typed in one engagement never shows up in another (`profile set NAME history shared` to share).
- Processes ShellCraft starts (`sub shellcraft`, `cmd &`, `mcp start`) run in the same profile.
  A server or subshell already running keeps its profile: `mcp restart` moves the server.

## On disk

`~/.shellcraft/profiles/NAME/`:

| File | Holds |
| --- | --- |
| `profile.json` | `description`, `color`, `history` (`own` / `shared`), and what it changes: `theme`, `sub_theme`, `prompt`, `settings` (only those it changes), `modules` (`enable` / `disable`), `env` (plain variables; `~` and `$VARS` are expanded) |
| `rc` | ShellCraft lines run after `~/.shellcraft/rc` when the profile starts |
| `aliases` | Aliases added on top of `~/.shellcraft/aliases`; a profile alias wins |
| `history` | Its own command history |

```json
{
  "schema": 1,
  "description": "OSINT: domain, certificate, DNS and IP lookups",
  "color": "#05d9e8",
  "theme": "nord",
  "settings": {"links": true},
  "modules": {"enable": ["queryCert", "queryDns"], "disable": ["fetch"]},
  "env": {"OSINT_ROOT": "~/osint"},
  "history": "own"
}
```

The files are plain text and JSON: edit them in any editor (`profile edit NAME config`), then
`profile reload`.
