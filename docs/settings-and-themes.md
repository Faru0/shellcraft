# Settings and themes

← [Manual](README.md)

How to change ShellCraft's settings, store the API keys modules need, let it run your system's
programs, pick or make a color theme, and choose what the prompt shows. Everything here is saved
in `~/.shellcraft/config.json`.

## Settings

```
settings                      # every setting, with its value and what it does
settings links on             # change one now (on, off or toggle)
settings reset links          # back to its default
```

A change applies at once and is kept for next time. Inside a [profile](profiles.md), it changes
the profile instead of your main setup.

| Key | Default | Meaning |
| --- | --- | --- |
| `system_commands` | off | Allow OS programs from your `PATH` (see [OS commands](#your-systems-programs)). |
| `pager` | on | Open man pages, and output taller than the window, in the scrollable viewer. |
| `spinner` | on | Show a spinner while slow commands run (with the elapsed time after 3 s). |
| `banner` | on | Show the startup banner. |
| `diagnostics` | off | Offer the `diag` command. `shellcraft --diag` turns it on; off hides `diag` again. |
| `hot_reload` | on | Reload modules when their `.py`, `.md` or `.skill` files change, in the shell and in the MCP server. |
| `lazy_modules` | on | Import a module's code the first time it runs, not at startup. |
| `clear_on_start` | on | Start on a fresh screen (the scrollback is cleared too, as `clear` does). |
| `toolbar` | on | Show the usage of the command being typed under the prompt. |
| `highlight` | on | Color the command line as you type. |
| `transient_prompt` | off | Collapse the prompt to one line after Enter. |
| `mouse` | on | Click and scroll in the completion menu and the pager; the terminal keeps the mouse at other times. |
| `mouse_always` | off | Keep the mouse on at the prompt: click to place the cursor, click prompt segments and the toolbar, wheel through history. Hold Shift to select text. |
| `links` | off | File names from `ls`, `tree`, `find`, `du` and `grep` are hyperlinks: Ctrl-click opens them. |
| `startup` | on | Run `~/.shellcraft/rc` and the `.sc` files in `~/.shellcraft/startup/` when the interactive shell starts. |
| `footer_mouse` | on | While the footer lists a subshell or an alert, the shell has the mouse at the prompt: its lines, an alert's ✖ and the badge are clickable, text is selected with Shift, and the wheel hands the mouse back to the terminal. Off: the terminal keeps the mouse at the prompt. |
| `scroll_view` | off | A turn of the wheel at the prompt (while the shell has the mouse) opens the scroll view of the session's output instead of handing the mouse to the terminal. |
| `editor` | `$EDITOR`, else nano | The editor `func edit` and `profile edit` open: `settings editor code` (VS Code, waited for), `vim`, `nano`… See [the editor](funcs.md#the-editor). |
| `loader` | `bar` | What the screen shows while the shell starts, under the banner (and in a `sub shellcraft` subshell until its prompt): `bar`, a progress bar with the step it's on, or `spinner` (`settings loader spinner`). It vanishes the moment the prompt is ready. |
| `prefix_key` | `ctrl-]` | The subshell prefix key: `ctrl-]`, `ctrl-b`, `ctrl-a`, `ctrl-t`, `ctrl-g` or `ctrl-s` (`settings prefix_key ctrl-b`). `settings prefix_key` alone lists them with what each one shadows. |
| `ai_run` | off | This session only, never saved: an AI client may run any command here at once (`run_in_shell`); `✦ run` shows in the prompt. Off: read-only commands run at once, the rest asks in the review queue. |
| `footer` | on | The shell stack on the last rows of the terminal, at the prompt and inside every subshell (click a line, or Ctrl-] N, to switch; Ctrl-] c folds it). Off: a panel under the prompt only while something is running, and nothing inside subshells. |
| `jobs_autoclear` | on | A finished subshell or background command leaves the stack at the next prompt, after its `[N] done` note and a command's output. Off: it stays until `jobs -c`. |
| `panel_hint` | off | Show a header line at the top of the subshell panel listing its keys (click or Ctrl-] N switches, Ctrl-] alone returns, Ctrl-] x ends it). |

A few settings take a word instead of on or off:

```
settings editor code          # the editor for func edit and profile edit
settings loader spinner       # what you see while the shell starts: bar or spinner
settings prefix_key ctrl-b    # the subshell key, instead of Ctrl-]
```

## API keys

