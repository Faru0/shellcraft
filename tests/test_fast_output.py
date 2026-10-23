"""Plain text skips Rich on its way to the terminal, and looks exactly as it did."""

import io

from rich.console import Console
from rich.text import Text

from shellcraft.output import show, write_plain
from shellcraft.themes import UI, all_themes


def console(width: int = 40, record: bool = False) -> Console:
    return Console(file=io.StringIO(), force_terminal=True, width=width, record=record)


def rich_output(text: str, width: int = 40) -> str:
    con = console(width)
    con.print(Text.from_ansi(text))
    return con.file.getvalue()


def test_plain_text_is_written_as_rich_would_print_it():
    for text in ("hello", "a\nb\n\nc", "\n".join(str(n) for n in range(2000)), "x" * 40):
        con = console()
        assert write_plain(con, text)
        assert con.file.getvalue() == rich_output(text)


def test_what_rich_must_still_handle_is_left_to_it():
    con = console()
    for text in ("col\tumn", "\x1b[31mred\x1b[0m", "wide 漢字", "x" * 41, "bell\x07", "back\rspace"):
        assert not write_plain(con, text), text
    assert con.file.getvalue() == ""
    assert write_plain(con, "x" * 41, soft=True)  # the terminal folds it, as Rich's soft_wrap does


def test_a_recording_console_or_a_capture_still_sees_the_text():
    con = console(record=True)
    assert not write_plain(con, "hello")
    con = console()
    with con.capture():
        assert not write_plain(con, "hello")
    assert write_plain(con, "hello")


def test_show_prints_a_big_plain_result_the_same_way():
    text = "\n".join(f"line {n}" for n in range(5000))
    ui = UI(all_themes({})["cyberpunk"], console(80))
    show(ui, text, pager=False)
    assert ui.console.file.getvalue() == rich_output(text, 80)
