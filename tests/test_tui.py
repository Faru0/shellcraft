"""The TUI pieces: the input-line lexer, the toolbar, prompt segments, the pager's keys, `show()`'s
threshold, themes (persistence, preview, custom colors, presets), the banner, completion."""

import io
import json
from types import SimpleNamespace

import pytest
from prompt_toolkit.application.current import create_app_session
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from shellcraft import shell
from shellcraft.context import ShellContext, to_text
from shellcraft.highlight import highlight
from shellcraft.loader import ModuleRegistry
from shellcraft.output import page, show, spinner_label
from shellcraft.themes import PRESETS, UI, all_themes


def _ui(width=100, height=10, record=False):
    console = Console(file=io.StringIO(), force_terminal=True, width=width, height=height, record=record)
    return UI(all_themes({})["cyberpunk"], console)


# ── syntax highlighting ──────────────────────────────────────────────────────

def _styles(line, known=("ls", "grep", "curl")):
    return [(style, text) for style, text in highlight(line, lambda n: n in known) if text.strip()]


def test_lexer_colors_commands_operators_strings_and_switches():
    out = _styles('ls -l "my dir" | grep -i x > out.txt ; nosuch $x')
    assert ("class:cmd.known", "ls") in out and ("class:switch", "-l") in out
    assert ("class:string", '"my dir"') in out and ("class:operator", "|") in out
    assert ("class:cmd.known", "grep") in out and ("class:operator", ">") in out
    assert ("class:cmd.unknown", "nosuch") in out and ("class:variable", "$x") in out


def test_lexer_knows_keywords_assignments_and_comments():
    out = _styles("for f in *.log; do grep -q x $f && echo $f; done")
    assert ("class:keyword", "for") in out and ("class:keyword", "do") in out and ("class:keyword", "done") in out
    assert ("class:cmd.known", "grep") in out and ("class:cmd.unknown", "echo") in out
    out = _styles("x=1 # a comment")
    assert any(style == "class:variable" for style, _ in out) and ("class:comment", "# a comment") in out
    out = _styles(r"\ls a=1&b=2")
    assert ("class:cmd.known", "\\ls") in out and ("", "a=1&b=2") in out  # & inside a word is text


# ── prompt segments, toolbar ─────────────────────────────────────────────────

@pytest.fixture
def repl(monkeypatch, tmp_path):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shell, "create_prompt_toolkit_console", lambda cleanup: None)
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    ui = UI(all_themes({})["nord"], console)
    ctx = ShellContext(registry=ModuleRegistry(tmp_path), ui=ui, config={})
    with shell.Shell(ctx) as sh:
        yield sh, ctx


def test_prompt_segments_follow_the_prompt_setting(repl, tmp_path):
    sh, ctx = repl
    texts = lambda fragments: [f[1] for f in fragments]  # noqa: E731
    assert " ⚡shellcraft " in texts(sh._prompt()) and sh.user_host in texts(sh._prompt())
    ctx.config["prompt"] = {"left": ["name", "path", "status"], "right": ["clock"]}
    assert sh.user_host not in texts(sh._prompt())
    ctx.last_status = 2
    assert "[✗ 2]" in texts(sh._prompt())
    assert sh._rprompt() is not None and len(texts(sh._rprompt())[0]) == 5  # HH:MM
    sh.last_duration = 3.2
    assert "⏱ 3.2s" not in texts(sh._prompt())  # not in the chosen segments
    ctx.config["prompt"] = {"left": ["duration"]}
    assert texts(sh._prompt())[:3] == ["╭─", " ", "⏱ 3.2s"]
    sh.last_duration = 0.5
    assert "⏱" not in "".join(texts(sh._prompt()))


def test_prompt_shows_the_git_branch(repl, tmp_path):
    sh, ctx = repl
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/feature/x\n")
    from shellcraft import gitinfo

    gitinfo._cache.clear()
    assert ("class:git", "⎇ feature/x") in [f[:2] for f in sh._prompt()]
    (tmp_path / ".git" / "HEAD").write_text("0123456789abcdef\n")
    gitinfo._cache.clear()
    assert ("class:git", "⎇ 0123456") in [f[:2] for f in sh._prompt()]
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {tmp_path / '.git'}\n")
    assert gitinfo.branch(worktree) == "0123456"


