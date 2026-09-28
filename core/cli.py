"""Command-line entry point: interactive shell, one-shot (-c), or MCP server (--mcp)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from core import __version__

DEFAULT_MODULES_DIR = Path(__file__).resolve().parent.parent / "modules"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="shellcraft", description="ShellCraft — a modular, pipe-friendly shell.")
    p.add_argument("-c", dest="command", metavar="CMDLINE", help="run one command line and exit")
    p.add_argument("--mcp", action="store_true", help="run as an MCP server over stdio")
    p.add_argument("--modules", type=Path, metavar="DIR",
                   help="modules directory (default: $SHELLCRAFT_MODULES or ./modules next to main.py)")
    p.add_argument("--theme", help="theme for this session (does not change the saved default)")
    p.add_argument("--no-banner", action="store_true", help="skip the startup banner")
    p.add_argument("--allow-system", action="store_true",
                   help="allow OS commands for this session (overrides the system_commands setting)")
    p.add_argument("--version", action="version", version=f"ShellCraft {__version__}")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    modules_dir = args.modules or Path(os.environ.get("SHELLCRAFT_MODULES", DEFAULT_MODULES_DIR))

    from core.loader import ModuleRegistry

    registry = ModuleRegistry(modules_dir)
    registry.load()

    from core import settings
    from core.config import load_config

    config = load_config()
    settings.export_env(config)  # API keys set with `settings NAME`, for modules in every mode

    if args.mcp:
        from core.mcp_server import serve

        serve(registry, allow_system=args.allow_system)
        return 0

    from core.context import ShellContext
    from core.themes import UI, all_themes

    themes = all_themes(config)
    theme_name = args.theme or config.get("theme", "cyberpunk")
    if theme_name not in themes:
        print(f"shellcraft: unknown theme '{theme_name}', using cyberpunk", file=sys.stderr)
        theme_name = "cyberpunk"
    ui = UI(themes[theme_name])
    ctx = ShellContext(registry=registry, ui=ui, config=config,
                       allow_system=args.allow_system or settings.get(config, "system_commands"))

    if args.command is not None:
        return _run_once(args.command, ctx)

    from core.shell import Shell

    shell = Shell(ctx)
    if not args.no_banner and settings.get(config, "banner"):
        shell.banner()
    return shell.loop()


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

        from core.themes import UI

        show_error(UI(ctx.ui.theme, Console(stderr=True, highlight=False)), exc, line)
        return 1
    if result is not None and result.output is not None:
        if isinstance(result.output, (str, Styled)) and not ctx.ui.console.is_terminal:
            sys.stdout.write(to_text(result.output))  # plain text for scripts: no wrapping or styling
        else:
            show(ctx.ui, result.output, pager=settings.get(ctx.config, "pager"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
