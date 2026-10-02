"""Command-line entry point: interactive shell, one-shot (-c), or MCP server (--mcp)."""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from pathlib import Path

from core import __version__

# An installed copy has the modules inside the package; a source checkout has them next to core/.
_INSTALLED_MODULES = Path(__file__).resolve().parent / "bundled_modules"
DEFAULT_MODULES_DIR = (_INSTALLED_MODULES if _INSTALLED_MODULES.is_dir()
                       else Path(__file__).resolve().parent.parent / "modules")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="shellcraft", description="ShellCraft — a modular, pipe-friendly shell.")
    p.add_argument("-c", dest="command", metavar="CMDLINE", help="run one command line and exit")
    p.add_argument("--mcp", action="store_true", help="run as an MCP server over stdio")
    p.add_argument("--mcp-http", metavar="HOST:PORT",
                   help="run as an MCP server over streamable HTTP at http://HOST:PORT/mcp "
                        "(this machine only, e.g. 127.0.0.1:8765)")
    p.add_argument("--modules", type=Path, metavar="DIR",
                   help="modules directory (default: $SHELLCRAFT_MODULES or the bundled modules)")
    p.add_argument("--theme", help="theme for this session (does not change the saved default)")
    p.add_argument("--no-banner", action="store_true", help="skip the startup banner")
    p.add_argument("--allow-system", action="store_true",
                   help="allow OS commands for this session (overrides the system_commands setting)")
    p.add_argument("--version", action="version", version=f"ShellCraft {__version__}")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    from core.stdio import utf8_stdio

    utf8_stdio()  # Windows pipes and files default to cp1252, which can't encode box drawing
    args = _parse_args(argv)
    modules_dir = args.modules or Path(os.environ.get("SHELLCRAFT_MODULES", DEFAULT_MODULES_DIR))

    from core.loader import ModuleRegistry

    registry = ModuleRegistry(modules_dir)
    registry.load()

    from core import settings
    from core.config import load_config

    config = load_config()
    settings.export_env(config)  # API keys set with `settings NAME`, for modules in every mode

    if args.mcp and args.mcp_http:
        print("shellcraft: use either --mcp (stdio) or --mcp-http, not both", file=sys.stderr)
        return 2
    if args.mcp_http:
        from core.mcp_server import AddressError, parse_http_address, serve_http

        try:
            host, port = parse_http_address(args.mcp_http)
        except AddressError as exc:
            print(f"shellcraft: {exc}", file=sys.stderr)
            return 2
        serve_http(registry, host, port, allow_system=args.allow_system,
                   watch=settings.get(config, "hot_reload"))
        return 0
    if args.mcp:
        from core.mcp_server import serve

        serve(registry, allow_system=args.allow_system, watch=settings.get(config, "hot_reload"))
        return 0

    from core.context import ShellContext
    from core.themes import UI, all_themes

    theme_warnings: list[str] = []
    themes = all_themes(config, theme_warnings)
    for warning in theme_warnings:
        print(f"shellcraft: {warning}", file=sys.stderr)
    theme_name = args.theme or config.get("theme", "cyberpunk")
    if theme_name not in themes:
        print(f"shellcraft: unknown theme '{theme_name}', using cyberpunk", file=sys.stderr)
        theme_name = "cyberpunk"
    allow_system = args.allow_system or settings.get(config, "system_commands")

    from rich.console import Console

    from core.shell import Shell, TerminalConsole, open_windows_console
    from core.stdio import is_console

    if args.command is None:
        # The interactive shell is always on a terminal. On Windows, Shell opens the console session
        # (CONIN$ / CONOUT$ with VT sequences on) and moves this UI's Rich output there. Rich takes
        # the terminal size from prompt_toolkit's output, so both agree on Linux and Windows.
        ui = UI(themes[theme_name], TerminalConsole(highlight=False, force_terminal=True, legacy_windows=False,
                                                   color_system="truecolor"))
        ctx = ShellContext(registry=registry, ui=ui, config=config, allow_system=allow_system)
        with Shell(ctx) as shell:
            if not args.no_banner and settings.get(config, "banner"):
                shell.banner()
            return shell.loop()

    # `-c` keeps writing to stdout, so its pipes and redirects get plain text, and only needs the
    # Windows console for the pager. Elsewhere open_windows_console() is None.
    ui = UI(themes[theme_name], Console(highlight=False, force_terminal=is_console(sys.stdout)))
    ctx = ShellContext(registry=registry, ui=ui, config=config, allow_system=allow_system)
    console = open_windows_console() if is_console(sys.stdout) else None
    try:
        with console.session() if console else contextlib.nullcontext():
            return _run_once(args.command, ctx)
    finally:
        if console:
            console.close()


def _run_once(line: str, ctx) -> int:
    from core import settings
    from core.context import ShellExit, Styled, to_text
    from core.output import show, show_error
    from core.parser import ParseError
    from core.pipeline import PipelineError, run_line

    try:
        result = run_line(line, ctx)
    except ShellExit as exc:
        return exc.code
    except (ParseError, PipelineError) as exc:
        from rich.console import Console

        from core.stdio import is_console
        from core.themes import UI

        show_error(UI(ctx.ui.theme, Console(stderr=True, highlight=False, force_terminal=is_console(sys.stderr))),
                   exc, line)
        return 1
    if result is not None and result.output is not None:
        if isinstance(result.output, (str, Styled)) and not ctx.ui.console.is_terminal:
            sys.stdout.write(to_text(result.output))  # plain text for scripts: no wrapping or styling
        else:
            show(ctx.ui, result.output, pager=settings.get(ctx.config, "pager"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
