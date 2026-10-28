"""Profiles: named ShellCraft setups (bug bounty, OSINT, SOC…) layered on top of the default one.

A profile is a folder `~/.shellcraft/profiles/NAME/`:

    profile.json   description, color, history (own | shared), and the overlay: theme, sub_theme,
                   prompt, settings (only the ones it changes), modules {"enable": [...],
                   "disable": [...]}, env {NAME: value} (plain variables; secrets go in its rc)
    rc             ShellCraft lines run after the base ~/.shellcraft/rc (`env set -s TOKEN`, `cd …`)
    aliases        aliases added on top of ~/.shellcraft/aliases (a profile alias wins)
    history        its own command history (unless "history": "shared")

The **effective config** is the base config.json with the overlay on top (`effective()`); it is a
`Config` whose `.profile` names the profile, and `config.save_config()` sends what a profile owns
(theme, sub_theme, settings, prompt, disabled_modules) to its profile.json and the rest (API keys,
the MCP token, the prefix key, custom themes) to the base file. API keys, the MCP token, the base
aliases and the base rc are shared by every profile.

Nothing remembers the active profile across restarts: every start is the default setup unless
`shellcraft --profile NAME`. Inside a session, `SHELLCRAFT_PROFILE` names it for the processes
ShellCraft starts (lite subshells, `cmd &`, the `mcp start` server), which then run in it too.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

DEFAULT = "default"
ENV_VAR = "SHELLCRAFT_PROFILE"
SCHEMA = 1
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
OWNED = ("theme", "sub_theme", "settings", "prompt", "disabled_modules")  # config keys a profile owns
OVERLAY = ("theme", "sub_theme", "prompt", "settings", "modules")  # their form in profile.json
DEFAULT_COLOR = "#05d9e8"
FILE = "profile.json"


class ProfileError(Exception):
    """A profile that can't be used: bad name, missing, unreadable."""


# ── where profiles live ──────────────────────────────────────────────────────

def root() -> Path:
    from shellcraft.config import config_dir

    return config_dir() / "profiles"


def check_name(name: str) -> str:
    if name == DEFAULT:
        raise ProfileError("'default' is the base setup, not a profile you can change this way")
    if not NAME.match(name):
        raise ProfileError(f"invalid profile name '{name}' (lowercase letters, digits, - and _, up to 32)")
    return name


def folder(name: str) -> Path:
    return root() / check_name(name)


def exists(name: str) -> bool:
    return NAME.match(name) is not None and name != DEFAULT and (root() / name / FILE).is_file()


def names() -> list[str]:
    try:
        return sorted(p.name for p in root().iterdir() if p.is_dir() and exists(p.name))
    except OSError:
        return []


def load(name: str) -> dict[str, Any]:
    """profile.json of NAME. Raises ProfileError when it is missing or not a profile."""
    path = folder(name) / FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        known = ", ".join(names()) or "none yet (profile new NAME)"
        raise ProfileError(f"no profile '{name}' (profiles: {known})") from None
    except (OSError, ValueError) as exc:
        raise ProfileError(f"profile '{name}': cannot read {path}: {exc}") from None
    if not isinstance(data, dict):
        raise ProfileError(f"profile '{name}': {path} is not a JSON object")
    return data


def write(name: str, data: dict[str, Any]) -> None:
    """Write profile.json atomically (like config.json)."""
    path = folder(name) / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"schema": SCHEMA, **{k: v for k, v in data.items() if k != "schema"}}
    fd, tmp = tempfile.mkstemp(prefix=".profile-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def text_file(name: str, which: str) -> Path:
    """The profile's `rc` or `aliases` file (it may not exist yet)."""
    return folder(name) / which


def read_text(name: str, which: str) -> str:
    try:
        return text_file(name, which).read_text(encoding="utf-8")
    except OSError:
        return ""


def write_text(name: str, which: str, text: str) -> None:
    path = text_file(name, which)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if not text or text.endswith("\n") else text + "\n", encoding="utf-8")