def test_prompt_builtin(repl, tmp_path):
    from shellcraft.context import to_text
    from shellcraft.pipeline import PipelineError, run_line

    sh, ctx = repl
    assert "prompt name profile user path git mcp status duration" in to_text(run_line("prompt", ctx).output)
    run_line("prompt name path", ctx)
    assert ctx.config["prompt"] == {"left": ["name", "path"]}
    run_line("prompt -r clock", ctx)
    assert ctx.config["prompt"]["right"] == ["clock"]
    assert json.loads((tmp_path / "home" / "config.json").read_text())["prompt"]["left"] == ["name", "path"]
    with pytest.raises(PipelineError, match="unknown segment"):
        run_line("prompt name bogus", ctx)
    run_line("prompt reset", ctx)
    assert "prompt" not in ctx.config


def test_mouse_only_while_a_completion_menu_is_open(repl):
    """The prompt asks for mouse reporting only with a menu open and the setting on; the pager
    follows the setting too."""
    from prompt_toolkit.buffer import CompletionState

    from shellcraft.pipeline import run_line

    sh, ctx = repl
    buffer = sh.session.default_buffer
    assert sh.session.app.mouse_support() is False  # no menu: the terminal keeps the mouse
    buffer.complete_state = CompletionState(buffer.document, [])
    assert sh.session.app.mouse_support() is True
    run_line("settings mouse off", ctx)
    assert sh.ui.mouse is False and sh.session.app.mouse_support() is False
    run_line("settings mouse on", ctx)
    assert sh.session.app.mouse_support() is True
    buffer.complete_state = None
    assert sh.session.app.mouse_support() is False


def test_mouse_always_clicks_segments_toolbar_and_wheels_history(repl, monkeypatch):
    """With `mouse_always` the mouse stays on; a click on a segment runs its command above the
    prompt, the status segment shows the last error again, the toolbar opens the man page and
    the wheel over the input line feeds ↑ / ↓."""
    from prompt_toolkit.data_structures import Point
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType

    from shellcraft import shell as shell_module
    from shellcraft.pipeline import run_line

    sh, ctx = repl
    run_line("settings mouse_always on", ctx)
    assert sh.ui.mouse_always and sh.session.app.mouse_support() is True
    monkeypatch.setattr(shell_module, "run_in_terminal", lambda func: func())  # no event loop here
    up = MouseEvent(Point(0, 0), MouseEventType.MOUSE_UP, MouseButton.LEFT, frozenset())
    down = MouseEvent(Point(0, 0), MouseEventType.MOUSE_DOWN, MouseButton.LEFT, frozenset())
    name = next(f for f in sh._prompt() if f[1] == " ⚡shellcraft ")
    assert name[2](down) is NotImplemented  # only the release counts
    name[2](up)
    out = sh.ui.console.export_text()
    assert "❯ help" in out and "⚡ShellCraft" in out and "commands ·" in out

    ctx.last_status = 127
    sh.last_error = (RuntimeError("boom"), "nosuch")
    status = next(f for f in sh._prompt() if f[0] == "class:failed")
    status[2](up)
    assert "boom" in sh.ui.console.export_text()

    control = sh.session.layout.current_control  # the input line
    control.mouse_handler(MouseEvent(Point(0, 0), MouseEventType.SCROLL_UP, MouseButton.NONE, frozenset()))
    assert sh.session.app.key_processor.input_queue[0].key == Keys.Up

    sh.session.default_buffer = SimpleNamespace(text="grep -i")  # the toolbar reads the text
    key = sh._toolbar()[0]
    assert key[1].strip() == "grep" and key[2](up) is None
    assert "❯ man grep" in sh.ui.console.export_text()

    run_line("settings mouse_always off", ctx)
    assert sh.ui.mouse_always is False


