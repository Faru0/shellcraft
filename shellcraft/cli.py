"""Command-line entry point: the interactive shell, or an MCP server (--mcp / --mcp-http)."""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from pathlib import Path
from typing import Any

from shellcraft import __version__, timing  # timing first: it notes when startup began

# An installed copy has the modules inside the package; a source checkout has them next to shellcraft/.
_INSTALLED_MODULES = Path(__file__).resolve().parent / "bundled_modules"
DEFAULT_MODULES_DIR = (_INSTALLED_MODULES if _INSTALLED_MODULES.is_dir()
                       else Path(__file__).resolve().parent.parent / "modules")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="shellcraft", description="ShellCraft — a modular, pipe-friendly shell.",
                                epilog="shellcraft ingest [SCRIPT.py | check ...] turns a Python script into a "
                                       "module, or checks modules (shellcraft ingest --help).")
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
    p.add_argument("--no-startup", action="store_true", help="don't run the scripts in ~/.shellcraft/startup/")
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
    p.add_argument("--parent-pid", type=int, help=argparse.SUPPRESS)  # `mcp start`: exit with the shell
    p.add_argument("--brief", type=Path, metavar="FILE", help=argparse.SUPPRESS)  # an AI-opened subshell's brief
    p.add_argument("--version", action="version", version=f"ShellCraft {__version__}")
    return p.parse_args(argv)


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
    args = _parse_args(argv)
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

    if args.mcp and args.mcp_http:
        print("shellcraft: use either --mcp (stdio) or --mcp-http, not both", file=sys.stderr)
        return 2
    if (args.mcp or args.mcp_http) and not _mcp_installed():
        return 1
    if profile_name and (args.mcp or args.mcp_http):  # a server in a profile: its variables too
        for var, value in (profiles.load(profile_name).get("env") or {}).items():
            os.environ[str(var)] = profiles.plain(value)
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
                   expose=expose)
        return 0
    if args.mcp:
        from shellcraft.mcp_server import serve

        serve(registry, allow_system=args.allow_system, watch=settings.get(config, "hot_reload"), expose=expose)
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
    ctx = ShellContext(registry=registry, ui=ui, config=config, allow_system=allow_system, lite=args.lite,
                       brief=args.brief if args.lite else None)
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
        from rich.console import Console

        from shellcraft import termsize
        from shellcraft.banner import render_banner
        from shellcraft.themes import UI

        restore = termsize.enable_vt()
        try:
            # The width the shell will use (Rich alone would trust COLUMNS or fall back to 80 columns).
            console = Console(highlight=False, force_terminal=True, legacy_windows=False, color_system="truecolor",
                              width=termsize.columns())
            if clear:  # home, clear the screen and the scrollback, as `clear` does
                console.file.write("\x1b[H\x1b[2J\x1b[3J")
            console.print(render_banner(UI(theme, console), registry, warnings, profile))
            console.file.flush()
        finally:
            restore()
    except Exception:  # noqa: BLE001 — the shell prints it later, as before
        return False
    return True


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

    loading.stop()  # erased before the shell writes anything
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

                if startup.scripts():
                    shell.run_startup()  # ~/.shellcraft/startup/*.py, before the first prompt (shellcraft.startup)
                    timing.mark("startup scripts")
                if startup.rc_path().is_file():
                    shell.run_rc()  # ~/.shellcraft/rc: ShellCraft lines, as if typed (never for --mcp)
                    timing.mark("rc file")
                if profile_name:
                    profiles.run_rc(ctx, profile_name)  # then the profile's rc
                if startup.background_scripts():
                    shell.start_background()  # `# shellcraft: background` scripts: subshells, never waited for
            if ctx.brief is not None and hasattr(shell, "show_brief"):
                shell.show_brief(ctx.brief)  # what the AI that opened this subshell wants read first
        except KeyboardInterrupt:
            with contextlib.suppress(KeyboardInterrupt):
                ui.console.print(Text("^C startup interrupted; the rest of it was skipped", style="sc.warning"))
        except Exception as exc:  # noqa: BLE001
            from shellcraft import crashlog

            logged = crashlog.record(exc, "startup")
            with contextlib.suppress(KeyboardInterrupt):
                ui.console.print(Text(f"startup failed: {type(exc).__name__}: {exc}"
                                      + (f" (details in {logged})" if logged else ""), style="sc.error"))
        return shell.loop()


if __name__ == "__main__":
    sys.exit(main())