def history_file(config: Any) -> Path:
    """The command history of the active profile (its own file unless it shares), else the base one."""
    from shellcraft.config import history_path

    name = active(config)
    if name:
        try:
            if load(name).get("history", "own") != "shared":
                return folder(name) / "history"
        except ProfileError:
            pass
    return history_path()


def active(config: Any) -> str | None:
    """The profile a config belongs to (None: the default setup)."""
    return getattr(config, "profile", None)


# ── create, copy, rename, delete ─────────────────────────────────────────────

def create(name: str, data: dict[str, Any] | None = None, rc: str = "", aliases: str = "",
           overwrite: bool = False) -> None:
    check_name(name)
    if exists(name) and not overwrite:
        raise ProfileError(f"profile '{name}' exists already (profile delete {name}, or -f to replace it)")
    now = time.strftime("%Y-%m-%d %H:%M")
    base = {"description": "", "color": DEFAULT_COLOR, "history": "own", "created": now}
    write(name, {**base, **(data or {}), "created": (data or {}).get("created", now)})
    if rc:
        write_text(name, "rc", rc)
    if aliases:
        write_text(name, "aliases", aliases)
    make_startup(name)


def make_startup(name: str) -> None:
    """Give a profile its startup folder, empty, if it has none yet."""
    from shellcraft import startup

    (folder(name) / startup.FOLDER).mkdir(parents=True, exist_ok=True)


def copy_profile(src: str, dst: str) -> None:
    load(src)
    check_name(dst)
    if exists(dst):
        raise ProfileError(f"profile '{dst}' exists already")
    shutil.copytree(folder(src), folder(dst), ignore=shutil.ignore_patterns("history"))
    data = load(dst)
    data["created"] = time.strftime("%Y-%m-%d %H:%M")
    data.pop("used", None)
    write(dst, data)
    make_startup(dst)


def rename(old: str, new: str) -> None:
    load(old)
    check_name(new)
    if exists(new):
        raise ProfileError(f"profile '{new}' exists already")
    folder(old).rename(folder(new))


def delete(name: str) -> None:
    load(name)
    shutil.rmtree(folder(name))


# ── layering ─────────────────────────────────────────────────────────────────

def effective(base: Any, name: str) -> Any:
    """The base config with profile NAME on top, as a Config remembering the profile."""
    from shellcraft.config import Config

    data = load(name)
    result = Config(copy.deepcopy(dict(base)))
    for key in ("theme", "sub_theme"):
        if isinstance(data.get(key), str):
            result[key] = data[key]
    if isinstance(data.get("prompt"), dict):
        result["prompt"] = copy.deepcopy(data["prompt"])
    if isinstance(data.get("settings"), dict):
        merged = dict(result.get("settings") or {})
        merged.update({k: v for k, v in data["settings"].items() if isinstance(v, bool)})
        result["settings"] = merged
    modules = data.get("modules") if isinstance(data.get("modules"), dict) else {}
    disabled = set(result.get("disabled_modules") or [])
    disabled -= set(modules.get("enable") or [])
    disabled |= set(modules.get("disable") or [])
    result["disabled_modules"] = sorted(disabled)
    result.profile = name
    result.baseline = copy.deepcopy(dict(result))
    return result