def test_toolbar_shows_usage_or_not_found(repl):
    from types import SimpleNamespace

    sh, ctx = repl
    buffer = sh.session.default_buffer = SimpleNamespace(text="")  # no event loop in a test: set the text by hand
    text = lambda: "".join(f[1] for f in sh._toolbar())  # noqa: E731

    def typed(line):
        buffer.text = line
        sh._update_toolbar()

    typed("grep -i")
    assert "grep [-i" in text() and sh.session.bottom_toolbar is not None
    sh._reset_input()  # the next prompt: the old text is gone, and so is the toolbar
    assert sh.session.default_buffer.text == "" and sh.session.bottom_toolbar is None
    sh.session.default_buffer.text = "grep -i"
    sh._update_toolbar()
    typed("nosuch x")
    assert "command not found" in text()
    typed("gre")
    assert text().strip() == ""  # still typing the name
    typed("")
    assert sh.session.bottom_toolbar is None
    ctx.config["settings"] = {"toolbar": False}
    typed("grep")
    assert sh.session.bottom_toolbar is None
    assert sh._current_lexer() is not None
    ctx.config["settings"]["highlight"] = False
    assert sh._current_lexer() is None


def test_transient_prompt_reprints_the_command(repl):
    sh, ctx = repl
    ctx.config["settings"] = {"transient_prompt": True}
    seen = {}

    class Session:
        bottom_toolbar = None

        class app:
            erase_when_done = False

        def prompt(self, message):
            seen["erase"] = self.app.erase_when_done
            return "echo hi"

    sh.session = Session()
    assert sh._ask(None) == "echo hi" and seen["erase"] is True
    assert "❯ echo hi" in sh.ui.console.export_text()


# ── the pager ────────────────────────────────────────────────────────────────

def _page(keys, lines=60, width=40, title=None):
    text = "\n".join(f"line {i} " + "x" * (i % 3 * 30) for i in range(lines))
    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        output = DummyOutput()
        output.get_size = lambda: __import__("prompt_toolkit").data_structures.Size(rows=11, columns=width)
        with create_app_session(input=pipe, output=output):
            return page(_ui(), text, title)


def _click(col, row):
    return f"\x1b[<0;{col};{row}M\x1b[<0;{col};{row}m"  # SGR press + release, 1-based


def test_pager_mouse_scrollbar_mark_wheel_and_status_keys():
    from shellcraft.output import PAGER_HINTS

    # The scrollbar is the last column: its last row is the end of the text, its first the top.
    assert _page(_click(100, 10) + "q", width=100)["top"] == 50
    assert _page(_click(100, 10) + _click(100, 1) + "q", width=100)["top"] == 0
    # A click on a body row marks that line for n / N.
    state = _page(_click(1, 3) + "q", width=100)
    assert state["match"] == 2 and "line 3 marked" in state["message"]
    # The wheel over the body scrolls three lines.
    assert _page("\x1b[<65;5;5M" + "q", width=100)["top"] == 3
    # The status bar: w and ? are clickable (q too: it closes, or the test would hang on EOF).
    hints, pos = " lines 1-10/60 (16%)  ", {}
    for i, (key, what, _action) in enumerate(PAGER_HINTS):
        hints += "  " if i else ""
        pos[key] = len(hints) + 1
        hints += f"{key} {what}"
    assert _page(_click(pos["w"], 11) + "q", width=100)["wrap"] is True
    assert _page(_click(pos["?"], 11) + "q" + "q", width=100)["help"] is False  # ? opened it, q closed it
    assert _page(_click(pos["q"], 11), width=100)["top"] == 0


