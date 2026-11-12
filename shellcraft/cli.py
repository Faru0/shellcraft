"""Command-line entry point: the interactive shell, or an MCP server (--mcp / --mcp-http)."""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from pathlib import Path
from typing import Any, NoReturn

from shellcraft import __version__, timing  # timing first: it notes when startup began

# An installed copy has the modules inside the package; a source checkout has them next to shellcraft/.
_INSTALLED_MODULES = Path(__file__).resolve().parent / "bundled_modules"
DEFAULT_MODULES_DIR = (_INSTALLED_MODULES if _INSTALLED_MODULES.is_dir()
                       else Path(__file__).resolve().parent.parent / "modules")


class ArgsError(Exception):
    """A bad command line. main() prints it as one `shellcraft: ...` line on stderr and returns 2,
    instead of argparse printing the usage and calling sys.exit itself."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ArgsError(message)


def _parser() -> _ArgumentParser:
    p = _ArgumentParser(prog="shellcraft", description="ShellCraft — a modular, pipe-friendly shell.",
                        formatter_class=argparse.RawDescriptionHelpFormatter,
                        epilog="shellcraft ingest [SCRIPT.py | check ...] turns a Python script into a module, or\n"
                               "  checks modules (shellcraft ingest --help).\n"
                               "shellcraft [OPTIONS] func run [-i] [-q|-v] NAME [ARG...] runs a func with no prompt\n"
                               "  (rc and startup files first, unless --no-startup) and exits with its status;\n"
                               "  shellcraft func list / show NAME / check NAME / path [NAME] work the same way.")
    p.add_argument("--mcp", action="store_true", help="run as an MCP server over stdio")
    p.add_argument("--mcp-http", metavar="HOST:PORT",
                   help="run as an MCP server over streamable HTTP at http://HOST:PORT/mcp "
                        "(this machine only, e.g. 127.0.0.1:38765)")
    p.add_argument("--modules", type=Path, metavar="DIR",
                   help="modules directory (default: $SHELLCRAFT_MODULES or the bundled modules)")
    p.add_argument("--theme", help="theme for this session (does not change the saved default)")
    p.add_argument("--profile", metavar="NAME",
                   help="start in a profile (`profile list`); every start is the default setup without it")
    p.add_argument("--no-banner", action="store_true", help="skip the startup banner")
    p.add_argument("--no-startup", action="store_true",
                   help="don't run ~/.shellcraft/rc and the files in ~/.shellcraft/startup/")
    p.add_argument("--lite", action="store_true",
                   help="a lightweight shell, what `sub shellcraft` runs inside ShellCraft: builtins, modules and "
                        "settings, without the banner, the startup scripts, the alert watcher, hot reload or "
                        "the MCP server")
    p.add_argument("--timing", action="store_true",
                   help="print how long each startup phase took, just before the first prompt")
    p.add_argument("--diag", action="store_true",
                   help="turn the diagnostics setting on (the `diag` command) and print its report at startup")
    p.add_argument("--allow-system", action="store_true",
                   help="allow OS commands for this session (overrides the system_commands setting)")
    p.add_argument("--token", metavar="TOKEN",
                   help="with --mcp-http: the bearer token clients must send (default: $SHELLCRAFT_MCP_TOKEN, "
                        "or a random one printed at startup)")
    p.add_argument("--no-token", action="store_true",
                   help="with --mcp-http: no bearer token (any local process may connect)")
    p.add_argument("--expose", metavar="NAME,...",
                   help="with --mcp / --mcp-http: offer only these modules as tools (a .skill with "
                        "`expose = false` is never offered)")
    p.add_argument("--role", metavar="NAME",
                   help="with --mcp / --mcp-http: serve only what this role may use (monitor, orchestrator, worker, "
                        "analyst, or one from ai-policy.toml); the shell checks the role again before it runs a "
                        "command")
    p.add_argument("--parent-pid", type=int, help=argparse.SUPPRESS)  # `mcp start`: exit with the shell
    p.add_argument("--brief", type=Path, metavar="FILE", help=argparse.SUPPRESS)  # an AI-opened subshell's brief
    p.add_argument("--init", action="store_true",
                   help="write the starter files (a commented rc and aliases, an example func and startup file) "
                        "into ~/.shellcraft where they are missing; never overwrites your files")
    p.add_argument("--force", action="store_true",
                   help="with --init: also refresh the .sample and README files; with --install-ai: replace "
                        "files you edited (the old text is kept as NAME.bak)")
    p.add_argument("--install-ai", nargs="*", metavar="NAME",
                   help="install the AI agents and skills (ai/) for Claude Code: the named ones, with the skills "
                        "an agent needs, or all of them; never overwrites a file you edited")
    p.add_argument("--check-ai", nargs="*", metavar="NAME",
                   help="show which AI agents and skills are installed, out of date, edited or missing")
    p.add_argument("--uninstall-ai", nargs="*", metavar="NAME",
                   help="remove the AI agents and skills that --install-ai wrote and you have not edited")
    p.add_argument("--setup-analyst", nargs="*", metavar="HOST",
                   help="set up host analysis for an AI: enable these ssh hosts (or pick from your ssh config), "
                        "test ssh, write ./.mcp.json with the analyst server, install the analyst agents, write "
                        "starter watch plans; it only adds, never rewrites what you wrote")
    p.add_argument("--check-analyst", action="store_true",
                   help="check the host-analysis setup (read-only) and say what to fix")
    p.add_argument("--sudo", action="store_true",
                   help="with --setup-analyst: set sudo = true on the hosts where `sudo -n` works")
    p.add_argument("--aggregator", metavar="ALIAS",
                   help="with --setup-analyst: also set up an aggregator box (an ssh host holding one folder of "
                        "EDR output files per device); needs --root")
    p.add_argument("--root", metavar="PATH", help="with --aggregator: the folder on the aggregator that holds the "
                                                 "device folders")
    p.add_argument("--file", action="append", metavar="KIND=GLOB",
                   help="with --aggregator: what a kind's files are called, like ps='ps_*.log' (repeat it; "
                        "beats what setup guesses from the folder)")
    p.add_argument("--device", action="append", metavar="NAME=FOLDER",
                   help="with --aggregator: a friendly name for a device folder (repeat it)")
    p.add_argument("--stale-minutes", type=int, default=30, metavar="N",
                   help="with --aggregator: a kind with no newer file than this is reported stale (default 30)")
    p.add_argument("--watch-rounds", metavar="PLAN",
                   help="run one round of a watch plan now if one is due, with no prompt and nobody at the "
                        "keyboard: the AI checks the plan's hosts and feeds, and its alerts wait for your next "
                        "shell; one line per run goes to ~/.shellcraft/rounds.log (--dry-run shows the command)")
    p.add_argument("--budget", type=float, metavar="USD",
                   help="with --watch-rounds and --install-timer: what one round may cost (default: 2)")
    p.add_argument("--day-budget", type=float, metavar="USD",
                   help="with --watch-rounds and --install-timer: what all rounds of a day may cost; "
                        "when it is used up no round starts until tomorrow (default: 10, 0: no limit)")
    p.add_argument("--install-timer", metavar="PLAN",
                   help="install a systemd user timer that runs --watch-rounds PLAN every --every minutes")
    p.add_argument("--every", type=int, metavar="MIN",
                   help="with --install-timer: minutes between rounds (default: the plan's interval_minutes)")
    p.add_argument("--remove-timer", metavar="PLAN", help="remove the timer --install-timer made")
    p.add_argument("--user", action="store_true", help="with --install-ai: install into ~/.claude (every project)")
    p.add_argument("--project", action="store_true", help="with --install-ai: install into ./.claude (this folder)")
    p.add_argument("--team", action="store_true",
                   help="with --install-ai: the orchestrator, worker and monitor agents and their skills")
    p.add_argument("--skills-only", action="store_true", help="with --install-ai: only the skills")
    p.add_argument("--agents-only", action="store_true", help="with --install-ai: only the agents")
    p.add_argument("--dry-run", action="store_true",
                   help="with the AI flags, --watch-rounds and the timer flags: show what would happen, change nothing")
    p.add_argument("--version", action="version", version=f"ShellCraft {__version__}")
    return p


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    return _parser().parse_args(argv)


def _split_func(argv: list[str]) -> tuple[list[str], list[str]] | None:
    """`shellcraft [OPTIONS] func ACTION ...`: the options before `func` and the words after it, or None
    when the command line has no `func` (a `func` that is an option's value, `--profile func`, doesn't count)."""
    options = _parser()._option_string_actions
    takes_value = {name for name, action in options.items() if action.nargs != 0}
    i = 0
    while i < len(argv):
        word = argv[i]
        if word == "func":
            return argv[:i], argv[i + 1:]
        if not word.startswith("-") or word == "--":
            return None  # a word argparse will refuse anyway
        if "=" not in word:
            # argparse takes a unique prefix too (--prof work)
            matches = [name for name in options if name.startswith(word)] if word.startswith("--") else [word]
            if word in options:
                matches = [word]
            if len(matches) == 1 and matches[0] in takes_value:
                i += 1  # its value
        i += 1
    return None


def _init(force: bool) -> int:
    """`shellcraft --init`: the starter files, and what happened to each."""
    from shellcraft import skel
    from shellcraft.config import config_dir

    try:
        report = skel.init(force=force)
    except OSError as exc:
        print(f"shellcraft: --init: {exc}", file=sys.stderr)
        return 1
    home = config_dir()
    for rel, what in report:
        print(f"{what:<9}  {home / rel}")
    print("Nothing in rc or aliases runs until you remove a #; rename a .sample file to use it.")
    return 0


def _ai_target(args: argparse.Namespace) -> Path | None:
    """--user / --project, else ask on a terminal; None (after a message) when there is no answer."""
    if args.user and args.project:
        print("shellcraft: use either --user or --project, not both", file=sys.stderr)
        return None
    if args.user or args.project:
        return Path.home() / ".claude" if args.user else Path.cwd() / ".claude"
    if not sys.stdin.isatty():
        print("shellcraft: say where with --user (~/.claude) or --project (./.claude)", file=sys.stderr)
        return None
    try:
        answer = input(f"Use it in [u]ser mode, every project ({Path.home() / '.claude'}), "
                       f"or only the [p]roject here ({Path.cwd() / '.claude'})? [u/p] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        return None
    if answer[:1] not in ("u", "p"):
        print("shellcraft: nothing installed (answer u or p)", file=sys.stderr)
        return None
    return Path.home() / ".claude" if answer[0] == "u" else Path.cwd() / ".claude"


def _ai(args: argparse.Namespace) -> int:
    """`shellcraft --install-ai | --check-ai | --uninstall-ai`: the AI agents and skills, and what happened to each."""
    from shellcraft import ai_install

    install = args.install_ai is not None
    check = args.check_ai is not None
    names = args.install_ai if install else args.check_ai if check else args.uninstall_ai
    if sum(x is not None for x in (args.install_ai, args.check_ai, args.uninstall_ai)) > 1:
        print("shellcraft: use one of --install-ai, --check-ai and --uninstall-ai", file=sys.stderr)
        return 2
    target = _ai_target(args)
    if target is None:
        return 2
    try:
        items = ai_install.select(names, args.team, args.skills_only, args.agents_only)
        if install:
            results = ai_install.install(items, target, force=args.force, dry_run=args.dry_run)
        elif check:
            results = ai_install.check(items, target)
        else:
            results = ai_install.uninstall(items, target, dry_run=args.dry_run)
    except (ai_install.InstallError, OSError) as exc:
        print(f"shellcraft: {exc}", file=sys.stderr)
        return 1
    prefix = "would: " if args.dry_run else ""
    for r in results:
        print(f"{prefix}{r.status:<9}  {target / r.rel}")
    edited = [r for r in results if r.status == "edited"]
    for r in edited:
        print(f"\n{r.diff}" if r.diff else "", end="")
    if edited:
        print(f"\n{len(edited)} file(s) differ from what the installer wrote and were left alone; "
              "--force replaces them (the old text is kept as NAME.bak).")
    return 0


def _analyst(args: argparse.Namespace) -> int:
    """`shellcraft --setup-analyst [HOST ...]` and `--check-analyst`: host analysis, set up and checked."""
    from shellcraft import analyst_setup

    if args.setup_analyst is not None and args.check_analyst:
        print("shellcraft: use either --setup-analyst or --check-analyst", file=sys.stderr)
        return 2
    check = args.check_analyst
    target = _analyst_target(args, check)
    if target is None:
        return 2
    try:
        if check:
            lines = analyst_setup.doctor(analyst_setup.project_dir(), target)
        else:
            lines = analyst_setup.setup(args.setup_analyst, sudo=args.sudo, aggregator=args.aggregator,
                                        root=args.root, target=target, project=analyst_setup.project_dir(),
                                        force=args.force, dry_run=args.dry_run, file_args=args.file,
                                        device_args=args.device, stale_minutes=args.stale_minutes)
    except (analyst_setup.SetupError, OSError) as exc:
        print(f"shellcraft: {exc}", file=sys.stderr)
        return 1
    prefix = "would: " if args.dry_run and not check else ""
    for line in lines:
        print(prefix + line.render())
    return analyst_setup.exit_code(lines) if check else (1 if any(x.status == "fix" for x in lines) else 0)


def _rounds(args: argparse.Namespace, registry: Any) -> int:
    """`shellcraft --watch-rounds PLAN`, `--install-timer PLAN` and `--remove-timer PLAN`."""
    from shellcraft import rounds

    budget = rounds.DEFAULT_BUDGET if args.budget is None else args.budget
    day_budget = rounds.DEFAULT_DAY_BUDGET if args.day_budget is None else args.day_budget
    if args.remove_timer:
        return rounds.remove_timer(args.remove_timer, dry_run=args.dry_run)
    if args.install_timer:
        return rounds.install_timer(registry, args.install_timer, every=args.every, budget=budget,
                                    day_budget=day_budget, dry_run=args.dry_run)
    _interrupts_as_usual()  # Ctrl-C ends the round
    return rounds.run_round(registry, args.watch_rounds, budget=budget, day_budget=day_budget, dry_run=args.dry_run)


def _analyst_target(args: argparse.Namespace, check: bool) -> Path | None:
    """Where the agents go: --user, --project, or (for the check) the folder that has them."""
    if args.user or args.project or not check:
        return _ai_target(args)
    project, user = Path.cwd() / ".claude", Path.home() / ".claude"
    return project if (project / "agents").is_dir() or not (user / "agents").is_dir() else user


def _mcp_installed() -> bool:
    """The MCP server needs the `mcp` package (the shell doesn't); say how to get it, not a traceback."""
    import importlib.util

    missing = [name for name in ("mcp", "mcp_types") if importlib.util.find_spec(name) is None]
    if missing:
        print(f"shellcraft: the MCP server needs the 'mcp' package, which this Python doesn't have "
              f"(missing: {', '.join(missing)}).\n"
              f"  Python: {sys.executable}\n"
              f"  Install ShellCraft's requirements into it:  {sys.executable} -m pip install -r requirements.txt\n"
              f"  (or into a virtual environment)", file=sys.stderr)
    return not missing


def _enable_diagnostics(config: dict) -> None:
    """`--diag`: the diagnostics setting goes on and stays on (the `diag` command); `settings diagnostics off`
    hides it again."""
    from shellcraft import settings
    from shellcraft.config import save_config

    if settings.get(config, "diagnostics"):
        return
    settings.set_value(config, "diagnostics", True)
    try:
        save_config(config)
    except OSError:
        pass  # on for this session all the same


def main(argv: list[str] | None = None) -> int:
    from shellcraft.stdio import utf8_stdio

    utf8_stdio()  # Windows pipes and files default to cp1252, which can't encode box drawing
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["ingest"]:  # `shellcraft ingest ...`: the module wizard, with this shell's modules folder
        from tools.ingest.__main__ import main as ingest_main

        return ingest_main(argv[1:], prog="shellcraft ingest")  # new modules go to ~/.shellcraft/modules
    split = _split_func(argv)  # `shellcraft [OPTIONS] func run NAME ...`: a func, with no prompt
    func_words = split[1] if split is not None else None
    try:
        args = _parse_args(split[0] if split is not None else argv)
    except ArgsError as exc:
        print(f"shellcraft: {exc} (shellcraft --help lists the options)", file=sys.stderr)
        return 2
    ai_mode = any(x is not None for x in (args.install_ai, args.check_ai, args.uninstall_ai))
    analyst_mode = args.setup_analyst is not None or args.check_analyst
    if analyst_mode and (ai_mode or args.mcp or args.mcp_http or args.init or args.role or args.expose
                         or func_words is not None):
        print("shellcraft: --setup-analyst and --check-analyst go without --mcp, --mcp-http, --init, --role, "
              "--expose, func and the other AI flags", file=sys.stderr)
        return 2
    if analyst_mode:
        return _analyst(args)
    rounds_mode = [name for name in ("watch_rounds", "install_timer", "remove_timer") if getattr(args, name)]
    if rounds_mode and (len(rounds_mode) > 1 or ai_mode or args.mcp or args.mcp_http or args.init or args.role
                        or args.expose or func_words is not None):
        print("shellcraft: --watch-rounds, --install-timer and --remove-timer go one at a time, and without "
              "--mcp, --mcp-http, --init, --role, --expose, func and the AI flags", file=sys.stderr)
        return 2
    if func_words is not None and (args.mcp or args.mcp_http or args.init or args.role or args.expose or ai_mode):
        print("shellcraft: func goes without --mcp, --mcp-http, --init, --role, --expose and the AI flags",
              file=sys.stderr)
        return 2
    if ai_mode and (args.mcp or args.mcp_http or args.init or args.role or args.expose):
        print("shellcraft: the AI install flags go without --mcp, --mcp-http, --init, --role and --expose",
              file=sys.stderr)
        return 2
    if ai_mode:
        return _ai(args)
    if args.init:
        return _init(args.force)
    if args.timing:
        os.environ[timing.ENV] = "1"  # subshells started from this shell report theirs too
    timing.mark("arguments")
    modules_dir = args.modules or Path(os.environ.get("SHELLCRAFT_MODULES", DEFAULT_MODULES_DIR))

    from shellcraft import settings
    from shellcraft.config import load_config
    from shellcraft.loader import ModuleRegistry

    config = load_config()
    try:
        from shellcraft import aliases
        from shellcraft.config import save_config

        if aliases.migrate(config):  # aliases saved in config.json by older versions → ~/.shellcraft/aliases
            save_config(config)
    except OSError:
        pass  # they keep working from config.json
    if not (args.mcp or args.mcp_http or args.lite or func_words is not None):
        from shellcraft import skel

        skel.seed(config)  # the commented rc and aliases and the examples, each written once (`--init` again)
    # A profile (--profile, or the one the parent ShellCraft runs in) layers its config on the base.
    from shellcraft import profiles

    profile_name = args.profile or os.environ.get(profiles.ENV_VAR) or None
    if profile_name == profiles.DEFAULT:
        profile_name = None
    if profile_name:
        try:
            config = profiles.effective(config, profile_name)
        except profiles.ProfileError as exc:
            print(f"shellcraft: {exc}", file=sys.stderr)
            return 2
        os.environ[profiles.ENV_VAR] = profile_name
    else:
        os.environ.pop(profiles.ENV_VAR, None)
    settings.export_env(config)  # API keys set with `settings NAME`, for modules in every mode
    from shellcraft import prefix

    prefix.from_config(config)  # the subshell prefix key (`settings prefix_key`)

    # Modules turned off with `modules disable` are skipped in every mode, the MCP server included.
    disabled = config.get("disabled_modules")
    from shellcraft.config import config_dir

    registry = ModuleRegistry(modules_dir, disabled if isinstance(disabled, list) else (),
                              user_directory=config_dir() / "modules",
                              lazy=settings.get(config, "lazy_modules"))
    registry.load()
    timing.mark("modules found")
    expose = [n.strip() for n in args.expose.split(",") if n.strip()] if args.expose else None
    if args.parent_pid:
        from shellcraft.mcp_child import watch_parent

        watch_parent(args.parent_pid)

    if rounds_mode:
        return _rounds(args, registry)
    if func_words is not None:
        from shellcraft import func_cli

        return func_cli.main(func_words, args, config, registry, profile_name)
    if args.mcp and args.mcp_http:
        print("shellcraft: use either --mcp (stdio) or --mcp-http, not both", file=sys.stderr)
        return 2
    if (args.mcp or args.mcp_http) and not _mcp_installed():
        return 1
    if profile_name and (args.mcp or args.mcp_http):  # a server in a profile: its variables too
        for var, value in (profiles.load(profile_name).get("env") or {}).items():
            os.environ[str(var)] = profiles.plain(value)
    if args.role and not (args.mcp or args.mcp_http):
        print("shellcraft: --role goes with --mcp or --mcp-http", file=sys.stderr)
        return 2
    if args.role:
        from shellcraft import ai_policy

        known = ai_policy.load().roles
        if args.role not in known:
            print(f"shellcraft: unknown role '{args.role}' (known: {', '.join(sorted(known))})", file=sys.stderr)
            return 2
    if args.mcp or args.mcp_http:
        _interrupts_as_usual()
    if args.mcp_http:
        from shellcraft.mcp_server import AddressError, parse_http_address, serve_http

        try:
            host, port = parse_http_address(args.mcp_http)
        except AddressError as exc:
            print(f"shellcraft: {exc}", file=sys.stderr)
            return 2
        from shellcraft.mcp_server import resolve_token

        serve_http(registry, host, port, allow_system=args.allow_system,
                   watch=settings.get(config, "hot_reload"), token=resolve_token(args.token, args.no_token),
                   expose=expose, role=args.role)
        return 0
    if args.mcp:
        from shellcraft.mcp_server import serve

        serve(registry, allow_system=args.allow_system, watch=settings.get(config, "hot_reload"), expose=expose,
              role=args.role)
        return 0

    from shellcraft.themes import all_themes

    theme_warnings: list[str] = []
    themes = all_themes(config, theme_warnings)
    theme_name = args.theme or config.get("theme", "cyberpunk")
    sub_theme = config.get("sub_theme")
    if args.lite and not args.theme and sub_theme:  # `theme --sub NAME`: the theme subshells start with
        if sub_theme in themes:
            theme_name = sub_theme
        else:
            theme_warnings.append(f"unknown subshell theme '{sub_theme}', using the main theme")
    if not isinstance(theme_name, str) or theme_name not in themes:
        theme_warnings.append(f"unknown theme '{theme_name}', using cyberpunk")
        theme_name = "cyberpunk"
    allow_system = args.allow_system or settings.get(config, "system_commands")
    show_banner = not args.no_banner and not args.lite and settings.get(config, "banner")
    if not show_banner and not args.lite:  # with the banner, the warnings are shown inside it
        from shellcraft.banner import core_warnings

        for warning in [*core_warnings(), *theme_warnings]:
            print(f"shellcraft: {warning}", file=sys.stderr)

    timing.mark("config and theme")
    early = show_banner and not args.diag and _early_banner(themes[theme_name], registry, theme_warnings,
                                                            profile_name, settings.get(config, "clear_on_start"))
    if early:
        timing.mark("banner")
    from shellcraft import loading

    loading.start(loading.style(config), themes[theme_name])  # until the shell takes the terminal
    try:
        return _start_shell(args, config, registry, themes[theme_name], theme_warnings, profile_name, profiles,
                            allow_system, show_banner, early)
    finally:
        loading.stop()


def _start_shell(args: Any, config: Any, registry: Any, theme: Any, theme_warnings: list[str],
                 profile_name: str | None, profiles: Any, allow_system: bool, show_banner: bool, early: bool) -> int:
    """Load prompt_toolkit and the shell (the slow part, under the loading line), then run it."""
    from shellcraft.console import build_console
    from shellcraft.context import ShellContext
    from shellcraft.themes import UI

    timing.mark("prompt_toolkit imported")
    from shellcraft.shell import Shell

    timing.mark("prompt_toolkit + shell imported")

    # The interactive shell is always on a terminal. On Linux that is stdout. On Windows, Shell
    # opens CONIN$ / CONOUT$ itself (shellcraft.console: Win32Input for keys, VT output) and rebuilds this
    # UI's Rich console on CONOUT$, whatever stdin and stdout are redirected to. Rich takes the
    # terminal size from prompt_toolkit's output, so both agree on Linux and Windows.
    ui = UI(theme, build_console())
    from shellcraft import settings

    ctx = ShellContext(registry=registry, ui=ui, config=config, allow_system=allow_system, lite=args.lite,
                       brief=args.brief if args.lite else None,
                       ai_run=not args.lite and settings.get(config, "ai_access"))
    from shellcraft import crashlog

    crashlog.install_thread_hook()  # a background thread's error goes to the log, not over the prompt
    _ctrl_break_is_ctrl_c()
    try:
        return _interactive(Shell, ctx, ui, config, args, show_banner and not early, theme_warnings, profile_name,
                            profiles, cleared=early)
    except Exception as exc:  # noqa: BLE001 — the last resort: one line, the details in the log
        logged = crashlog.record(exc, "the interactive shell")
        print(f"shellcraft: unexpected error: {type(exc).__name__}: {exc}"
              + (f" (details in {logged})" if logged else ""), file=sys.stderr)
        return 1


def _early_banner(theme: Any, registry: Any, warnings: list[str], profile: str | None, clear: bool) -> bool:
    """Print the banner before prompt_toolkit and the shell are imported (most of the start-up time),
    so something is on screen at once. Only on a terminal; False when it wasn't printed here."""
    if not sys.stdout.isatty():
        return False
    try:
        from shellcraft import termsize
        from shellcraft.banner import render_banner
        from shellcraft.console import build_console
        from shellcraft.themes import UI

        restore = termsize.enable_vt()
        try:
            # The width the shell will use (Rich alone would trust COLUMNS or fall back to 80 columns).
            console = build_console(width=termsize.columns())
            if clear:  # home, clear the screen and the scrollback, as `clear` does
                console.file.write("\x1b[H\x1b[2J\x1b[3J")
            console.print(render_banner(UI(theme, console), registry, warnings, profile))
            console.file.flush()
        finally:
            restore()
    except Exception:  # noqa: BLE001 — the shell prints it later, as before
        return False
    return True


def _interrupts_as_usual() -> None:
    """Python's default Ctrl-C handling (KeyboardInterrupt), after main.py's quiet startup exit."""
    import signal

    try:
        signal.signal(signal.SIGINT, signal.default_int_handler)
    except (ValueError, OSError):
        pass


def _ctrl_break_is_ctrl_c() -> None:
    """Windows: Ctrl-Break would end the process (Python's default); make it a Ctrl-C."""
    import signal

    number = getattr(signal, "SIGBREAK", None)
    if number is not None:
        try:
            signal.signal(number, signal.default_int_handler)
        except (ValueError, OSError):
            pass


def _interactive(Shell: Any, ctx: Any, ui: Any, config: Any, args: Any, show_banner: bool,
                 theme_warnings: list[str], profile_name: str | None, profiles: Any, cleared: bool = False) -> int:
    from rich.text import Text

    from shellcraft import loading, settings
    from shellcraft.context import ShellExit

    loading.stop()  # erased before the shell writes anything
    _interrupts_as_usual()  # from here Ctrl-C is a KeyboardInterrupt again (main.py made it a quiet exit)
    with Shell(ctx) as shell:
        timing.mark("shell set up")
        # Only here, never before argument parsing or in --mcp mode: a clear sequence on the MCP
        # stdio stream is garbage to the client, and `--version` must print only the version.
        try:  # startup: Ctrl-C (or a bug) skips the rest of it, and the prompt still comes
            if profile_name and not args.lite:  # its variables, recorded so `profile use` can undo them
                for warning in profiles.enter_env(ctx, profile_name) + profiles.missing_modules(ctx, profile_name):
                    theme_warnings.append(f"profile {profile_name}: {warning}")
            if settings.get(config, "clear_on_start") and ui.console.is_terminal and not args.lite and not cleared:
                # Home, clear the screen and the scrollback (ESC[3J): the terminal starts fresh, as
                # `clear` on Linux and Windows Terminal do, instead of leaving the old lines above.
                from shellcraft.output import clear_screen

                clear_screen(ui)
            if args.diag:
                _enable_diagnostics(config)
                shell.diagnostics()  # first, so it is there even if the banner is what breaks
            if show_banner:
                shell.banner(theme_warnings)
                timing.mark("banner")
            if not args.no_startup and not args.lite and settings.get(config, "startup"):
                from shellcraft import startup

                if startup.rc_path().is_file():
                    shell.run_rc()  # ~/.shellcraft/rc: ShellCraft lines, as if typed (never for --mcp)
                    timing.mark("rc file")
                if startup.scripts(startup.folder()):
                    startup.run_folder(ctx, startup.folder())  # then ~/.shellcraft/startup/*.sc, like ~/.bashrc.d
                    timing.mark("startup files")
                if profile_name:
                    profiles.run_rc(ctx, profile_name)  # then the profile's rc and its startup/*.sc
            if ctx.brief is not None and hasattr(shell, "show_brief"):
                shell.show_brief(ctx.brief)  # what the AI that opened this subshell wants read first
        except KeyboardInterrupt:
            with contextlib.suppress(KeyboardInterrupt):
                ui.console.print(Text("^C startup interrupted; the rest of it was skipped", style="sc.warning"))
        except ShellExit as exc:  # `exit N` in the rc file or a startup script: ShellCraft ends there
            return int(shell._goodbye(exc.code))
        except Exception as exc:  # noqa: BLE001
            from shellcraft import crashlog

            logged = crashlog.record(exc, "startup")
            with contextlib.suppress(KeyboardInterrupt):
                ui.console.print(Text(f"startup failed: {type(exc).__name__}: {exc}"
                                      + (f" (details in {logged})" if logged else ""), style="sc.error"))
        return shell.loop()


if __name__ == "__main__":
    sys.exit(main())
