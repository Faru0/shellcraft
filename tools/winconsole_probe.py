"""Check the interactive shell's console setup on a real terminal (written for Windows Terminal).

    python tools/winconsole_probe.py                      the shell's setup (SHELLCRAFT_CONSOLE=direct)
    $env:SHELLCRAFT_CONSOLE="legacy-keys"; python tools\\winconsole_probe.py   classic key reader
    $env:SHELLCRAFT_CONSOLE="native";      python tools\\winconsole_probe.py   prompt_toolkit's own

It opens the console exactly as the shell does (core.console) and runs a prompt that looks like the
shell's, with Tab completion. The bottom toolbar shows the last keys prompt_toolkit received (key and
raw data), the console modes and the size, live. Try: typing, Backspace in the middle of a line, Tab
(twice, near the bottom of the window too), the arrows, Ctrl-C, resizing the window. Each accepted
line is echoed through Rich, like command output. Ctrl-D or `exit` quits and prints a report to copy.
"""

from __future__ import annotations

import contextlib
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # runnable from anywhere: python <shellcraft>/tools/winconsole_probe.py
    sys.path.insert(0, str(ROOT))

from prompt_toolkit import PromptSession  # noqa: E402
from prompt_toolkit.application.current import create_app_session, get_app_session  # noqa: E402
from prompt_toolkit.completion import WordCompleter  # noqa: E402
from prompt_toolkit.formatted_text import FormattedText  # noqa: E402
from prompt_toolkit.input import create_input  # noqa: E402

from core import console  # noqa: E402
from core.diagnostics import _INPUT_FLAGS, _OUTPUT_FLAGS, _flags  # noqa: E402

WORDS = ["alias", "banner", "cat", "cd", "diff", "echo", "fetch", "filter", "find", "grep", "help",
         "history", "ip2geo", "ls", "man", "modules", "myip", "queryCensys", "queryCert", "queryDns",
         "settings", "sort", "tail", "theme", "tree", "uniq", "which"]


def _log_keys(input_: object, keys: deque[str], everything: list[str]) -> None:
    """Record every key prompt_toolkit reads, as `key data`, without changing what it gets."""
    for name in ("read_keys", "flush_keys"):
        original = getattr(input_, name)

        def logged(original=original):
            presses = original()
            for press in presses:
                key = getattr(press.key, "value", press.key)
                entry = f"{key} {press.data!r}"
                keys.append(entry)
                everything.append(entry)
            return presses

        setattr(input_, name, logged)


def main() -> int:
    from core.stdio import utf8_stdio

    utf8_stdio()
    keys: deque[str] = deque(maxlen=6)
    everything: list[str] = []
    with contextlib.ExitStack() as cleanup:
        try:
            direct = console.create_prompt_toolkit_console(cleanup)
            why = None
        except OSError as exc:
            direct, why = None, str(exc)
        if direct is not None:
            input_, output = direct.input, direct.output
            cleanup.enter_context(create_app_session(input=input_, output=output))
            rich = console.build_console(file=direct.stream)
            setup = f"direct: {direct.reader}, VT input {'on' if direct.vt_input else 'off'}"
        else:
            input_ = create_input()
            cleanup.enter_context(create_app_session(input=input_))
            rich = console.build_console()
            setup = f"prompt_toolkit's own console ({why or 'not Windows'})"
        _log_keys(input_, keys, everything)

        def modes() -> str:
            if direct is None:
                return ""
            return (f"CONIN$ {_flags(console.get_mode(direct.conin), _INPUT_FLAGS)}\n"
                    f"CONOUT$ {_flags(console.get_mode(direct.conout), _OUTPUT_FLAGS)}")

        def toolbar() -> FormattedText:
            size = get_app_session().output.get_size()
            text = (f" {setup} · size {size.columns}x{size.rows} · rich {rich.width}x{rich.height}\n"
                    f" keys: {' | '.join(keys) or '(none yet)'}\n {modes()}")
            return FormattedText([("", text)])

        prompt = FormattedText([("ansibrightmagenta", "╭─ ⚡probe "), ("ansicyan", "─ user@host ─ C:\\Users\\me"),
                                ("", "\n"), ("ansibrightmagenta", "╰─❯ ")])
        session: PromptSession = PromptSession(completer=WordCompleter(WORDS), complete_while_typing=True,
                                               bottom_toolbar=toolbar, refresh_interval=0.5)
        rich.print(f"[bold]shellcraft console probe[/] — {setup}. Ctrl-D or `exit` quits.")
        while True:
            try:
                line = session.prompt(prompt)
            except KeyboardInterrupt:
                rich.print("[yellow]^C[/]")
                continue
            except EOFError:
                break
            if line.strip() == "exit":
                break
            rich.print(f"you typed [cyan]{line!r}[/] ({len(line)} characters)")

        report = [f"setup: {setup}", f"SHELLCRAFT_CONSOLE={console.console_mode()}"]
        if direct is not None:
            report += [f"CONIN$ mode before {_flags(direct.input_mode_before, _INPUT_FLAGS)}",
                       f"CONOUT$ mode before {_flags(direct.output_mode_before, _OUTPUT_FLAGS)}", modes()]
        report += ["keys received (key, raw data):", *(f"  {k}" for k in everything[-60:])]
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
