"""stdin / stdout / stderr handling that has to work the same on Windows and Linux."""

import io
import os
import sys

import pytest

from core import pipeline
from core.stdio import is_console, utf8_stdio


def test_is_console_rejects_non_terminals(tmp_path):
    assert not is_console(None)  # pythonw.exe has no std streams
    assert not is_console(io.StringIO())
    read_end, write_end = os.pipe()
    try:
        with os.fdopen(write_end, "w", closefd=False) as pipe:
            assert not is_console(pipe)
    finally:
        os.close(read_end)
        os.close(write_end)
    with open(tmp_path / "out.txt", "w") as fh:
        assert not is_console(fh)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pseudo-terminal")
def test_is_console_accepts_a_terminal():
    import pty

    primary, secondary = pty.openpty()
    try:
        with os.fdopen(secondary, "w", closefd=False) as tty:
            assert is_console(tty)
    finally:
        os.close(primary)
        os.close(secondary)


def _cp1252_stream():
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", write_through=True)


def test_utf8_stdio_fixes_windows_ansi_code_page_pipes(monkeypatch):
    out, err = _cp1252_stream(), _cp1252_stream()
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    with pytest.raises(UnicodeEncodeError):
        out.write("├── ╭─ ✗")  # what `tree > out.txt` or an error panel hit on Windows
    utf8_stdio()
    out.write("├── ✗\n")
    err.write("╭─\n")
    assert out.buffer.getvalue().decode("utf-8").endswith("├── ✗\n")
    assert err.buffer.getvalue().decode("utf-8") == "╭─\n"


def test_utf8_stdio_respects_pythonioencoding(monkeypatch):
    out = _cp1252_stream()
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    monkeypatch.setattr(sys, "stdout", out)
    utf8_stdio()
    assert out.encoding == "cp1252"


def test_utf8_stdio_leaves_utf8_and_missing_streams_alone(monkeypatch):
    out = io.TextIOWrapper(io.BytesIO(), encoding="UTF8")
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", None)  # pythonw.exe
    utf8_stdio()
    assert out.encoding == "UTF8"


def test_windows_cmd_builtins_are_read_as_utf16():
    raw = "Répertoire de C:\\données\r\n".encode("utf-16-le")
    assert pipeline._decode(raw, utf16=True) == "Répertoire de C:\\données\n"
    assert pipeline._decode("héllo\r\nwörld".encode(), utf16=True) == "héllo\nwörld"  # not UTF-16 after all
    assert pipeline._decode("a\r\nb\n".encode()) == "a\nb\n"


def test_windows_cmd_builtins_use_unicode_output(monkeypatch):
    monkeypatch.setattr(pipeline.sys, "platform", "win32")
    monkeypatch.setattr(pipeline.shutil, "which", lambda name: None)
    argv = pipeline._resolve_system(pipeline.Command("dir", ["/b"]))
    assert argv == ["cmd", "/d", "/u", "/c", "dir", "/b"]
