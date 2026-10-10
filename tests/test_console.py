"""core.console: the Windows console devices (checked with fakes on any OS) and the Rich console."""

import contextlib
import io
import sys
from types import SimpleNamespace as NS

import pytest
from prompt_toolkit.application.current import create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.output import DummyOutput

from core import console


def _sized_output(rows, columns):
    output = DummyOutput()
    output.get_size = lambda: Size(rows=rows, columns=columns)
    return output


# ── setup choice ─────────────────────────────────────────────────────────────

def test_only_used_on_windows(monkeypatch):
    monkeypatch.setattr(console.sys, "platform", "linux")
    with contextlib.ExitStack() as cleanup:
        assert console.create_prompt_toolkit_console(cleanup) is None


@pytest.mark.parametrize("value, mode", [
    (None, "direct"), ("", "direct"), ("direct", "direct"), (" Legacy-Keys ", "legacy-keys"),
    ("native", "native"), ("bogus", "direct"),
])
def test_console_mode_setting(monkeypatch, value, mode):
    if value is None:
        monkeypatch.delenv("SHELLCRAFT_CONSOLE", raising=False)
    else:
        monkeypatch.setenv("SHELLCRAFT_CONSOLE", value)
    assert console.console_mode() == mode


def test_native_asks_for_prompt_toolkits_own_console(monkeypatch):
    monkeypatch.setattr(console.sys, "platform", "win32")
    monkeypatch.setenv("SHELLCRAFT_CONSOLE", "native")
    with contextlib.ExitStack() as cleanup, pytest.raises(OSError, match="SHELLCRAFT_CONSOLE=native"):
        console.create_prompt_toolkit_console(cleanup)


# ── output: prompt_toolkit's Windows10_Output on CONOUT$ ──────────────────────

def test_size_is_win32outputs_visible_window_without_the_last_column():
    # conhost: a 9001-row scrollback, scrolled down; only srWindow is the visible window.
    info = NS(srWindow=NS(Left=0, Top=100, Right=119, Bottom=129), dwSize=NS(X=120, Y=9001))
    assert console.window_size(info) == Size(rows=30, columns=119)
    # A buffer wider than the window (horizontal scrollbar): the window counts.
    wide_buffer = NS(srWindow=NS(Left=0, Top=0, Right=99, Bottom=9), dwSize=NS(X=300, Y=10))
    assert console.window_size(wide_buffer).columns == 99
    # A window wider than the buffer: capped below the buffer width.
    narrow_buffer = NS(srWindow=NS(Left=0, Top=0, Right=199, Bottom=9), dwSize=NS(X=80, Y=10))
    assert console.window_size(narrow_buffer).columns == 79


def test_output_reports_size_and_rows_below_the_cursor(monkeypatch):
    # 30 visible rows (100..129), cursor on row 120: rows 120..129 are free for the menu.
    info = NS(srWindow=NS(Left=0, Top=100, Right=119, Bottom=129), dwSize=NS(X=120, Y=9001),
              dwCursorPosition=NS(X=0, Y=120))
    monkeypatch.setattr(console, "buffer_info", lambda conout: info)
    output = console.DirectOutput(io.StringIO(), conout="CONOUT$")
    assert output.get_rows_below_cursor_position() == 10
    assert output.get_size() == Size(rows=30, columns=119)
    assert not output.responds_to_cpr  # the reply could arrive as keys

    monkeypatch.setattr(console, "buffer_info", lambda conout: None)
    with pytest.raises(NotImplementedError):  # the renderer then does without it
        output.get_rows_below_cursor_position()
    assert output.get_size() == Size(rows=24, columns=79)


def test_flush_writes_in_windows10_outputs_mode_and_restores_it(monkeypatch):
    modes = {"CONOUT$": 0x0007 | console.DISABLE_NEWLINE_AUTO_RETURN}
    events = []

    def set_mode(handle, mode):
        events.append(("mode", mode))
        modes[handle] = mode
        return True

    monkeypatch.setattr(console, "get_mode", lambda handle: modes[handle])
    monkeypatch.setattr(console, "set_mode", set_mode)

    class Stream(io.StringIO):
        def write(self, text):
            events.append(("write", modes["CONOUT$"]))
            return super().write(text)

    stream = Stream()
    output = console.DirectOutput(stream, conout="CONOUT$")
    output.flush()
    assert events == []  # nothing to write: the console mode is left alone
    output.write("hello")
    output.flush()
    flush_mode = console.ENABLE_PROCESSED_OUTPUT | console.ENABLE_VIRTUAL_TERMINAL_PROCESSING
    assert console.FLUSH_OUTPUT_MODE == flush_mode
    assert events[0] == ("mode", flush_mode)
    assert ("write", flush_mode) in events  # written with no wrap and no delayed wrap
    assert events[-1] == ("mode", 0x0007 | console.DISABLE_NEWLINE_AUTO_RETURN)  # restored
    assert "hello" in stream.getvalue()


def test_resting_mode_never_turns_on_delayed_wrap():
    assert not console.RESTING_OUTPUT_MODE & console.DISABLE_NEWLINE_AUTO_RETURN
    assert console.RESTING_OUTPUT_MODE & console.ENABLE_VIRTUAL_TERMINAL_PROCESSING


# ── input: Win32Input bound to CONIN$ ────────────────────────────────────────

