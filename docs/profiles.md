# Profiles

← [Manual](README.md)

This page is for you if you use ShellCraft for more than one kind of work and want each to have
its own setup. After reading it you'll be able to create a profile from a starter, from a few
questions, or by hand, switch between profiles in the running shell, and save the setup you have
right now as a profile.

## What a profile is

A profile is a named setup for one kind of work: bug bounty, OSINT, a client project. Switching
to it changes the theme, settings, modules, environment variables, aliases, rc file and command
history all at once, in the running shell. Switching back undoes all of it.

Here's when that helps. Say you do OSINT work for one client and bug bounty work on the side. For
the OSINT work you want the DNS and certificate modules on, a working folder variable set, and a
couple of aliases. For bug bounty you want different modules and different aliases. And you don't
want a domain you typed for one client to show up as a grey suggestion while you work on the
other. A profile for each keeps them apart.

A profile only holds what it changes. Everything else comes from your main setup. Your API keys,
the MCP token, the prefix key, and your main aliases and rc file are shared by every profile, so
you set those up once.

## Switch between profiles

```
profile use recon        # switch to it now
profile off              # back to your main setup
profile                  # the list; ● marks the active one
```

`profile switch NAME` does the same as `profile use NAME`, and `profile use default` does the same
as `profile off`.

When a profile is active, the prompt shows `◆ recon` in the profile's color, so you always know
which setup you're in.

ShellCraft always starts in your main setup. To start in a profile:

```bash
shellcraft --profile recon
```

## Start from a ready-made one

The quickest way to get a working profile is to copy a starter. Three come with ShellCraft:
`bugbounty`, `osint` and `soc`. Make a profile from one, then change what you like:

```
profile starters                       # what each one sets
profile new recon --template osint
profile use recon
```

The first line shows what each starter sets, so you can pick the closest. The second makes a
profile called `recon` with the `osint` starter's settings. The third switches to it.

### Or answer a few questions

```
profile new acme
```

Without `--template`, `profile new` starts a wizard. It asks for a description, what to start from,
the theme, modules to switch on or off, environment variables, aliases, and whether it keeps its
own history. Press Enter to keep the value shown in brackets, and Ctrl-C to stop without creating
anything. At the end it shows what it will write and offers to switch to it.

A few switches skip parts of the wizard: `--from P` copies another profile, `--empty` sets
nothing, `-d DESC` gives the description, and `--use` switches to the new profile straight away.

## Save what you've set up

Sometimes you don't know what you want until you've tried it. Set things up the usual way with
`theme`, `settings`, `modules disable`, `env set` and `prompt`, then keep it as a profile:

```
profile save acme
```

It saves the theme, the settings that differ from the defaults, the prompt, the modules you
switched off, and the variables you set.

A secret is handled differently. If you set one with `env set -s NAME`, the profile gets an
`env set -s NAME` line in its rc, never the value. So the profile asks for it, with hidden typing,
each time it starts. That way a profile folder never holds a password, and you can share it.

## Write one by hand

A profile is a folder: `~/.shellcraft/profiles/NAME/`. Every file in it is optional, so start with
the one you need and add more later. Making a profile, any way, also makes its empty `startup/`
folder.

```
~/.shellcraft/profiles/recon/
├── profile.json     what it changes
├── rc               commands to run when you switch to it
├── aliases          aliases added on top of your main ones
├── history          its own command history (ShellCraft writes this)
├── funcs/           its own funcs
└── startup/         more .sc files like rc, run after it
```

After editing a profile's files, apply them to the running shell with `profile reload`. You can
also open one in your editor straight from the prompt with `profile edit NAME rc`, or `aliases`,
or `config` for `profile.json`.

### profile.json

This file holds the settings part of the profile. Here's a complete one:

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

Read it as a list of differences from your main setup. While this profile is active, the theme is
Nord, file names are links, two modules are switched on and one off, `$OSINT_ROOT` points at
`~/osint`, and the history is kept separately. Each key:

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
format as [your main rc](startup.md#the-rc-file). Use it for things you'd otherwise type every
time you start this kind of work:

```
mkdir -p $OSINT_ROOT
cd $OSINT_ROOT
env set -s SHODAN_API_KEY        # a secret: asked with hidden typing when the profile starts
```

The first line makes the working folder if it isn't there yet, and `-p` keeps `mkdir` quiet when
it already is. The second goes into it. `$OSINT_ROOT` has a `$` because you want the variable's
value here, not the word itself. The third asks for a key each time, as described in
[Save what you've set up](#save-what-youve-set-up).

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

More files like the profile's rc, ending in `.sc`, one per job. The folder is already there in
every new profile, so drop the files in. They run after the profile's rc, in name order, when you
start in the profile and when you switch to it. See [Startup files](startup.md#startup-files).

## While a profile is active

Most commands change the profile, not your main setup. That's on purpose: you can tune a profile
by using it, without touching anything else.

- The prompt shows `◆ NAME` in the profile's color.
- `theme`, `settings`, `prompt`, `alias` and `modules enable` or `disable` change the profile, not
  your main setup. `alias -g` changes your main aliases. API keys are always shared.
- `env` lists the profile's variables as `from profile:NAME`. Switching away removes them.
- `history`, ↑, Ctrl-R and the grey suggestions use the profile's own history, so a target you typed
  in one project never shows up in another.
- Subshells and background commands started from it run in the same profile. An MCP server that
  was already running keeps its profile until `mcp restart`.

## Share or back up a profile

`profile export recon recon.json` writes one JSON file with everything the profile sets, and
`profile import recon.json` reads it back, on this machine or another. The file has no history and
no secrets, so it's safe to send to a colleague.

## Every profile command

Use this table when you know what you want to do and only need the exact command.

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

## If it doesn't work

- **`profile: no profile 'NAME'`.** There's no profile by that name. The message lists the ones you
  have. `profile` shows them too.
- **You edited a profile's files and nothing changed.** The running shell read them when you
  switched. Run `profile reload`.
- **A change went into the wrong place.** While a profile is active, `alias`, `theme` and
  `settings` change the profile. Check the prompt for `◆ NAME`, and use `alias -g` or
  `profile off` first when you meant your main setup.
