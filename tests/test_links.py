"""File names on screen are terminal hyperlinks (the `links` setting); pipes and MCP see plain text."""

import io

from rich.console import Console

from core.context import to_text
from core.pipeline import run_line
from core.themes import UI, all_themes


def _screen(ctx, line):
    out = run_line(line, ctx).output
    console = Console(file=io.StringIO(), force_terminal=True, width=100, color_system="truecolor")
    console.push_theme(ctx.ui.theme.rich_theme())
    console.print(getattr(out, "renderable", out))
    return console.file.getvalue()


def test_file_names_are_hyperlinks_on_screen_when_turned_on(ctx, tmp_path):
    (tmp_path / "a.txt").write_text("hello\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("hello\n")
    ctx.interactive = True
    ctx.ui = UI(all_themes({})["nord"], Console(file=io.StringIO()))
    uri = (tmp_path / "a.txt").resolve().as_uri()
    assert "file://" not in _screen(ctx, "ls")  # off by default
    run_line("settings links on", ctx)
    for line in ("ls", "ls -l", "ls a.txt", "tree", "find . -name a.txt", "grep -r hello .", "grep -l hello a.txt"):
        assert uri in _screen(ctx, line), line
    assert (tmp_path / "sub").resolve().as_uri() in _screen(ctx, "du")
    assert "file://" not in to_text(run_line("ls", ctx).output)  # pipes: plain names
    assert "file://" not in to_text(run_line("find . -name a.txt", ctx).output)

    run_line("settings links off", ctx)
    assert "file://" not in _screen(ctx, "ls") and "file://" not in _screen(ctx, "find .")
    run_line("settings links on", ctx)
    ctx.interactive = False  # over MCP there is no screen
    assert "file://" not in _screen(ctx, "ls")