def _fake_win32():
    """Just enough of prompt_toolkit.input.win32: every console object starts on the std handle."""

    class Reader:
        def __init__(self):
            self.handle, self._fdcon, self.closed = "STD_INPUT", None, False

        def close(self):
            self.closed = True

    class Vt100ConsoleInputReader(Reader):
        pass

    class ConsoleInputReader(Reader):
        pass

    class raw_mode:
        def __init__(self, fileno=None, use_win10_virtual_terminal_input=False):
            self.handle, self.vt = "STD_INPUT", use_win10_virtual_terminal_input

    class cooked_mode(raw_mode):
        pass

    class _Win32InputBase:
        def __init__(self):
            self.win32_handles = "handles"

    class Win32Input(_Win32InputBase):
        def __init__(self):
            raise AssertionError("Win32Input.__init__ looks at the std handle")

    return NS(_Win32InputBase=_Win32InputBase, Win32Input=Win32Input, ConsoleInputReader=ConsoleInputReader,
              Vt100ConsoleInputReader=Vt100ConsoleInputReader, raw_mode=raw_mode, cooked_mode=cooked_mode)


@pytest.mark.parametrize("vt_keys, supported, reader, vt", [
    (True, True, "Vt100ConsoleInputReader", True),     # Windows 10+: prompt_toolkit's default
    (True, False, "ConsoleInputReader", False),         # VT input refused by the console
    (False, True, "ConsoleInputReader", False),         # SHELLCRAFT_CONSOLE=legacy-keys
])
def test_console_input_reads_and_switches_modes_on_conin(monkeypatch, vt_keys, supported, reader, vt):
    import prompt_toolkit.input

    win32 = _fake_win32()
    monkeypatch.setattr(prompt_toolkit.input, "win32", win32, raising=False)
    monkeypatch.setitem(sys.modules, "prompt_toolkit.input.win32", win32)
    monkeypatch.setitem(sys.modules, "msvcrt", NS(get_osfhandle=lambda fd: 4242))
    monkeypatch.setattr(console, "get_mode", lambda handle: 0x01f7)
    monkeypatch.setattr(console, "set_mode", lambda handle, mode: supported or mode != console.ENABLE_VIRTUAL_TERMINAL_INPUT)

    console_input, conin = console._console_input(7, vt_keys=vt_keys)
    assert conin.value == 4242
    assert type(console_input.console_input_reader).__name__ == reader
    assert console_input.console_input_reader.handle is conin  # the reader reads CONIN$ ...
    assert console_input.console_input_reader.closed and console_input.console_input_reader._fdcon is None
    assert console_input.raw_mode().handle is conin  # ... and CONIN$ is what goes raw
    assert console_input.raw_mode().vt is vt
    assert console_input.cooked_mode().handle is conin
    assert console_input._use_virtual_terminal_input is vt
    assert console_input.fileno() == 7


def test_changed_prompt_toolkit_internals_are_a_clear_error():
    complete = NS(**{name: object() for name in console.WIN32_INTERNALS})
    console.check_win32_internals(complete)
    with pytest.raises(OSError, match="no _Win32InputBase"):
        console.check_win32_internals(NS(**{name: object() for name in console.WIN32_INTERNALS[1:]}))
    console.check_handle(NS(handle=1))
    with pytest.raises(OSError, match="has no .handle"):
        console.check_handle(NS())


# ── Rich ─────────────────────────────────────────────────────────────────────

def test_build_console_is_a_truecolor_vt_terminal():
    rich_console = console.build_console(file=io.StringIO())
    assert rich_console.is_terminal and not rich_console.legacy_windows
    assert rich_console.color_system == "truecolor"


def test_rich_measures_like_the_prompt(monkeypatch):
    monkeypatch.setenv("COLUMNS", "300")  # a stale snapshot must not win over the live size
    monkeypatch.setenv("LINES", "99")
    sizes = [(30, 120)]
    output = _sized_output(0, 0)
    output.get_size = lambda: Size(*sizes[0])
    rich_console = console.build_console(file=io.StringIO())
    with create_app_session(output=output):
        assert (rich_console.width, rich_console.height) == (120, 30)
        sizes[0] = (40, 90)  # the window was resized
        assert rich_console.size == (90, 40)
        rich_console.width = 50  # an explicit width still wins
        assert rich_console.size == (50, 40)


def test_rich_falls_back_to_its_own_size_without_an_output(monkeypatch):
    output = _sized_output(0, 0)

    def broken():
        raise OSError("not a terminal")

    output.get_size = broken
    monkeypatch.setenv("COLUMNS", "77")
    rich_console = console.build_console(file=io.StringIO())
    with create_app_session(output=output):
        assert rich_console.width == 77


# ── truecolor prompt on Linux ────────────────────────────────────────────────

class _Tty(io.StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize("env, tty, expected", [
    ({"COLORTERM": "truecolor"}, True, "DEPTH_24_BIT"),
    ({"COLORTERM": "24bit"}, True, "DEPTH_24_BIT"),
    ({}, True, None),                                            # 256 colors: prompt_toolkit's default
    ({"COLORTERM": "truecolor"}, False, None),                   # stdout isn't a terminal
    ({"COLORTERM": "truecolor", "NO_COLOR": "1"}, True, None),   # NO_COLOR still wins
    ({"COLORTERM": "truecolor", "PROMPT_TOOLKIT_COLOR_DEPTH": "DEPTH_4_BIT"}, True, None),
])
def test_prompt_uses_truecolor_when_the_terminal_says_so(monkeypatch, env, tty, expected):
    for name in ("COLORTERM", "NO_COLOR", "PROMPT_TOOLKIT_COLOR_DEPTH"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(console.sys, "stdout", _Tty() if tty else io.StringIO())
    output = console.truecolor_output()
    assert (output and output.get_default_color_depth().name) == expected