def test_pager_keys_scroll_search_wrap_and_quit():
    assert _page("q")["top"] == 0
    assert _page("jjjq")["top"] == 3
    assert _page("Gq")["top"] == 50  # 60 lines, 10 body rows
    assert _page("Gkq")["top"] == 49
    assert _page(" q")["top"] == 9
    assert _page("dq")["top"] == 5
    assert _page("\x1b[6~uq")["top"] == 4  # PgDn, then half a page up
    state = _page("/line 2\rq")
    assert state["pattern"] == "line 2" and state["match"] == 2 and state["top"] == 2
    state = _page("/line 2\rnnq")
    assert state["match"] == 21  # line 20 and 21 both contain "line 2"
    state = _page("/zzz\rq")
    assert "not found" in state["message"]
    state = _page("\x1b[C\x1b[Cq")  # → →
    assert state["left"] == 16
    assert _page("wq")["wrap"] is True
    assert _page("wGq")["top"] > 50  # wrapped lines take more rows, so fewer fit on the last screen
    assert _page("?qq")["help"] is False  # ? opens the help, the first q closes it, the second quits
    assert _page("?\x03")["help"] is True  # Ctrl-C quits from anywhere


def test_pager_title_is_shortened():
    from shellcraft.output import _short

    assert _short("man grep", 30) == "man grep"
    assert _short("a" * 50, 20) == "a" * 19 + "…"


def test_spinner_label_adds_elapsed_time_and_the_cancel_hint():
    assert spinner_label("running x…", 1) == "running x…"
    assert spinner_label("running x…", 4.6) == "running x… (4 s)"
    assert spinner_label("running x…", 12) == "running x… (12 s)  · Ctrl-C cancels"


def test_highlight_in_theme_colors_restores_the_lines_own_color():
    from shellcraft.output import _highlight

    line = "\x1b[31mred match red\x1b[0m"
    out = _highlight(line, "match", "\x1b[7m", "\x1b[0m")
    assert out == "\x1b[31mred \x1b[7mmatch\x1b[0m\x1b[31m red\x1b[0m"


# ── show() ───────────────────────────────────────────────────────────────────

def test_show_pages_only_when_asked(monkeypatch):
    """Tall output prints; only a Paged value with page=True (man, `cmd | less`) opens the viewer,
    and never with the pager setting off."""
    from shellcraft import output
    from shellcraft.context import Paged

    paged = []
    monkeypatch.setattr(output, "page", lambda ui, rendered, title=None: paged.append(title))
    ui = _ui(height=10)
    show(ui, "\n".join(map(str, range(8))), title="fits")
    show(ui, "\n".join(map(str, range(50))), title="tall")  # taller than the window: printed anyway
    show(ui, Paged("\n".join(map(str, range(3))), page=True), title="less")  # short, but asked for
    show(ui, Paged("\n".join(map(str, range(50))), page=True), title="man")
    show(ui, Paged("\n".join(map(str, range(50))), page=True), pager=False, title="off")
    assert paged == ["less", "man"]
    assert "49\n" in ui.console.file.getvalue()  # the tall one, printed


def test_show_renders_styled_wrapping_paged(monkeypatch):
    """`help` returns Styled(Paged(table), page=False): the table is printed, never its repr,
    and never paged, however tall it is."""
    from rich.text import Text

    from shellcraft import output
    from shellcraft.context import Paged, Styled

    paged = []
    monkeypatch.setattr(output, "page", lambda ui, rendered, title=None: paged.append(title))
    ui = _ui(height=5)
    tall = Text("\n".join(f"row {i}" for i in range(30)))
    show(ui, Styled(Paged(tall, page=False), "plain"), title="help")
    show(ui, Paged(Styled(tall, "plain"), page=False), title="help2")
    out = ui.console.file.getvalue()
    assert "row 29" in out and "Paged(" not in out and "Styled(" not in out
    assert paged == []
    assert to_text(Styled(Paged(tall, page=False), "plain")) == "plain"


def test_show_sends_huge_plain_text_straight_to_the_pager(monkeypatch):
    from shellcraft import output

    paged = []
    monkeypatch.setattr(output, "page", lambda ui, rendered, title=None: paged.append(rendered))
    from shellcraft.context import Paged

    monkeypatch.setattr(output, "PAGER_DIRECT_LINES", 100)
    show(_ui(), Paged("\n".join(["row"] * 200), page=True))
    assert len(paged) == 1 and paged[0].count("\n") == 199
    show(_ui(), "\n".join(["row"] * 200))  # not asked for: printed, however big
    assert len(paged) == 1