Some modules call services that need a key. `settings` lists the keys every module needs, as
*module · label*, and never shows a value in full:

```
settings DNSDUMPSTER_API_KEY          # asks for the key; what you type is hidden
settings DNSDUMPSTER_API_KEY VALUE    # or set it in one go
settings reset DNSDUMPSTER_API_KEY    # forget it
```

| Variable | Module | Where to get it |
| --- | --- | --- |
| `DNSDUMPSTER_API_KEY` | `queryDns` | Free account at [dnsdumpster.com](https://dnsdumpster.com): the key is on your dashboard. |
| `CENSYS_API_TOKEN` | `queryCensys` | Censys Platform → your user icon → **API Access** → *Create New Token*. |
| `CENSYS_ORG_ID` | `queryCensys` | Paid plans only (needed for `search`): the *Current Organization* box on the same page. |

Where the keys go:

- A key you set is stored in plain text in `~/.shellcraft/config.json`. On Linux and macOS only
  you can read that file. On Windows anyone who can read your home folder can read it.
- If you'd rather not keep keys in a file, export them in your normal shell instead, in
  `~/.bashrc` for example. ShellCraft never stores those, and they win over a stored key.
- Stored keys are passed to modules as environment variables, in every mode, including the MCP
  server.
- `settings NAME VALUE` is saved in your history as `settings NAME ••••`. `settings NAME` on its
  own asks for the value and never shows it.
- `queryCensys` also needs the Censys SDK: `pip install censys-platform`.

## Environment variables

`env set NAME=VALUE` sets a variable for this session, and `settings` lists those too, under
"Environment (this session)". [Pipes and scripting](scripting.md#environment-variables) covers
`env`, and [Startup](startup.md) shows how to set them at every start.

## Your system's programs

Out of the box ShellCraft runs only its own commands and modules, so a command does the same thing
on Windows and Linux. To also run programs from your `PATH`, such as `git`, `python` or `docker`:

```
settings system_commands on
```

Or for one session only: `shellcraft --allow-system`. ShellCraft's own commands still win over a
program with the same name, and `which -a NAME` shows both. `command NAME` runs the program
instead, as in bash. A full screen program such as `vim` or `top` needs the whole terminal: run it
with `sub vim` (see [Subshells](subshells.md)).

## Themes

```
theme                     # every theme, with a color preview
theme nord                # switch now and keep it
theme preview dracula     # try one without saving
theme reset               # back to the default
```

The themes are `cyberpunk` (the default), `matrix`, `nord`, `solarized`, `dracula`, `gruvbox`,
`catppuccin` and `tokyonight`, plus `solarized-light` and `catppuccin-latte` for a light terminal.

Inside a ShellCraft subshell, `theme NAME` changes that subshell only. `theme --sub NAME` picks the
theme every subshell starts with, and `theme --sub --clear` goes back to using the main one.

### Make your own theme

Add it to `~/.shellcraft/config.json` and restart ShellCraft. Any color you leave out comes from
the `base` theme:

```json
{
  "theme": "sunset",
  "themes": {
    "sunset": {
      "base": "cyberpunk",
      "label": "Sunset",
      "prompt": "#ff7b00",
      "accent": "#ff0059",
      "gradient_from": "#ff7b00",
      "gradient_to": "#ff0059"
    }
  }
}
```

A theme can set `prompt`, `path`, `accent`, `muted`, `error`, `success`, `warning`,
`gradient_from` and `gradient_to` as `#rrggbb` colors, plus `surface` and `surface_text` for menus
and the footer, and `"light": true` for a light terminal. A color ShellCraft can't read is replaced
by the base theme's, with a warning in the banner.

## The prompt

The prompt has two lines: the first shows information, the second is where you type.

```
prompt                         # what it shows now, and every part there is
prompt name path git status    # choose the parts of the first line, in this order
prompt -r clock                # something at the right edge of the second line
prompt reset                   # back to the default
```

| Part | Shows |
| --- | --- |
| `name` | ⚡shellcraft |
| `user` | you@host |
| `path` | the folder you're in |
| `git` | the git branch, inside a repository |
| `mcp` | the MCP server's port while `mcp start` runs |
| `status` | `[✗ N]` after a command fails |
| `duration` | how long the last command took, if more than 2 seconds |
| `clock` | the time |
| `python` | the Python version |

`settings transient_prompt on` shrinks each prompt to one line after you press Enter, which keeps
the scrollback compact.
