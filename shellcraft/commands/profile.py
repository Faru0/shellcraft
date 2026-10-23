"""profile: named ShellCraft setups (bug bounty, OSINT, SOC…), switched in the running shell.

The storage, layering and switching live in shellcraft.profiles; this is the command, its wizard
and its manual.
"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Group
from rich.table import Table
from rich.text import Text

from shellcraft import envvars, profiles
from shellcraft.builtins import builtin
from shellcraft.commands._io import user_path
from shellcraft.context import CommandError, ShellContext, Styled

USAGE = ("profile [list | show [NAME] | use NAME | off | new NAME [--from P | --template T | --empty] | "
         "save [NAME] | edit [NAME] [rc|aliases|config] | set NAME KEY VALUE | env NAME [set VAR VALUE | unset VAR] | "
         "diff [A] [B] | copy SRC DST | rename OLD NEW | delete NAME [-y] | reload | export NAME [FILE] | "
         "import FILE [NAME] [-f] | path [NAME] | starters]")

PROFILE_DOC = """# profile

Named setups for different kinds of work (bug bounty, OSINT, SOC…), switched in the running
shell. A profile **layers on top of your default setup** and keeps only what it changes: the
theme, settings, prompt, modules on or off, environment variables, aliases, an rc file, and its
own command history. Your API keys, the MCP token, the prefix key and your base aliases and rc
are shared by every profile.

## Usage

```
profile                              # the profiles (same as profile list); ● marks the active one
profile show [NAME]                  # everything a profile sets (the active one by default)
profile use NAME                     # switch to it now (also: profile switch NAME)
profile off                          # back to the default setup (also: profile use default)
profile new NAME                     # the wizard: asks what the profile should set, one question at a time
profile new NAME --template osint    # start from a starter (profile starters lists them)
profile new NAME --from OTHER        # a copy of another profile; --empty: nothing set
profile save [NAME]                  # this session's customizations as a profile (asks the name)
profile edit [NAME] [rc|aliases|config]   # open a profile file in your editor (settings editor; rc by default)
profile set NAME KEY VALUE           # description, color (#rrggbb), theme, history (own|shared)
profile env NAME                     # its environment variables; env NAME set VAR VALUE / unset VAR
profile diff [A] [B]                 # what differs: the active profile (or A) against the default, or A against B
profile copy SRC DST · rename OLD NEW · delete NAME [-y]
profile reload                       # apply the active profile again after editing its files
profile export NAME [FILE]           # one JSON file to back up or share (no history, no secrets)
profile import FILE [NAME] [-f]      # the reverse (-f replaces an existing profile)
profile path [NAME]                  # the folder (all profiles without NAME)
profile starters                     # the starter profiles: bugbounty, osint, soc
```

## What a profile holds

`~/.shellcraft/profiles/NAME/`:

| File | Holds |
| --- | --- |
| `profile.json` | description, color, `history` (own / shared), and what it changes: `theme`, `sub_theme`, `prompt`, `settings` (only those it changes), `modules` (`enable` / `disable`), `env` (plain variables, `~` and `$VARS` expanded) |
| `rc` | ShellCraft lines run after your `~/.shellcraft/rc` when the profile starts: `cd`, `env set -s TOKEN` for a secret, `sub …` |
| `aliases` | aliases added on top of `~/.shellcraft/aliases` (a profile alias wins) |
| `history` | the profile's own command history: Ctrl-R, ↑ and ghost text stay within the profile |

## While a profile is active

- The prompt shows `◆ NAME` in the profile's color (the `profile` prompt segment).
- `theme`, `settings KEY on|off`, `prompt`, `alias` and `modules enable/disable` change **the
  profile**, not your default setup. `alias -g` / `unalias -g` change the shared aliases; API
  keys (`settings NAME VALUE`) and the MCP token are always shared.
- `env` lists the profile's variables as `from profile:NAME`. Switching away removes them (and
  brings back a value your shell had); variables you set yourself with `env set` stay.
- Processes ShellCraft starts (`sub shellcraft`, `cmd &`, `mcp start`) run in the same profile
  (`SHELLCRAFT_PROFILE`). A server already running keeps its profile: `mcp restart`.

ShellCraft always starts in the default setup; `shellcraft --profile NAME` starts in a profile.

## Secrets