# ── themes ───────────────────────────────────────────────────────────────────

def test_theme_switch_persists_preview_does_not(tmp_path, monkeypatch):
    from shellcraft.pipeline import PipelineError, run_line

    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    ui = _ui()
    ctx = ShellContext(registry=ModuleRegistry(tmp_path), ui=ui, config={})
    run_line("theme nord", ctx)
    assert ui.theme.name == "nord" and json.loads((tmp_path / "config.json").read_text())["theme"] == "nord"
    run_line("theme preview dracula", ctx)
    assert ui.theme.name == "dracula" and json.loads((tmp_path / "config.json").read_text())["theme"] == "nord"
    run_line("theme reset", ctx)
    assert ui.theme.name == "cyberpunk" and json.loads((tmp_path / "config.json").read_text())["theme"] == "cyberpunk"
    with pytest.raises(PipelineError, match="did you mean 'nord'"):
        run_line("theme nrod", ctx)


def test_theme_in_a_subshell_is_not_saved(tmp_path, monkeypatch):
    """`theme NAME` in a lite shell (`sub shellcraft`) changes that subshell only."""
    from shellcraft.pipeline import run_line

    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    ui = _ui()
    ctx = ShellContext(registry=ModuleRegistry(tmp_path), ui=ui, config={}, lite=True)
    out = to_text(run_line("theme nord", ctx).output)
    assert ui.theme.name == "nord" and not (tmp_path / "config.json").exists()
    assert "this subshell only" in out and "theme --sub nord" in out
    run_line("theme reset", ctx)
    assert ui.theme.name == "cyberpunk" and not (tmp_path / "config.json").exists()
    main = ShellContext(registry=ModuleRegistry(tmp_path), ui=_ui(), config={})
    run_line("theme dracula", main)  # the main shell still saves
    assert json.loads((tmp_path / "config.json").read_text())["theme"] == "dracula"


def test_theme_sub_sets_shows_and_clears(tmp_path, monkeypatch):
    from shellcraft.pipeline import PipelineError, run_line

    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    ui = _ui()
    ctx = ShellContext(registry=ModuleRegistry(tmp_path), ui=ui, config={})
    assert "no subshell theme" in to_text(run_line("theme --sub", ctx).output)
    run_line("theme --sub matrix", ctx)
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["sub_theme"] == "matrix" and "theme" not in saved
    assert ui.theme.name == "cyberpunk"  # the main shell keeps its own
    assert "Matrix" in to_text(run_line("theme --sub", ctx).output)
    run_line("theme --sub --clear", ctx)
    assert "sub_theme" not in json.loads((tmp_path / "config.json").read_text())
    run_line("theme --sub nord", ctx)
    run_line("theme --sub off", ctx)
    assert "sub_theme" not in json.loads((tmp_path / "config.json").read_text())
    with pytest.raises(PipelineError, match="did you mean 'nord'"):
        run_line("theme --sub nrod", ctx)
    with pytest.raises(PipelineError, match="usage"):
        run_line("theme --sub nord matrix", ctx)
    lite_ui = _ui()  # from a subshell: saved, and that subshell switches too
    lite = ShellContext(registry=ModuleRegistry(tmp_path), ui=lite_ui, config={}, lite=True)
    run_line("theme --sub gruvbox", lite)
    assert lite_ui.theme.name == "gruvbox"
    assert json.loads((tmp_path / "config.json").read_text())["sub_theme"] == "gruvbox"


