"""Command-line entry point: the interactive shell, or an MCP server (--mcp / --mcp-http)."""

from __future__ import annotations

import argparse
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
    p.add_argument("--mcp", action="store_true", help="run as an MCP server over stdio")
    p.add_argument("--mcp-http", metavar="HOST:PORT",
                   help="run as an MCP server over streamable HTTP at http://HOST:PORT/mcp "
                        "(this machine only, e.g. 127.0.0.1:8765)")
    p.add_argument("--modules", type=Path, metavar="DIR",
                   help="modules directory (default: $SHELLCRAFT_MODULES or the bundled modules)")
    p.add_argument("--theme", help="theme for this session (does not change the saved default)")
    p.add_argument("--no-banner", action="store_true", help="skip the startup banner")
    p.add_argument("--diag", action="store_true",
                   help="print the console diagnostics report at startup (like the diagnostics setting)")
    p.add_argument("--allow-system", action="store_true",
                   help="allow OS commands for this session (overrides the system_commands setting)")
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


def main(argv: list[str] | None = None) -> int:
    from core.stdio import utf8_stdio

    utf8_stdio()  # Windows pipes and files default to cp1252, which can't encode box drawing
    args = _parse_args(argv)
    modules_dir = args.modules or Path(os.environ.get("SHELLCRAFT_MODULES", DEFAULT_MODULES_DIR))

    from core import settings
    from core.config import load_config
    from core.loader import ModuleRegistry

    config = load_config()
    settings.export_env(config)  # API keys set with `settings NAME`, for modules in every mode

    # Modules turned off with `modules disable` are skipped in every mode, the MCP server included.
    disabled = config.get("disabled_modules")
    registry = ModuleRegistry(modules_dir, disabled if isinstance(disabled, list) else ())
    registry.load()

    if args.mcp and args.mcp_http:
        print("shellcraft: use either --mcp (stdio) or --mcp-http, not both", file=sys.stderr)
        return 2
    if (args.mcp or args.mcp_http) and not _mcp_installed():
        return 1
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

    from core.console import build_console
    from core.shell import Shell

    # The interactive shell is always on a terminal. On Linux that is stdout. On Windows, Shell
    # opens CONIN$ / CONOUT$ itself (core.console: Win32Input for keys, VT output) and rebuilds this
    # UI's Rich console on CONOUT$, whatever stdin and stdout are redirected to. Rich takes the
    # terminal size from prompt_toolkit's output, so both agree on Linux and Windows.
    ui = UI(themes[theme_name], build_console())
    ctx = ShellContext(registry=registry, ui=ui, config=config, allow_system=allow_system)
    with Shell(ctx) as shell:
        if args.diag or settings.get(config, "diagnostics"):
            shell.diagnostics()  # first, so it is there even if the banner is what breaks
        if not args.no_banner and settings.get(config, "banner"):
            shell.banner()
        return shell.loop()


if __name__ == "__main__":
    sys.exit(main())
