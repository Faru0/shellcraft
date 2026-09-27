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
                   help="MCP only: let pipelines fall back to system executables (off by default)")
    p.add_argument("--version", action="version", version=f"ShellCraft {__version__}")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    modules_dir = args.modules or Path(os.environ.get("SHELLCRAFT_MODULES", DEFAULT_MODULES_DIR))

    from core.loader import ModuleRegistry

    registry = ModuleRegistry(modules_dir)
    registry.load()

    if args.mcp:
        from core.mcp_server import serve

        serve(registry, allow_system=args.allow_system)
        return 0

    from core.config import load_config
    from core.context import ShellContext
    from core.themes import UI, all_themes

    config = load_config()
    themes = all_themes(config)
    theme_name = args.theme or config.get("theme", "cyberpunk")
    if theme_name not in themes:
        print(f"shellcraft: unknown theme '{theme_name}', using cyberpunk", file=sys.stderr)
        theme_name = "cyberpunk"
    ui = UI(themes[theme_name])
    ctx = ShellContext(registry=registry, ui=ui, config=config)

    if args.command is not None:
        return _run_once(args.command, ctx)

    from core.shell import Shell

    shell = Shell(ctx)
    if not args.no_banner:
        shell.banner()
    return shell.loop()


def _run_once(line: str, ctx) -> int:
    from core.context import ShellExit
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
        if isinstance(result.output, str) and not ctx.ui.console.is_terminal:
            sys.stdout.write(result.output)  # raw bytes for scripts: no wrapping or styling
        else:
            show(ctx.ui, result.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