A profile never stores a secret's value. Put `env set -s NAME` in its rc: each start asks for it
with hidden typing (or export it in your own shell, and ShellCraft sees it as is); `profile save`
writes such lines for the secrets you set in the session.

## Examples

```
profile new recon --template osint
profile use recon
theme matrix                          # the recon profile's theme now
profile save bugbounty-acme
profile diff recon bugbounty-acme
profile off
```
"""


@builtin("profile", "Named setups: switch, create, save, edit (bug bounty, OSINT, SOC…)", USAGE,
         category="shell", stateful=True, doc=PROFILE_DOC)
def profile(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    action, rest = (args[0], args[1:]) if args else ("list", [])
    try:
        handler = ACTIONS.get(action)
        if handler is None:
            raise CommandError(f"profile: unknown action '{action}'; usage: {USAGE}")
        return handler(ctx, rest)
    except profiles.ProfileError as exc:
        raise CommandError(f"profile: {exc}") from None
    except OSError as exc:
        raise CommandError(f"profile: {exc}") from None


# ── list / show / path / starters ────────────────────────────────────────────

def _list(ctx: ShellContext, args: list[str]) -> Any:
    if args:
        raise CommandError("profile list: takes no arguments")
    current = profiles.active(ctx.config)
    table = Table(title="Profiles", title_style="sc.prompt", border_style="sc.border", header_style="sc.accent")
    for column in ("", "name", "description", "theme", "env", "aliases", "funcs", "modules", "history", "last used"):
        table.add_column(column, overflow="fold")
    from shellcraft import funcs as funcmod

    default_funcs = len(funcmod.load_dir(funcmod.default_dir(), "default"))
    table.add_row("●" if current is None else "", Text("default", style="sc.path"), "your base setup", "", "", "",
                  str(default_funcs or ""), "", "shared", "")
    plain = [f"{'*' if current is None else ' '} default"]
    for name in profiles.names():
        s = profiles.summary(name)
        on, off = s["modules"]
        mods = " ".join(p for p in (f"+{on}" if on else "", f"−{off}" if off else "") if p)
        table.add_row(Text("●", style="sc.success") if name == current else "",
                      Text(name, style=f"bold {s['color']}"), s["description"], s["theme"], str(s["env"] or ""),
                      str(s["aliases"] or ""), str(s["funcs"] or ""), mods, s["history"], s["used"])
        plain.append(f"{'*' if name == current else ' '} {name}\t{s['description']}")
    hint = Text("profile use NAME switches · profile new NAME creates one (a wizard) · profile save NAME "
                "keeps this session's setup · man profile", style="sc.muted")
    return Styled(Group(table, hint), "\n".join(plain) + "\n")


def _show(ctx: ShellContext, args: list[str]) -> Any:
    name = args[0] if args else (profiles.active(ctx.config) or profiles.DEFAULT)
    if len(args) > 1:
        raise CommandError("profile show: usage: profile show [NAME]")
    if name == profiles.DEFAULT:
        rows = profiles.described(None)
        title, data = "default (your base setup)", {}
    else:
        data = profiles.load(name)
        rows = {k: v for k, v in profiles.described(name).items()
                if not k.startswith("setting ") or k[8:] in (data.get("settings") or {})}
        title = f"◆ {name}" + (f" — {data['description']}" if data.get("description") else "")
    table = Table(title=title, title_style="sc.prompt", border_style="sc.border", header_style="sc.accent",
                  show_header=False)
    table.add_column("what", style="sc.path", no_wrap=True)
    table.add_column("value", overflow="fold")
    for key, value in rows.items():
        table.add_row(key, value)
    parts: list[Any] = [table]
    plain = "".join(f"{k}\t{v}\n" for k, v in rows.items())
    if name != profiles.DEFAULT:
        rc = profiles.read_text(name, "rc").rstrip("\n")
        if rc:
            parts.append(Text("── rc ──", style="sc.muted"))
            parts.append(Text(rc))
            plain += "-- rc --\n" + rc + "\n"
        from shellcraft import funcs as funcmod

        owned = funcmod.load_dir(funcmod.profile_dir(name), name)
        if owned:
            parts.append(Text("── funcs ──", style="sc.muted"))
            parts.append(Text("\n".join(f"{n}  {f.description}" for n, f in sorted(owned.items()))))
            plain += "-- funcs --\n" + "".join(f"{n}\t{f.description}\n" for n, f in sorted(owned.items()))
        parts.append(Text(f"{profiles.folder(name)}", style="sc.muted"))
    return Styled(Group(*parts), plain)


def _path(ctx: ShellContext, args: list[str]) -> str:
    if args:
        profiles.load(args[0])
        return f"{profiles.folder(args[0])}\n"
    return f"{profiles.root()}\n"


def _starters(ctx: ShellContext, args: list[str]) -> Any:
    table = Table(title="Starter profiles", title_style="sc.prompt", border_style="sc.border",
                  header_style="sc.accent")
    table.add_column("starter", style="sc.path")
    table.add_column("what it sets", overflow="fold")
    for name, starter in profiles.STARTERS.items():
        data = starter["data"]
        table.add_row(name, f"{data['description']}. Theme {data.get('theme')}; "
                            f"variables {', '.join(data.get('env') or {}) or 'none'}.")
    hint = Text("profile new NAME --template STARTER makes one, then profile edit NAME to adjust it", style="sc.muted")
    return Styled(Group(table, hint), "".join(f"{n}\t{s['data']['description']}\n" for n, s in profiles.STARTERS.items()))


# ── switching ────────────────────────────────────────────────────────────────

def _use(ctx: ShellContext, args: list[str]) -> Any:
    if len(args) != 1:
        raise CommandError("profile use: usage: profile use NAME (profile off: the default setup)")
    return _switch(ctx, args[0])


def _off(ctx: ShellContext, args: list[str]) -> Any:
    return _switch(ctx, None)


def _reload(ctx: ShellContext, args: list[str]) -> Any:
    current = profiles.active(ctx.config)
    if current is None:
        return Text("the default setup is active: nothing to reload (profile use NAME)", style="sc.muted")
    return _switch(ctx, current)


def _switch(ctx: ShellContext, name: str | None) -> Any:
    warnings = profiles.switch(ctx, name)
    current = profiles.active(ctx.config)
    if current is None:
        line = Text.assemble(("✓ ", "sc.success"), "the default setup")
    else:
        s = profiles.summary(current)
        on, off = s["modules"]
        bits = [f"theme {ctx.config.get('theme', 'cyberpunk')}"]
        bits += [f"{s['env']} env var(s)"] if s["env"] else []
        bits += [f"{s['aliases']} alias(es)"] if s["aliases"] else []
        bits += ["modules " + " ".join(x for x in (f"+{on}" if on else "", f"−{off}" if off else "") if x)] \
            if on or off else []
        bits += ["own history" if s["history"] != "shared" else "shared history"]
        line = Text.assemble(("✓ ", "sc.success"), (f"◆ {current}", f"bold {s['color']}"), " · " + " · ".join(bits))
    lines = [line] + [Text(f"⚠ {w}", style="sc.warning") for w in warnings]
    mcp = getattr(ctx, "mcp_http", None)
    if mcp is not None and mcp.running():
        lines.append(Text("  the running `mcp start` server keeps its profile: `mcp restart` moves it", style="sc.muted"))
    return Text("\n").join(lines)


# ── creating ─────────────────────────────────────────────────────────────────

def _flags(args: list[str], *names: str, values: tuple[str, ...] = ()) -> tuple[list[str], dict[str, Any]]:
    """Split `args` into positionals and the flags given (a flag in `values` takes the next word)."""
    found: dict[str, Any] = {}
    positional: list[str] = []
    words = iter(args)
    for word in words:
        if word in values:
            value = next(words, None)
            if value is None:
                raise CommandError(f"profile: {word} needs a value")
            found[word] = value
        elif word in names:
            found[word] = True
        elif word.startswith("-") and len(word) > 1:
            raise CommandError(f"profile: unknown switch {word}")
        else:
            positional.append(word)
    return positional, found


def _new(ctx: ShellContext, args: list[str]) -> Any:
    positional, flags = _flags(args, "--empty", "-f", "--use", values=("--from", "--template", "-d"))
    if len(positional) != 1:
        raise CommandError("profile new: usage: profile new NAME [--from P | --template T | --empty] [-d DESC]")
    name = profiles.check_name(positional[0])
    overwrite = bool(flags.get("-f"))
    if profiles.exists(name) and not overwrite:
        raise CommandError(f"profile new: '{name}' exists already (profile edit {name}, or -f to replace it)")
    if "--from" in flags:
        if overwrite and profiles.exists(name):
            profiles.delete(name)
        profiles.copy_profile(flags["--from"], name)
    elif "--template" in flags:
        profiles.from_starter(name, flags["--template"], overwrite=overwrite)
    elif flags.get("--empty") or not (ctx.interactive and ctx.ui is not None):
        profiles.create(name, {"description": flags.get("-d", "")}, overwrite=overwrite)
    else:
        return _wizard(ctx, name, flags.get("-d"), overwrite)
    if flags.get("-d"):
        data = profiles.load(name)
        data["description"] = flags["-d"]
        profiles.write(name, data)
    message = Text.assemble(("✓ ", "sc.success"), "profile ", (name, "sc.path"), " created",
                            (f"  (profile use {name} switches to it; profile edit {name} changes it)", "sc.muted"))
    return _switch(ctx, name) if flags.get("--use") else message


def _ask(ctx: ShellContext, question: str, default: str = "") -> str:
    from shellcraft import ask

    shown = f"{question} [{default}]: " if default else f"{question}: "
    try:
        answer = ask.ask(ctx, shown)
    except ask.AskError as exc:
        raise CommandError(f"profile: {exc}", exc.status) from None
    return answer.strip() or default


def _yes(ctx: ShellContext, question: str, default: bool = True) -> bool:
    answer = _ask(ctx, question + (" (Y/n)" if default else " (y/N)"))
    return default if not answer else answer.lower().startswith("y")


def _say(ctx: ShellContext, text: str, style: str = "sc.muted") -> None:
    if ctx.ui is not None:
        ctx.ui.console.print(Text(text, style=style), markup=False, highlight=False)


def _wizard(ctx: ShellContext, name: str, description: str | None, overwrite: bool) -> Any:
    """`profile new NAME` with no source: ask what the profile should set, one question at a time."""
    from shellcraft.themes import all_themes

    _say(ctx, f"New profile '{name}'. Enter keeps the value in [brackets]; Ctrl-C stops without creating it.",
         "sc.accent")
    description = description or _ask(ctx, "Description (what you use it for)")
    sources = ["empty", "current", *profiles.STARTERS, *profiles.names()]
    _say(ctx, "Start from: empty, current (this session's setup), a starter (" + ", ".join(profiles.STARTERS)
         + ")" + (", or a profile (" + ", ".join(profiles.names()) + ")" if profiles.names() else ""))
    source = _ask(ctx, "Start from", "empty")
    while source not in sources:
        _say(ctx, f"'{source}' is not one of: {', '.join(sources)}", "sc.warning")
        source = _ask(ctx, "Start from", "empty")
    if source == "current":
        data, rc, aliases_text = profiles.snapshot(ctx)
    elif source in profiles.STARTERS:
        starter = profiles.STARTERS[source]
        data, rc, aliases_text = json.loads(json.dumps(starter["data"])), starter["rc"], starter["aliases"]
    elif source in profiles.names():
        data, rc, aliases_text = (profiles.load(source), profiles.read_text(source, "rc"),
                                  profiles.read_text(source, "aliases"))
        for key in ("created", "used"):
            data.pop(key, None)
    else:
        data, rc, aliases_text = {}, "", ""
    themes = all_themes(ctx.config)
    _say(ctx, "Themes: " + ", ".join(themes))
    theme = _ask(ctx, "Theme", str(data.get("theme") or ctx.config.get("theme", "cyberpunk")))
    while theme not in themes:
        _say(ctx, f"unknown theme '{theme}'", "sc.warning")
        theme = _ask(ctx, "Theme", str(ctx.config.get("theme", "cyberpunk")))
    data["theme"] = theme
    installed = ctx.registry.all_names()
    if installed:
        modules = dict(data.get("modules") or {})
        _say(ctx, "Modules: " + ", ".join(installed))
        off = _ask(ctx, "Modules to turn OFF in this profile (space-separated)", " ".join(modules.get("disable") or []))
        on = _ask(ctx, "Modules to turn ON (ones your default setup has off)", " ".join(modules.get("enable") or []))
        modules = {k: v for k, v in (("disable", [m for m in off.split() if m in installed]),
                                     ("enable", [m for m in on.split() if m in installed])) if v}
        data["modules"] = modules
    env = dict(data.get("env") or {})
    secrets: list[str] = []
    _say(ctx, "Environment variables: NAME=VALUE for a plain one, just NAME for a secret (asked with hidden "
              "typing when the profile starts). An empty line finishes."
         + (f" Already set: {', '.join(env)}." if env else ""))
    while True:
        entry = _ask(ctx, "  variable")
        if not entry:
            break
        var, sep, value = entry.partition("=")
        var = var.strip()
        if not envvars.valid(var):
            _say(ctx, f"  '{var}' is not a valid name", "sc.warning")
            continue
        if sep:
            env[var] = value
        else:
            secrets.append(f"env set -s {var}")
    data["env"] = env
    aliases_lines = aliases_text.rstrip("\n").splitlines() if aliases_text.strip() else []
    _say(ctx, "Aliases: NAME=COMMAND (e.g. subs=queryCert -s). An empty line finishes.")
    while True:
        entry = _ask(ctx, "  alias")
        if not entry:
            break
        alias, sep, command = entry.partition("=")
        if not sep or not alias.strip() or not command.strip():
            _say(ctx, "  write it as NAME=COMMAND", "sc.warning")
            continue
        from shellcraft import aliases as aliasmod

        aliases_lines.append(f"alias {alias.strip()}={aliasmod.quote(command.strip())}")
    data["history"] = "own" if _yes(ctx, "Its own command history (kept apart from your other profiles)?") else "shared"
    color = _ask(ctx, "Prompt badge color (#rrggbb)", str(data.get("color") or profiles.DEFAULT_COLOR))
    data["color"] = color if len(color) == 7 and color.startswith("#") else profiles.DEFAULT_COLOR
    data["description"] = description
    if secrets:
        rc = (rc.rstrip("\n") + "\n" if rc.strip() else "") + "\n".join(secrets) + "\n"
    _say(ctx, "")
    _say(ctx, f"◆ {name}: {description or '(no description)'}", "sc.accent")
    for line in _preview(data, rc, aliases_lines):
        _say(ctx, "  " + line, "")
    if not _yes(ctx, "Create it?"):
        return Text("nothing created", style="sc.muted")
    profiles.create(name, data, rc, "\n".join(aliases_lines) + ("\n" if aliases_lines else ""), overwrite=overwrite)
    if _yes(ctx, f"Switch to {name} now?"):
        return _switch(ctx, name)
    return Text.assemble(("✓ ", "sc.success"), "profile ", (name, "sc.path"), " created",
                         (f"  (profile use {name} switches to it)", "sc.muted"))


def _preview(data: dict[str, Any], rc: str, aliases_lines: list[str]) -> list[str]:
    lines = [f"theme {data.get('theme')}, history {data.get('history', 'own')}, color {data.get('color')}"]
    mods = data.get("modules") or {}
    if mods:
        lines.append("modules " + " ".join([f"+{m}" for m in mods.get("enable") or []]
                                           + [f"-{m}" for m in mods.get("disable") or []]))
    if data.get("settings"):
        lines.append("settings " + ", ".join(f"{k} {'on' if v else 'off'}" for k, v in data["settings"].items()))
    lines += [f"env {k}={v}" for k, v in (data.get("env") or {}).items()]
    lines += [f"rc: {ln}" for ln in rc.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    lines += [ln for ln in aliases_lines if ln.startswith("alias ")]
    return lines


def _save(ctx: ShellContext, args: list[str]) -> Any:
    positional, flags = _flags(args, "-f", "-y", values=("-d",))
    if len(positional) > 1:
        raise CommandError("profile save: usage: profile save [NAME] [-d DESC] [-f]")
    interactive = ctx.interactive and ctx.ui is not None
    if positional:
        name = positional[0]
    elif interactive:
        name = _ask(ctx, "Save this setup as profile")
        if not name:
            return Text("nothing saved", style="sc.muted")
    else:
        raise CommandError("profile save: give a NAME (no terminal to ask on)")
    profiles.check_name(name)
    data, rc, aliases_text = profiles.snapshot(ctx)
    exists = profiles.exists(name)
    if exists:
        old = profiles.load(name)
        for key in ("description", "color", "history", "created"):
            if key in old:
                data.setdefault(key, old[key])
    if flags.get("-d"):
        data["description"] = flags["-d"]
    if interactive and not flags.get("-y"):
        _say(ctx, f"◆ {name} will hold:", "sc.accent")
        for line in _preview(data, rc, aliases_text.splitlines()):
            _say(ctx, "  " + line, "")
        if exists and not flags.get("-f") and not _yes(ctx, f"Replace the existing profile '{name}'?", False):
            return Text("nothing saved", style="sc.muted")
        if not exists and not _yes(ctx, "Save it?"):
            return Text("nothing saved", style="sc.muted")
    elif exists and not flags.get("-f"):
        raise CommandError(f"profile save: '{name}' exists already (-f replaces it)")
    profiles.create(name, data, rc, aliases_text, overwrite=True)
    profiles.write_funcs(name, profiles.session_funcs(ctx))
    active = profiles.active(ctx.config)
    if active and active != name:  # the active profile's startup files come along, as its rc does
        profiles.write_startup(name, profiles.export_bundle(active).get("startup") or {})
    return Text.assemble(("✓ ", "sc.success"), "saved as profile ", (name, "sc.path"),
                         (f"  (profile use {name}; profile show {name})", "sc.muted"))


# ── editing ──────────────────────────────────────────────────────────────────

FILES = {"rc": "rc", "aliases": "aliases", "config": profiles.FILE}


def _edit(ctx: ShellContext, args: list[str]) -> Any:
    which = args[-1] if args and args[-1] in FILES else "rc"
    names = [a for a in args if a not in FILES]
    name = names[0] if names else profiles.active(ctx.config)
    if not name:
        raise CommandError("profile edit: usage: profile edit NAME [rc|aliases|config] (the default setup: "
                           "edit ~/.shellcraft/rc)")
    profiles.load(name)
    path = profiles.folder(name) / FILES[which]
    if not path.exists():
        path.write_text(f"# {which} of profile {name}\n" if which != "config" else "{}\n", encoding="utf-8")
    if not (ctx.interactive and ctx.ui is not None):
        return f"{path}\n"
    from shellcraft import editor

    editor.open_file(ctx, path)  # settings editor (VS Code, vim…), else $VISUAL / $EDITOR
    if name == profiles.active(ctx.config):
        return _switch(ctx, name)
    return Text.assemble(("✓ ", "sc.success"), f"edited {path.name} of ", (name, "sc.path"))


SET_KEYS = ("description", "color", "theme", "history")


def _set(ctx: ShellContext, args: list[str]) -> Any:
    if len(args) < 3 or args[1] not in SET_KEYS:
        raise CommandError(f"profile set: usage: profile set NAME KEY VALUE (KEY: {', '.join(SET_KEYS)})")
    name, key, value = args[0], args[1], " ".join(args[2:])
    data = profiles.load(name)
    if key == "color" and not (len(value) == 7 and value.startswith("#")):
        raise CommandError("profile set: a color is #rrggbb")
    if key == "history" and value not in ("own", "shared"):
        raise CommandError("profile set: history is own or shared")
    if key == "theme":
        from shellcraft.themes import all_themes

        if value not in all_themes(ctx.config):
            raise CommandError(f"profile set: unknown theme '{value}'")
    data[key] = value
    profiles.write(name, data)
    if name == profiles.active(ctx.config) and key in ("theme", "history", "color"):
        return _switch(ctx, name)
    return Text.assemble(("✓ ", "sc.success"), (name, "sc.path"), f" {key} set to {value}")


def _env(ctx: ShellContext, args: list[str]) -> Any:
    if not args:
        raise CommandError("profile env: usage: profile env NAME [set VAR VALUE | unset VAR]")
    name = args[0]
    data = profiles.load(name)
    env = dict(data.get("env") or {})
    if len(args) == 1:
        return "".join(f"{k}={v}\n" for k, v in sorted(env.items())) or Text(
            f"{name} sets no variables (profile env {name} set VAR VALUE)", style="sc.muted")
    if args[1] == "set" and len(args) >= 4 and envvars.valid(args[2]):
        env[args[2]] = " ".join(args[3:])
    elif args[1] == "unset" and len(args) == 3:
        if env.pop(args[2], None) is None:
            raise CommandError(f"profile env: {name} does not set {args[2]}")
    else:
        raise CommandError("profile env: usage: profile env NAME set VAR VALUE | unset VAR (a secret: put "
                           "`env set -s VAR` in the profile's rc)")
    data["env"] = env
    profiles.write(name, data)
    if name == profiles.active(ctx.config):
        return _switch(ctx, name)
    return Text.assemble(("✓ ", "sc.success"), (name, "sc.path"), f" {args[1]} {args[2]}")


def _diff(ctx: ShellContext, args: list[str]) -> Any:
    if len(args) > 2:
        raise CommandError("profile diff: usage: profile diff [A] [B]")
    a = args[0] if args else (profiles.active(ctx.config) or profiles.DEFAULT)
    b = args[1] if len(args) > 1 else profiles.DEFAULT
    left, right = (None if a == profiles.DEFAULT else a), (None if b == profiles.DEFAULT else b)
    rows = profiles.diff(left, right)
    if not rows:
        return Text(f"{a} and {b} set the same things", style="sc.muted")
    table = Table(title=f"{a} vs {b}", title_style="sc.prompt", border_style="sc.border", header_style="sc.accent")
    table.add_column("what", style="sc.path", no_wrap=True)
    table.add_column(a, overflow="fold")
    table.add_column(b, overflow="fold")
    for row in rows:
        table.add_row(*row)
    return Styled(table, "".join(f"{w}\t{x}\t{y}\n" for w, x, y in rows))


def _copy(ctx: ShellContext, args: list[str]) -> Any:
    if len(args) != 2:
        raise CommandError("profile copy: usage: profile copy SRC DST")
    profiles.copy_profile(*args)
    return Text.assemble(("✓ ", "sc.success"), f"copied {args[0]} to ", (args[1], "sc.path"))


def _rename(ctx: ShellContext, args: list[str]) -> Any:
    if len(args) != 2:
        raise CommandError("profile rename: usage: profile rename OLD NEW")
    if args[0] == profiles.active(ctx.config):
        raise CommandError("profile rename: it is active; profile off first")
    profiles.rename(*args)
    return Text.assemble(("✓ ", "sc.success"), f"renamed {args[0]} to ", (args[1], "sc.path"))


def _delete(ctx: ShellContext, args: list[str]) -> Any:
    positional, flags = _flags(args, "-y")
    if len(positional) != 1:
        raise CommandError("profile delete: usage: profile delete NAME [-y]")
    name = positional[0]
    if name == profiles.active(ctx.config):
        raise CommandError("profile delete: it is active; profile off first")
    profiles.load(name)
    if not flags.get("-y"):
        if not (ctx.interactive and ctx.ui is not None):
            raise CommandError("profile delete: add -y to confirm (no terminal to ask on)")
        if not _yes(ctx, f"Delete profile '{name}' with its rc, aliases and history?", False):
            return Text("nothing deleted", style="sc.muted")
    profiles.delete(name)
    return Text.assemble(("✓ ", "sc.success"), "deleted profile ", (name, "sc.path"))


def _export(ctx: ShellContext, args: list[str]) -> Any:
    if not args or len(args) > 2:
        raise CommandError("profile export: usage: profile export NAME [FILE]")
    text = json.dumps(profiles.export_bundle(args[0]), indent=2) + "\n"
    if len(args) == 1:
        return text
    path = user_path(args[1])
    path.write_text(text, encoding="utf-8")
    return Text.assemble(("✓ ", "sc.success"), f"exported {args[0]} to ", (str(path), "sc.path"))


def _import(ctx: ShellContext, args: list[str]) -> Any:
    positional, flags = _flags(args, "-f")
    if not positional or len(positional) > 2:
        raise CommandError("profile import: usage: profile import FILE [NAME] [-f]")
    try:
        bundle = json.loads(user_path(positional[0]).read_text(encoding="utf-8"))
    except ValueError as exc:
        raise CommandError(f"profile import: {positional[0]}: not JSON ({exc})") from None
    name = profiles.import_bundle(bundle, positional[1] if len(positional) > 1 else None, bool(flags.get("-f")))
    return Text.assemble(("✓ ", "sc.success"), "imported profile ", (name, "sc.path"),
                         (f"  (profile show {name}; profile use {name})", "sc.muted"))


ACTIONS = {
    "list": _list, "ls": _list, "show": _show, "use": _use, "switch": _use, "off": _off, "reload": _reload,
    "new": _new, "create": _new, "save": _save, "edit": _edit, "set": _set, "env": _env, "diff": _diff,
    "copy": _copy, "rename": _rename, "delete": _delete, "rm": _delete, "export": _export, "import": _import,
    "path": _path, "starters": _starters, "templates": _starters,
}
