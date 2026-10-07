"""Small helpers for module authors (optional — a module only needs `run(args, stdin)`)."""

from __future__ import annotations

import argparse
import functools
import io
import re
import sys
import threading
from dataclasses import dataclass
from typing import Any, Callable, NoReturn


class ModuleError(Exception):
    """A user-facing failure: the shell shows the message without a traceback."""


class ArgParser(argparse.ArgumentParser):
    """argparse that raises ModuleError instead of printing and calling sys.exit()."""

    def __init__(self, prog: str, **kwargs):
        kwargs.setdefault("add_help", False)
        super().__init__(prog=prog, **kwargs)

    def error(self, message: str) -> NoReturn:
        raise ModuleError(f"{self.prog}: {message}  (see `man {self.prog}`)")

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        raise ModuleError(message or f"{self.prog}: exited with status {status}")


@dataclass(frozen=True)
class EnvSetting:
    """An environment variable (usually an API key) a module needs, set with `settings NAME`.

    Declare them in the module as ENV_SETTINGS = [EnvSetting("MY_API_KEY", "My API key", "…")].
    The shell lists them in `settings` as "<module> · <label>", stores the value in
    config.json and exports it to os.environ, so the module just reads os.environ["MY_API_KEY"].
    """

    name: str  # the environment variable, e.g. "DNSDUMPSTER_API_KEY"
    label: str  # short human name, e.g. "DnsDumpster API key"
    description: str  # where to get it, what it unlocks


# ── @script: run an ordinary print()-style script as a module ───────────────────────────────

_capture = threading.local()  # .out / .err: this thread's buffers while a @script call runs


class _ThreadRouter:
    """Stands in for sys.stdout / sys.stderr: a thread inside a @script call writes into its own
    buffer, every other thread writes to the real stream. Unlike contextlib.redirect_stdout this
    is safe when MCP runs several calls at once, and it never captures the shell's own output."""

    def __init__(self, real: Any, slot: str):
        self._real = real
        self._slot = slot

    def _target(self) -> Any:
        return getattr(_capture, self._slot, None) or self._real

    def write(self, text: str) -> int:
        return self._target().write(text)

    def writelines(self, lines: Any) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        if getattr(_capture, self._slot, None) is None:
            self._real.flush()

    def isatty(self) -> bool:
        return False if getattr(_capture, self._slot, None) is not None else self._real.isatty()

    def __getattr__(self, name: str) -> Any:  # .buffer, .fileno, .encoding, ... of the real stream
        return getattr(self._real, name)


def _install_routers() -> None:
    # Re-checked on every call: something (a test's redirect_stdout) may have swapped the stream.
    if not isinstance(sys.stdout, _ThreadRouter):
        sys.stdout = _ThreadRouter(sys.stdout, "out")
    if not isinstance(sys.stderr, _ThreadRouter):
        sys.stderr = _ThreadRouter(sys.stderr, "err")


def script(prog: str) -> Callable[[Callable[[list[str], str], Any]], Callable[[list[str], str], str]]:
    """Decorate run(args, stdin) whose body is a plain script: what it print()s becomes the output,
    sys.exit(0) ends it normally, any other exit (argparse errors too) becomes a ModuleError.

        @script("mytool")
        def run(args: list[str], stdin: str) -> str:
            ...  # the original script, print() and all
    """

    def decorate(fn: Callable[[list[str], str], Any]) -> Callable[[list[str], str], str]:
        @functools.wraps(fn)
        def run(args: list[str], stdin: str) -> str:
            _install_routers()
            saved = getattr(_capture, "out", None), getattr(_capture, "err", None)
            out, err = io.StringIO(), io.StringIO()
            _capture.out, _capture.err = out, err
            try:
                result = fn(args, stdin)
            except SystemExit as exc:
                code = exc.code
                if code not in (None, 0):
                    lines = [ln.strip() for ln in err.getvalue().splitlines() if ln.strip()]
                    detail = str(code) if isinstance(code, str) else (lines[-1] if lines else
                                                                      f"exited with status {code}")
                    # argparse writes "<prog>: error: <message>" (prog from sys.argv[0]); keep the message
                    detail = re.sub(r"^.*?: error: ", "", detail)
                    if detail.startswith(f"{prog}: "):
                        detail = detail[len(prog) + 2:]
                    raise ModuleError(f"{prog}: {detail}") from None
                result = None
            finally:
                _capture.out, _capture.err = saved
            text = out.getvalue()
            return text + result if isinstance(result, str) else text

        return run

    return decorate
