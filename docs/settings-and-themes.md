# Settings and themes

← [Manual](README.md)

This page is the reference for changing how ShellCraft behaves and looks. Use it to switch a
setting on or off, store the API keys modules need, let ShellCraft run your system's programs, pick
or make a color theme, and choose what the prompt shows. The tables list every option, and the
short text before each one says when you'd use it.

Everything here is saved in `~/.shellcraft/config.json`, so a change lasts until you change it
back. You'll rarely need to open that file: each command below saves it for you.

## Settings

```
settings                      # every setting, with its value and what it does
settings links on             # change one now (on, off or toggle)
settings reset links          # back to its default
```

A change applies at once and is kept for next time. Inside a [profile](profiles.md), it changes
the profile instead of your main setup.

Besides `on` and `off`, a setting takes `toggle`, which flips it, and the words `yes`, `no`,
`true`, `false`, `1` and `0`. Anything else gets `settings: expected on, off or toggle`.

The table lists every on/off setting with its default. Scan the Meaning column for the behavior
you want to change; the key on the left is what you type after `settings`.

| Key | Default | Meaning |
| --- | --- | --- |
| `system_commands` | off | Allow OS programs from your `PATH` (see [OS commands](#your-systems-programs)). |
| `pager` | on | Open man pages, and whatever is piped into `less`, in the scrollable viewer. Off: print them instead. |
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
| `footer_mouse` | on | While the footer lists a subshell or an alert, the shell has the mouse at the prompt: its lines, an alert's × and the badge are clickable, text is selected with Shift, and the wheel hands the mouse back to the terminal. Off: the terminal keeps the mouse at the prompt. |
| `scroll_view` | off | A turn of the wheel at the prompt (while the shell has the mouse) opens the scroll view of the session's output instead of handing the mouse to the terminal. |
| `editor` | `$EDITOR`, else nano | The editor `func edit` and `profile edit` open: `settings editor code` (VS Code, waited for), `vim`, `nano`… See [the editor](funcs.md#the-editor). |
| `loader` | `bar` | What the screen shows while the shell starts, under the banner (and in a `sub shellcraft` subshell until its prompt): `bar`, a progress bar with the step it's on, or `spinner` (`settings loader spinner`). It vanishes the moment the prompt is ready. |
| `prefix_key` | `ctrl-]` | The subshell prefix key: `ctrl-]`, `ctrl-b`, `ctrl-a`, `ctrl-t`, `ctrl-g` or `ctrl-s` (`settings prefix_key ctrl-b`). `settings prefix_key` alone lists them with what each one shadows. |
| `ai_access` | on | An AI client may run any command here at once (`run_in_shell`); `✦ run` shows in the prompt and `ai log` lists what it did. Off: read-only commands run at once, the rest asks in the review queue. Rules and roles go in `ai-policy.toml` (see [MCP and the AI co-pilot](mcp-and-ai.md#rules-and-roles)). `ai_run` is the old name and still works. |
| `footer` | on | The shell stack on the last rows of the terminal, at the prompt and inside every subshell (click a line, or Ctrl-] N, to switch; Ctrl-] c folds it). Off: a panel under the prompt only while something is running, and nothing inside subshells. |
| `jobs_autoclear` | on | A finished subshell or background command leaves the stack at the next prompt, after its `[N] done` note and a command's output. Off: it stays until `jobs -c`. |
| `panel_hint` | off | Show a header line at the top of the subshell panel listing its keys (click or Ctrl-] N switches, Ctrl-] alone returns, Ctrl-] x ends it). |

A few settings take a word instead of on or off. Type the setting alone, such as
`settings prefix_key`, to see the words it accepts:

```
settings editor code          # the editor for func edit and profile edit
settings loader spinner       # what you see while the shell starts: bar or spinner
settings prefix_key ctrl-b    # the subshell key, instead of Ctrl-]
```

## API keys

Some modules call services that need an account, and the service gives you a key to prove who you
are. You only have to store it once. `settings` lists the keys every module needs, as
*module · label*, and never shows a value in full:

```
settings DNSDUMPSTER_API_KEY          # asks for the key; what you type is hidden
settings DNSDUMPSTER_API_KEY VALUE    # or set it in one go
settings reset DNSDUMPSTER_API_KEY    # forget it
```

When a module needs a key you haven't set, its error message says which one and where to get it.
This table has the same information for the bundled modules:

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
on Windows and Linux. That's also why `git status` answers `command not found: git` on a fresh
install. To also run programs from your `PATH`, such as `git`, `python` or `docker`:

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

If your terminal has a white background, pick one of the light ones. The dark themes use light
text, which is hard to read on white.

Inside a ShellCraft subshell, `theme NAME` changes that subshell only. `theme --sub NAME` picks the
theme every subshell starts with, and `theme --sub --clear` goes back to using the main one. A
different color per subshell makes it easy to see which one you're typing in.

### Make your own theme

When none of the themes is quite right, start from the closest one and change a few colors. Add it
to `~/.shellcraft/config.json` and restart ShellCraft. Any color you leave out comes from the
`base` theme, or from `nord` when there's no `base`:

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

This one is called `sunset`. It starts from `cyberpunk`, changes the prompt and accent colors and
the banner's gradient, and the top-level `"theme": "sunset"` makes it the active theme. `label` is
the name `theme` shows in its list.

A theme can set `prompt`, `path`, `accent`, `muted`, `error`, `success`, `warning`,
`gradient_from` and `gradient_to` as `#rrggbb` colors, plus `surface` and `surface_text` for menus
and the footer, and `"light": true` for a light terminal. A color ShellCraft can't read is replaced
by the base theme's, with a warning in the banner that starts `theme 'sunset': invalid color`.

## The prompt

The prompt has two lines: the first shows information, the second is where you type. You choose
which pieces of information the first line shows, and in what order. Fewer parts keep it short in
a narrow window; `clock` on the right is handy when you look back through a long session.

```
prompt                         # what it shows now, and every part there is
prompt name path git status    # choose the parts of the first line, in this order
prompt -r clock                # something at the right edge of the second line
prompt reset                   # back to the default
```

These are the parts you can use. Several only appear when there's something to show, so the
default line stays short.

| Part | Shows |
| --- | --- |
| `name` | ⚡shellcraft (with `[N]` inside `sub shellcraft`) |
| `profile` | `◆ NAME` while a [profile](profiles.md) is active |
| `user` | you@host |
| `path` | the folder you're in |
| `git` | the git branch, inside a repository |
| `mcp` | the MCP server's port while `mcp start` runs, `✦ run` while AI access is on, `✦ N tasks` while the task board has work |
| `status` | `[✗ N]` after a command fails |
| `duration` | how long the last command took, if more than 2 seconds |
| `clock` | the time |
| `python` | the Python version |
| `jobs` | `N sub` while subshells started with `sub` are running |

The default is `name profile user path git mcp status duration` on the left, and nothing on the
right.

`settings transient_prompt on` shrinks each prompt to one line after you press Enter, which keeps
the scrollback compact.
