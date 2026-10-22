# Settings, API keys and themes

← [README](../README.md) · [All docs](README.md)

Every setting, the API keys modules need, OS commands, themes, and the command-line options and
environment variables.

## Settings

`settings` shows every setting and the API keys modules need. `settings KEY on|off|toggle` changes
a setting immediately, and `settings reset KEY` restores its default. Everything is saved in
`~/.shellcraft/config.json`.

| Key | Default | Meaning |
| --- | --- | --- |
| `system_commands` | off | Allow OS programs from your `PATH` (see [OS commands](#os-commands)). |
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
| `keyring` | on | Keep API keys in the OS keyring when the `keyring` package is installed (else plain text in `config.json`). |
| `startup` | on | Run `~/.shellcraft/startup/*.py` and `~/.shellcraft/rc` when the interactive shell starts. |
| `footer_mouse` | on | While the footer lists a subshell or an alert, the shell has the mouse at the prompt: its lines, an alert's ✖ and the badge are clickable, text is selected with Shift, and the wheel hands the mouse back to the terminal. Off: the terminal keeps the mouse at the prompt. |
| `scroll_view` | off | A turn of the wheel at the prompt (while the shell has the mouse) opens the scroll view of the session's output instead of handing the mouse to the terminal. |
| `editor` | `$EDITOR`, else nano | The editor `func edit` and `profile edit` open: `settings editor code` (VS Code, waited for), `vim`, `nano`… See [the editor](funcs.md#the-editor). |
| `prefix_key` | `ctrl-]` | The subshell prefix key: `ctrl-]`, `ctrl-b`, `ctrl-a`, `ctrl-t`, `ctrl-g` or `ctrl-s` (`settings prefix_key ctrl-b`). `settings prefix_key` alone lists them with what each one shadows. |
| `ai_run` | off | This session only, never saved: an AI client may run any command here at once (`run_in_shell`); `✦ run` shows in the prompt. Off: read-only commands run at once, the rest asks in the review queue. |
| `footer` | on | The shell stack on the last rows of the terminal, at the prompt and inside every subshell (click a line, or Ctrl-] N, to switch; Ctrl-] c folds it). Off: a panel under the prompt only while something is running, and nothing inside subshells. |
| `jobs_autoclear` | on | A finished subshell or background command leaves the stack at the next prompt, after its `[N] done` note and a command's output. Off: it stays until `jobs -c`. |
| `panel_hint` | off | Show a header line at the top of the subshell panel listing its keys (click or Ctrl-] N switches, Ctrl-] alone returns, Ctrl-] x ends it). |

### Environment variables