def test_a_lite_shell_starts_with_the_subshell_theme(tmp_path, monkeypatch, started):
    from shellcraft import cli

    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(json.dumps({"theme": "nord", "sub_theme": "matrix"}))
    assert cli.main(["--lite", "--modules", str(tmp_path)]) == 0
    assert started[-1].ui.theme.name == "matrix"
    assert cli.main(["--no-banner", "--modules", str(tmp_path)]) == 0
    assert started[-1].ui.theme.name == "nord"  # the main shell: its own theme
    assert cli.main(["--lite", "--theme", "dracula", "--modules", str(tmp_path)]) == 0
    assert started[-1].ui.theme.name == "dracula"  # --theme still wins
    (tmp_path / "config.json").write_text(json.dumps({"theme": "nord"}))
    assert cli.main(["--lite", "--modules", str(tmp_path)]) == 0
    assert started[-1].ui.theme.name == "nord"  # none set: the main theme, as before
    (tmp_path / "config.json").write_text(json.dumps({"theme": "nord", "sub_theme": "gone"}))
    assert cli.main(["--lite", "--modules", str(tmp_path)]) == 0
    assert started[-1].ui.theme.name == "nord"  # an unknown one: the main theme


def test_theme_sub_completes_theme_names_and_clear(tmp_path):
    from shellcraft.completer import ShellCompleter

    ctx = ShellContext(registry=ModuleRegistry(tmp_path), ui=_ui(), config={})
    completer = ShellCompleter(ctx)
    words = [c.text for c in completer.get_completions(Document("theme --"), CompleteEvent())]
    assert "--sub" in words
    words = [c.text for c in completer.get_completions(Document("theme --sub "), CompleteEvent())]
    assert "nord" in words and "--clear" in words


def test_light_themes_and_surface_colors():
    assert PRESETS["solarized-light"].light and PRESETS["catppuccin-latte"].light
    dark, light = PRESETS["nord"], PRESETS["catppuccin-latte"]
    assert dark._surface() != light._surface()
    style = light.pt_style()
    assert "completion-menu" in style.style_rules[0][0] or any("completion-menu" in r[0] for r in style.style_rules)
    custom = all_themes({"themes": {"mine": {"base": "nord", "light": True, "surface": "#eeeeee"}}})["mine"]
    assert custom.light and custom._surface() == "#eeeeee"


def test_theme_warnings_reach_the_banner(tmp_path):
    from shellcraft.banner import render_banner

    ui = _ui(width=120)
    panel = render_banner(ui, ModuleRegistry(tmp_path), ["theme 'x': invalid color"])
    ui.console.print(panel)
    assert "invalid color" in ui.console.file.getvalue()


def test_banner_falls_back_on_narrow_terminals(tmp_path):
    from shellcraft.banner import render_banner

    for width, expected in ((120, "███"), (40, "S H E L L C R A F T")):
        ui = _ui(width=width, record=True)
        ui.console.print(render_banner(ui, ModuleRegistry(tmp_path)))
        assert expected in ui.console.export_text()


# ── completion ───────────────────────────────────────────────────────────────

def test_completer_handles_quotes_and_positional_values(tmp_path, monkeypatch, registry):
    from shellcraft.completer import ShellCompleter, split_word

    monkeypatch.chdir(tmp_path)
    (tmp_path / "my file.txt").write_text("")
    (tmp_path / "plain.txt").write_text("")
    completer = ShellCompleter(ShellContext(registry=registry))
    run = lambda t: [c.text for c in completer.get_completions(Document(t), CompleteEvent())]  # noqa: E731
    assert split_word('cat "my fi') == ('cat "', "my fi", '"')
    assert split_word("cat 'a b' c") == ("cat 'a b' ", "c", None)
    assert run("cat my") == ["'my file.txt'"]  # quoted for the shell
    assert run('cat "my') == [" file.txt"]  # inside quotes: just the rest
    assert run("queryCensys ") == ["cert", "host", "search"]
    assert run("queryCensys h") == ["host"]
    assert run("queryCensys host ") == []  # an IP goes here (its usage says so), not a file name
    assert run("queryCensys host pl") == ["ain.txt"]  # though a word typed in part still gets them
    assert run("theme pre") == ["preview"] and run("theme preview no") == ["nord"]
    assert run("prompt na") == ["name"] and run("help gr") == ["grep"]