def overlay_of(config: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """What a profile must store so that effective(base) gives `config` back: only the differences."""
    from shellcraft.settings import SETTINGS

    out: dict[str, Any] = {}
    for key in ("theme", "sub_theme"):
        if isinstance(config.get(key), str) and config.get(key) != base.get(key):
            out[key] = config[key]
    if isinstance(config.get("prompt"), dict) and config.get("prompt") != base.get("prompt"):
        out["prompt"] = copy.deepcopy(config["prompt"])
    mine, theirs = config.get("settings") or {}, base.get("settings") or {}
    changed = {k: v for k, v in mine.items()
               if v != theirs.get(k, SETTINGS[k].default if k in SETTINGS else None)}
    if changed:
        out["settings"] = changed
    now, before = set(config.get("disabled_modules") or []), set(base.get("disabled_modules") or [])
    modules = {k: v for k, v in (("enable", sorted(before - now)), ("disable", sorted(now - before))) if v}
    if modules:
        out["modules"] = modules
    return out


def store_overlay(name: str, config: dict[str, Any]) -> None:
    """Save what a profile owns (from the effective config) into its profile.json."""
    from shellcraft.config import load_config

    data = load(name)
    for key in OVERLAY:
        data.pop(key, None)
    data.update(overlay_of(config, load_config()))
    write(name, data)


def plain(value: Any) -> str:
    """A profile's env value with ~ and $VARS expanded, as `env set` would see it typed."""
    return os.path.expandvars(os.path.expanduser(str(value)))


# ── what `profile list` / `show` / `diff` read ───────────────────────────────

def summary(name: str) -> dict[str, Any]:
    from shellcraft import aliases as aliasmod

    data = load(name)
    mods = data.get("modules") if isinstance(data.get("modules"), dict) else {}
    rc_lines = [ln for ln in read_text(name, "rc").splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    return {
        "name": name, "description": data.get("description", ""), "color": data.get("color", DEFAULT_COLOR),
        "theme": data.get("theme", ""), "env": len(data.get("env") or {}),
        "aliases": len(aliasmod.parse_file(read_text(name, "aliases"))[0]), "rc": len(rc_lines),
        "modules": (len(mods.get("enable") or []), len(mods.get("disable") or [])),
        "history": data.get("history", "own"), "used": data.get("used", ""), "created": data.get("created", ""),
        "funcs": len(list((folder(name) / "funcs").glob("*.func"))) if (folder(name) / "funcs").is_dir() else 0,
    }


def described(name: str | None) -> dict[str, Any]:
    """Everything a profile sets, flattened for `show` and `diff` (the default: the base setup)."""
    from shellcraft import aliases as aliasmod
    from shellcraft.config import load_config
    from shellcraft.settings import SETTINGS
    from shellcraft.settings import get as setting

    base = load_config()
    config = effective(base, name) if name else base
    data = load(name) if name else {}
    out: dict[str, Any] = {
        "theme": config.get("theme", "cyberpunk"), "sub_theme": config.get("sub_theme", ""),
        "prompt": " ".join((config.get("prompt") or {}).get("left") or []) or "(default)",
        "modules off": ", ".join(config.get("disabled_modules") or []) or "(none)",
        "history": data.get("history", "own") if name else "shared (the base file)",
    }
    out.update({f"setting {k}": "on" if setting(config, k) else "off" for k in SETTINGS})
    out.update({f"env {k}": str(v) for k, v in sorted((data.get("env") or {}).items())})
    file_aliases = aliasmod.parse_file(read_text(name, "aliases"))[0] if name else {}
    out.update({f"alias {k}": v for k, v in sorted(file_aliases.items())})
    return out


def diff(a: str | None, b: str | None) -> list[tuple[str, str, str]]:
    """(what, in A, in B) for every difference, rc files included line by line."""
    left, right = described(a), described(b)
    rows = [(k, left.get(k, "—"), right.get(k, "—")) for k in sorted(set(left) | set(right))
            if left.get(k) != right.get(k)]
    rc_a = read_text(a, "rc").splitlines() if a else []
    rc_b = read_text(b, "rc").splitlines() if b else []
    if rc_a != rc_b:
        import difflib

        for line in difflib.unified_diff(rc_a, rc_b, lineterm="", n=0):
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
                rows.append(("rc", line[1:] if line[0] == "-" else "—", line[1:] if line[0] == "+" else "—"))
    return rows


# ── `profile save`: the current session as a profile ─────────────────────────

def snapshot(ctx: Any) -> tuple[dict[str, Any], str, str]:
    """(profile.json data, rc, aliases) capturing the session's customizations: the theme, the
    settings that differ from their defaults, the prompt, the modules turned off, the variables
    set with `env set` / `export` / an rc (secrets become `env set -s NAME` rc lines, asked for when
    the profile starts; their values are never stored), and the active profile's rc and aliases."""
    from shellcraft import envvars
    from shellcraft.config import load_config
    from shellcraft.settings import SETTINGS

    config = ctx.config
    base = load_config()
    data: dict[str, Any] = {"theme": config.get("theme", "cyberpunk")}
    if config.get("sub_theme"):
        data["sub_theme"] = config["sub_theme"]
    if isinstance(config.get("prompt"), dict):
        data["prompt"] = copy.deepcopy(config["prompt"])
    changed = {k: v for k, v in (config.get("settings") or {}).items() if k in SETTINGS and v != SETTINGS[k].default}
    if changed:
        data["settings"] = changed
    now, before = set(config.get("disabled_modules") or []), set(base.get("disabled_modules") or [])
    modules = {k: v for k, v in (("enable", sorted(before - now)), ("disable", sorted(now))) if v}
    if modules:
        data["modules"] = modules
    current = active(config)
    rc = read_text(current, "rc") if current else ""
    secrets = []
    env: dict[str, str] = {}
    for entry in envvars.session(ctx):
        value = os.environ.get(entry.name, "")
        if entry.secret:  # never stored: the profile asks for it when it starts
            if f"env set -s {entry.name}" not in rc:
                secrets.append(f"env set -s {entry.name}")
        else:
            env[entry.name] = value
    if env:
        data["env"] = env
    if secrets:
        head = rc.rstrip("\n") + "\n" if rc.strip() else ""
        rc = head + "# secrets: asked with hidden typing when the profile starts\n" + "\n".join(secrets) + "\n"
    aliases_text = read_text(current, "aliases") if current else ""
    return data, rc, aliases_text


# ── starters ─────────────────────────────────────────────────────────────────

STARTERS: dict[str, dict[str, Any]] = {
    "bugbounty": {
        "data": {"description": "Bug bounty: one program's scope, recon modules, clickable output",
                 "color": "#ff2a6d", "theme": "dracula", "settings": {"links": True},
                 "modules": {"enable": ["queryCert", "queryDns", "ip2geo"]},
                 "env": {"BB_PROGRAM": "example", "BB_ROOT": "~/bugbounty/$BB_PROGRAM"}},
        "rc": ("# Bug bounty profile: runs after ~/.shellcraft/rc when you `profile use bugbounty`.\n"
               "# Keep each program's notes and output apart:\n"
               "mkdir -p $BB_ROOT\ncd $BB_ROOT\n"
               "# env set -s H1_API_TOKEN          # a platform token, asked with hidden typing at start\n"),
        "aliases": ("alias subs='queryCert -s'\nalias dns='queryDns'\nalias geo='ip2geo'\n"
                    "alias headers='curl -sIL'\n"),
    },
    "osint": {
        "data": {"description": "OSINT: domain, certificate, DNS and IP lookups",
                 "color": "#05d9e8", "theme": "nord", "settings": {"links": True},
                 "modules": {"enable": ["queryCert", "queryDns", "queryCensys", "ip2geo", "myip"]},
                 "env": {"OSINT_ROOT": "~/osint"}},
        "rc": "# OSINT profile\nmkdir -p $OSINT_ROOT\ncd $OSINT_ROOT\n",
        "aliases": ("alias certs='queryCert'\nalias subs='queryCert -s'\nalias whereis='ip2geo'\n"
                    "alias keys='jq -k'\nalias shape='jq -s'\n"),
    },
    "soc": {
        "data": {"description": "SOC: log triage, alerts, the AI review loop",
                 "color": "#f6c177", "theme": "gruvbox", "settings": {"jobs_autoclear": False},
                 "env": {"SOC_LOGS": "/var/log"}},
        "rc": ("# SOC profile\n# review                      # what the AI flagged or asks about\n"
               "# sub tail -f $SOC_LOGS/auth.log   # watch a log in the background\n"
               "# the ten most common lines of whatever is piped in (an alias can't hold a pipe)\n"
               "top10() { sort | uniq -c | sort -rn | head -10; }\n"),
        "aliases": ("alias fails=\"grep -i -E 'fail|denied|invalid'\"\nalias errs='grep -i error'\n"
                    "alias flag='mark -p high'\n"),
    },
}


def from_starter(name: str, starter: str, overwrite: bool = False) -> None:
    if starter not in STARTERS:
        raise ProfileError(f"no starter '{starter}' (starters: {', '.join(STARTERS)})")
    s = STARTERS[starter]
    create(name, copy.deepcopy(s["data"]), s["rc"], s["aliases"], overwrite=overwrite)


# ── export / import ──────────────────────────────────────────────────────────

BUNDLE = "shellcraft-profile"


def export_bundle(name: str) -> dict[str, Any]:
    """One JSON document: profile.json, rc, aliases, funcs and startup files (no history; secrets are
    never in a profile)."""
    data = load(name)
    data.pop("used", None)
    funcs_dir = folder(name) / "funcs"
    funcs = {}
    if funcs_dir.is_dir():
        funcs = {p.stem: p.read_text(encoding="utf-8") for p in sorted(funcs_dir.glob("*.func"))}
    startup_dir = folder(name) / "startup"
    startup = {}
    if startup_dir.is_dir():
        startup = {p.name: p.read_text(encoding="utf-8") for p in sorted(startup_dir.glob("*.sc"))}
    return {"format": BUNDLE, "version": SCHEMA, "name": name, "profile": data,
            "rc": read_text(name, "rc"), "aliases": read_text(name, "aliases"), "funcs": funcs,
            "startup": startup}


def import_bundle(bundle: Any, name: str | None = None, overwrite: bool = False) -> str:
    if not isinstance(bundle, dict) or bundle.get("format") != BUNDLE or not isinstance(bundle.get("profile"), dict):
        raise ProfileError("not a ShellCraft profile export (profile export NAME FILE makes one)")
    target = name or str(bundle.get("name") or "")
    create(target, bundle["profile"], str(bundle.get("rc") or ""), str(bundle.get("aliases") or ""),
           overwrite=overwrite)
    write_funcs(target, bundle.get("funcs") if isinstance(bundle.get("funcs"), dict) else {})
    write_startup(target, bundle.get("startup") if isinstance(bundle.get("startup"), dict) else {})
    return target


_STARTUP_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*\.sc$")


def write_startup(name: str, texts: dict[str, str]) -> None:
    """Put startup files (file name → text) into a profile's startup folder; a name that isn't a
    plain `NAME.sc` (a path, for one) is skipped."""
    target = folder(name) / "startup"
    for file_name, text in texts.items():
        if _STARTUP_NAME.match(str(file_name)):
            target.mkdir(parents=True, exist_ok=True)
            (target / str(file_name)).write_text(str(text), encoding="utf-8")


def write_funcs(name: str, texts: dict[str, str]) -> None:
    """Put func files (name → text) into a profile's funcs folder."""
    from shellcraft import funcs as funcmod

    target = folder(name) / "funcs"
    for func_name, text in texts.items():
        if funcmod.NAME.match(str(func_name)):
            target.mkdir(parents=True, exist_ok=True)
            (target / f"{func_name}{funcmod.SUFFIX}").write_text(str(text), encoding="utf-8")


def session_funcs(ctx: Any) -> dict[str, str]:
    """The funcs `profile save` copies: the active profile's files and the session's own funcs."""
    from shellcraft import funcs as funcmod

    current = active(ctx.config)
    texts = {}
    if current:
        texts.update({n: funcmod.render(f) for n, f in funcmod.load_dir(funcmod.profile_dir(current), current).items()})
    texts.update({n: funcmod.render(f) for n, f in getattr(ctx, "funcs", {}).items()})
    return texts


# ── switching ────────────────────────────────────────────────────────────────

def switch(ctx: Any, name: str | None) -> list[str]:
    """Leave the active profile and enter NAME (None or "default": the base setup). Returns
    warnings (a module it names that isn't installed…). Raises ProfileError before changing
    anything when NAME can't be used."""
    from shellcraft import envvars
    from shellcraft.config import Config, load_config

    name = None if name in (None, DEFAULT) else name
    data = load(name) if name else {}
    old = active(ctx.config)
    for entry in list(ctx.env_vars.values()):  # 1. the old profile's variables go, host values return
        if old and entry.origin == f"profile:{old}":
            envvars.unset_var(ctx, entry.name)
    before = set(ctx.config.get("disabled_modules") or [])
    new = effective(load_config(), name) if name else load_config()
    if isinstance(ctx.config, Config):  # 2. the effective config, in place (others hold it)
        ctx.config.clear()
        ctx.config.update(new)
        ctx.config.baseline = new.baseline
        ctx.config.profile = name
    else:
        ctx.config = new
        ctx.config.profile = name
    warnings = apply_config(ctx, before) + (missing_modules(ctx, name) if name else [])
    if name:  # 3. its variables and rc
        os.environ[ENV_VAR] = name
        warnings += enter_env(ctx, name, data)
        run_rc(ctx, name)
        data = load(name)
        data["used"] = time.strftime("%Y-%m-%d %H:%M")
        write(name, data)
    else:
        os.environ.pop(ENV_VAR, None)
    hook = getattr(ctx, "profile_hook", None)
    if hook is not None:
        hook(name)  # the interactive shell: the history file, the prompt
    return warnings


def enter_env(ctx: Any, name: str, data: dict[str, Any] | None = None) -> list[str]:
    """Set the profile's `env` variables, recorded as `profile:NAME` (a switch undoes them)."""
    from shellcraft import envvars

    warnings = []
    for var, value in ((data if data is not None else load(name)).get("env") or {}).items():
        if envvars.valid(str(var)):
            envvars.set_var(ctx, str(var), plain(value), how=f"profile:{name}")
        else:
            warnings.append(f"env: '{var}' is not a valid variable name (skipped)")
    return warnings


def missing_modules(ctx: Any, name: str) -> list[str]:
    """Modules the profile turns on or off that aren't installed."""
    modules = load(name).get("modules") or {}
    known = set(ctx.registry.all_names())
    return [f"module '{m}' is not installed" for m in sorted({*(modules.get("enable") or []),
                                                               *(modules.get("disable") or [])}) if m not in known]


def run_rc(ctx: Any, name: str) -> None:
    """The profile's rc, then its startup/*.sc files, in name order (at start and on `profile use`)."""
    from shellcraft import startup

    rc = text_file(name, "rc")
    if rc.is_file():
        startup.source(rc, ctx, label=f"profile:{name}", session=True)
    startup.run_folder(ctx, folder(name) / startup.FOLDER, label=f"profile:{name}")


def apply_config(ctx: Any, disabled_before: set[str]) -> list[str]:
    """Apply ctx.config to the running session: theme, settings, modules, prefix key."""
    from shellcraft import prefix, settings
    from shellcraft.themes import all_themes

    warnings: list[str] = []
    config = ctx.config
    if ctx.ui is not None:
        themes = all_themes(config)
        theme = themes.get(config.get("theme", "cyberpunk"))
        if theme is None:
            warnings.append(f"unknown theme '{config.get('theme')}', keeping {ctx.ui.theme.name}")
        else:
            ctx.ui.set_theme(theme)
    for setting in settings.SETTINGS.values():
        if setting.apply is not None:
            setting.apply(ctx, settings.get(config, setting.key))
    disabled = set(config.get("disabled_modules") or [])
    registry = ctx.registry
    for module in sorted(disabled_before - disabled):
        try:
            registry.enable(module)
        except KeyError:
            warnings.append(f"module '{module}' is not installed")
        except Exception as exc:  # noqa: BLE001 — enabled, but its import failed (as at startup)
            warnings.append(f"module '{module}': {type(exc).__name__}: {exc}")
    for module in sorted(disabled - disabled_before):
        try:
            registry.disable(module)
        except KeyError:
            warnings.append(f"module '{module}' is not installed")
    prefix.from_config(config)
    return warnings