Below the API keys, `settings` lists the environment variables set in this session with
`env set`, `export` or your rc file, each with its full value (masked for a secret), where it
came from, and whether it overrides your shell's value. `settings reset NAME` removes one, like
`env unset NAME`. See [Environment variables](scripting.md#environment-variables).

### API keys

Some modules call services that need a key. `settings` lists them as *module · label*, and never
shows the value in full: `set ••••ab12`, `from environment`, or `not set`.

```
settings DNSDUMPSTER_API_KEY          # asks for the key; typing is hidden
settings DNSDUMPSTER_API_KEY VALUE    # or set it in one go
settings reset DNSDUMPSTER_API_KEY    # forget it
```

| Variable | Module | Where to get it |
| --- | --- | --- |
| `DNSDUMPSTER_API_KEY` | `queryDns` | Free account at [dnsdumpster.com](https://dnsdumpster.com): the key is on your dashboard. |
| `CENSYS_API_TOKEN` | `queryCensys` | Censys Platform → your user icon → **API Access** → *Create New Token*. |
| `CENSYS_ORG_ID` | `queryCensys` | Paid plans only (needed for `search`): the *Current Organization* box on the same page. |

<details>
<summary>How keys are stored and passed to modules</summary>

- **Storage:** with the optional `keyring` package (`pip install keyring`, or `pip install -e ".[keyring]"`) and a working backend (Windows Credential Manager, macOS Keychain, Secret Service), a key goes into the OS keyring and `config.json` holds only the marker `@keyring`. **Without it, keys are plain text** under `"env"` in `~/.shellcraft/config.json`: mode 600 keeps other users out on Linux and macOS, but **does nothing on Windows**. `settings migrate-keys` moves keys already stored in plain text into the keyring; `settings keyring off` keeps them in `config.json`.
- **Delivery:** stored keys are exported as environment variables at startup, in every mode including `--mcp`, and right after you set one. A variable already set in your shell (or an MCP client's `env` block) wins at startup. After `settings reset`, your shell's own value applies again.
- **History:** `settings NAME VALUE` is saved as `settings NAME ••••`, but prefer the prompt form, `settings NAME`. Start any line with a space to keep it out of the history entirely.
- `queryCensys` also needs the Censys SDK: `pip install censys-platform`, or `pip install -e ".[censys]"`.

</details>

### OS commands

By default ShellCraft runs **only** its builtins and modules, so a command line does the same thing
on Windows and Linux. `settings system_commands on` (or `--allow-system` for one session) lets
unknown commands run programs from your `PATH`; builtins still win over programs with the same
name (`which -a NAME` shows both). OS programs run with their output captured; a full-screen program such as `vim` or `top`
runs with `sub vim`, which hands it the terminal (see [Subshells](subshells.md)).

## Themes

`cyberpunk` (the default), `matrix`, `nord`, `solarized`, `dracula`, `gruvbox`, `catppuccin` and
`tokyonight`, plus `solarized-light` and `catppuccin-latte` for a light terminal background.
`theme` lists them with color swatches, `theme NAME` switches immediately and saves your choice,
`theme preview NAME` tries one without saving, and `theme reset` goes back to the default.
In a subshell (`sub shellcraft`), `theme NAME` changes that subshell only. `theme --sub NAME`
saves a theme for every subshell to start with, `theme --sub` shows it and `theme --sub --clear`
removes it (subshells then start with the main theme).

<details>
<summary>Make your own theme</summary>

Add it to `~/.shellcraft/config.json`. Any color you leave out is taken from `base`:

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
`gradient_from` and `gradient_to`, as `#rrggbb` hex values, plus `surface` and `surface_text`
(the completion menu and toolbars; derived from `muted` when left out) and `"light": true` for a
light terminal. An invalid color is replaced by the base theme's color, with a warning shown in
the banner.

</details>

---

## Command-line options and environment variables

| Option | Meaning |
| --- | --- |
| `--mcp` | Run as an MCP server over stdio instead of the interactive shell. |
| `--mcp-http HOST:PORT` | Run as an MCP server over streamable HTTP at `http://HOST:PORT/mcp`. Only addresses on this machine are allowed (`127.0.0.1`, `localhost`, `::1`). |
| `--theme NAME` | Use a theme for this session only. |
| `--profile NAME` | Start in a profile ([Profiles](profiles.md)); without it every start is the default setup. |
| `--no-banner` | Skip the startup banner. |
| `--diag` | Turn the `diagnostics` setting on (the hidden `diag` command) and print the diagnostics report at startup. |
| `--allow-system` | Allow OS commands for this session (see [OS commands](#os-commands)). |
| `--modules DIR` | Load modules from DIR instead of the bundled ones. |
| `--expose NAME,...` | With `--mcp` / `--mcp-http`: offer only these modules as tools. |
| `--token TOKEN`, `--no-token` | With `--mcp-http`: the bearer token clients must send (default: a random one, printed at startup), or none. |
| `--version`, `-h` | Show the version, or the help text. |

| Environment variable | Meaning |
| --- | --- |
| `SHELLCRAFT_HOME` | Where config and history are stored (default `~/.shellcraft`). |
| `SHELLCRAFT_MODULES` | The default modules folder (overridden by `--modules`). |
| `SHELLCRAFT_MCP_TOKEN` | The bearer token for `--mcp-http` (overridden by `--token`). |
| `SHELLCRAFT_MCP_TIMEOUT` | Seconds an MCP tool call may take (default 120). |