def test_help_narrow_layout(tmp_path):
    from shellcraft.context import to_text
    from shellcraft.pipeline import run_line

    ui = _ui(width=60, record=True)
    ctx = ShellContext(registry=ModuleRegistry(tmp_path), ui=ui)
    result = run_line("help", ctx)
    ui.console.print(result.output.renderable.value)
    text = ui.console.export_text()
    assert " ShellCraft " in text and "grep" in text and "┃" not in text  # rules and grids, not a table
    assert "Open a subshell or a background job" in text  # ShellCraft's own, described
    assert max(len(line) for line in text.splitlines()) <= 60  # never wider than the terminal
    assert to_text(result.output).startswith("sub  ")  # the plain side, ShellCraft group first


def test_paged_text_keeps_long_lines_for_sideways_scrolling(monkeypatch):
    """Long lines reach the pager whole (← / → scroll them, w wraps), not wrapped by Rich."""
    from rich.text import Text

    from shellcraft import output

    paged = []
    monkeypatch.setattr(output, "page", lambda ui, rendered, title=None: paged.append(rendered))
    ui = _ui(width=40, height=10)
    text = "\n".join(f"L{i:02d} " + "x" * 200 for i in range(30))
    show(ui, text, title="cat")
    show(ui, Text(text), title="grep")
    for rendered in paged:
        lines = rendered.rstrip("\n").split("\n")
        assert len(lines) == 30 and "x" * 200 in lines[0]


def test_banner_warns_about_missing_core_packages(tmp_path, monkeypatch):
    """The banner lists what the shell itself is missing, as it does a module's missing import."""
    import importlib.util
    import sys

    from shellcraft.banner import core_warnings, render_banner

    gone = {"winpty", "pyte", "mcp", "keyring"}  # keyring: never asked about any more
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None if name in gone else object())
    monkeypatch.setattr(sys, "platform", "win32")
    found = core_warnings()
    assert [w.split(" ")[0] for w in found] == ["pywinpty", "pyte", "the"]
    assert "no footer" in found[0] and "pip install pywinpty" in found[0] and "mcp start" in found[2]
    monkeypatch.setattr(sys, "platform", "linux")
    assert not any(w.startswith("pywinpty") for w in core_warnings())  # pywinpty is Windows only
    gone.clear()
    assert core_warnings() == []
    gone.add("pyte")
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    ui = UI(all_themes({})["nord"], console)
    console.print(render_banner(ui, ModuleRegistry(tmp_path), ["theme 'x': invalid color"]))
    text = console.export_text()
    assert "⚠ pyte is not installed" in text and "⚠ theme 'x'" in text


# ── themes: custom colors and presets ─────────────────────────────────────────

def test_invalid_custom_color_falls_back_to_base():
    warnings: list[str] = []
    config = {"themes": {"mine": {"base": "matrix", "prompt": "not-a-color", "accent": "#123456"}}}
    theme = all_themes(config, warnings)["mine"]
    assert theme.prompt == PRESETS["matrix"].prompt
    assert theme.accent == "#123456"
    assert len(warnings) == 1 and "not-a-color" in warnings[0] and "'prompt'" in warnings[0]
    UI(theme)  # used to raise ValueError: Wrong color format


def test_color_must_work_in_rich_and_prompt_toolkit():
    config = {"themes": {"mine": {"path": "ansired", "muted": "#fff", "error": "red"}}}
    theme = all_themes(config)["mine"]  # no warnings list: stays silent
    assert theme.path == PRESETS["nord"].path  # prompt_toolkit only
    assert theme.muted == PRESETS["nord"].muted  # prompt_toolkit only
    assert theme.error == "red"


def test_every_preset_color_is_valid_and_renders():
    from shellcraft.themes import _COLOR_FIELDS, _valid_color

    assert {"dracula", "gruvbox", "catppuccin", "tokyonight"} <= set(PRESETS)
    for theme in PRESETS.values():
        assert all(_valid_color(getattr(theme, f)) for f in _COLOR_FIELDS), theme.name
        UI(theme)
