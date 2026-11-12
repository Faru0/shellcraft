"""Alerts: the bus (priority, expiry, dismissal), the spool folder and its senders, the `alert`
builtin (local and forwarded), the subshell shim, the panel lines, the MCP tool."""

import io
import json
import os
import sys
import time

import pytest
from rich.console import Console

from shellcraft import alerts as alertmod
from shellcraft.alerts import AlertBus
from shellcraft.context import ShellContext, to_text
from shellcraft.loader import ModuleRegistry
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.themes import UI, all_themes


def _wait(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(alertmod.SUB_ENV, raising=False)
    monkeypatch.delenv("SHELLCRAFT_DEPTH", raising=False)
    return tmp_path / "home"


@pytest.fixture
def bus(home):
    b = AlertBus()
    b.watch(home)
    yield b
    b.close()


def test_bus_orders_by_severity_caps_the_list_and_expires():
    changes = []
    b = AlertBus()
    b.on_change = lambda: changes.append(1)
    b.post("first", "info")
    b.post("second", "crit")
    b.post("third", "warn")
    b.post("fourth", "info")
    levels = [(a.level, a.message) for a in b.current()]
    assert levels == [("crit", "second"), ("warn", "third"), ("info", "fourth")]  # three kept, severe first
    assert b.current()[0].remaining is None and b.current()[2].timeout == 15.0 and b.current()[1].timeout == 60.0
    short = b.post("blink", "crit", timeout=1)  # a fourth alert: the lowest, oldest one ("fourth") goes
    assert short.timeout == 1.0 and [a.message for a in b.current()] == ["blink", "second", "third"]  # newest first
    assert _wait(lambda: all(a.message != "blink" for a in b.current()), 3)  # expired by the timer
    assert len(changes) >= 6
    assert b.dismiss(1) == 1 and [a.message for a in b.current()] == ["third"]
    assert b.dismiss() == 1 and b.current() == [] and b.dismiss() == 0
    assert b.post("x" * 500, "nope").message == "x" * 300 and b.current()[0].level == "info"
    b.close()


def test_spool_delivers_labels_and_drops_junk(bus, home, monkeypatch):
    spool = home / alertmod.SPOOL
    assert bus.spool == spool and spool.is_dir()
    if sys.platform != "win32":
        assert (spool.stat().st_mode & 0o777) == 0o700
    written = alertmod.send("disk almost full", "warn", 30, source="test")
    assert written.parent == spool and written.suffix == ".json"
    assert _wait(lambda: bus.current() and bus.current()[0].message == "disk almost full")
    first = bus.current()[0]
    assert first.level == "warn" and first.timeout == 30.0 and first.source == "test"
    assert _wait(lambda: not list(spool.glob("*.json")))  # consumed
    # the sender's label comes from the environment
    assert alertmod.source_label() == f"pid {os.getppid()}"
    monkeypatch.setenv(alertmod.SUB_ENV, "2:bash --norc extra")
    assert alertmod.source_label() == "sub 2 (bash --norc)"
    alertmod.send("from inside")
    assert _wait(lambda: any(a.source == "sub 2 (bash --norc)" for a in bus.current()))
    # junk, a bad timeout and a file from last month are handled without posting junk
    (spool / "0-junk.json").write_text("not json")
    (spool / "1-list.json").write_text("[1, 2]")
    (spool / "2-stale.json").write_text(json.dumps({"time": time.time() - 30 * 86400, "message": "old"}))
    (spool / "2-when.json").write_text(json.dumps({"time": "yesterday", "message": "old"}))
    (spool / "3-bad.json").write_text(json.dumps({"time": time.time(), "message": "odd", "timeout": "soon"}))
    assert _wait(lambda: any(a.message == "odd" for a in bus.current()))
    assert bus.current()[-1].timeout == 15.0 and not any("old" in a.message for a in bus.current())
    assert bus.waiting() == []
    assert _wait(lambda: not list(spool.glob("*.json")))
    bus.close()
    alertmod.send("after close", home=home)  # nobody watching: the file waits in the spool
    assert list(spool.glob("*.json"))


# ── alerts nobody was there to see ───────────────────────────────────────────

def _spooled(home, message, age, level="info", **more):
    """One spool file written `age` seconds ago, the way a sender leaves it."""
    spool = alertmod.spool_dir(home)
    sent = time.time() - age
    path = spool / f"{sent:.6f}-1-{len(list(spool.iterdir())):06x}.json"
    path.write_text(json.dumps({"time": sent, "level": level, "message": message, "source": "mcp · rounds", **more}))
    return path


def test_an_alert_sent_while_no_shell_was_open_is_shown_as_missed(home):
    sent = time.time() - 2 * 3600
    _spooled(home, "new listening port 4444 on web01", 2 * 3600, "warn", timeout=30, content="fleet host:web01")
    bus = AlertBus()
    bus.spool = alertmod.spool_dir(home)
    assert bus.drain() == 1
    (alert,) = bus.current()
    assert alert.message == f"new listening port 4444 on web01 (missed, {alertmod.clock(sent)})"
    assert alert.level == "warn" and alert.source == "mcp · rounds" and alert.content == "fleet host:web01"
    assert alert.timeout is None and alert.remaining is None  # it stays, whatever timeout it was sent with
    assert alert.raised == pytest.approx(sent, abs=1)
    assert not list(bus.spool.glob("*.json"))


def test_a_fresh_alert_is_not_called_missed(home):
    _spooled(home, "just now", 20)
    bus = AlertBus()
    bus.spool = alertmod.spool_dir(home)
    bus.drain()
    (alert,) = bus.current()
    assert alert.message == "just now" and alert.raised is None and alert.timeout == 15.0


def test_the_clock_of_a_missed_alert_names_the_day_when_it_was_not_today():
    now = time.time()
    assert alertmod.clock(now) == time.strftime("%H:%M", time.localtime(now))
    other = now - 3 * 86400
    assert alertmod.clock(other) == time.strftime("%a %H:%M", time.localtime(other))


def test_a_long_missed_alert_keeps_its_note():
    bus = AlertBus()
    when = time.time() - 600
    alert = bus.post("x" * 500, "info", raised=when)
    assert len(alert.message) == alertmod.MAX_MESSAGE and alert.message.endswith(f" (missed, {alertmod.clock(when)})")


def test_old_requests_still_expire(home):
    asked = []
    bus = AlertBus()
    bus.spool = alertmod.spool_dir(home)
    bus.on_request = asked.append
    bus.on_recording = asked.append
    for age in (20, 3600):
        _spooled(home, "", age, kind="sub", args=["bash"], cwd="/")
        _spooled(home, "", age, kind="recording", on=True)
    assert bus.drain() == 0 and bus.current() == []
    assert sorted(a["kind"] for a in asked) == ["recording", "sub"]  # the fresh pair only
    assert not list(bus.spool.glob("*.json"))


def test_a_missed_alert_older_than_a_week_is_dropped(home):
    _spooled(home, "six days", 6 * 86400)
    _spooled(home, "eight days", 8 * 86400)
    bus = AlertBus()
    bus.spool = alertmod.spool_dir(home)
    assert bus.drain() == 1 and bus.current()[0].message.startswith("six days (missed, ")


def test_missed_alerts_wait_for_room_and_move_up(home):
    for n, level in enumerate(("info", "warn", "crit", "info", "warn")):
        _spooled(home, f"night {n}", 3600 - n * 60, level)
    bus = AlertBus()
    bus.spool = alertmod.spool_dir(home)
    assert bus.drain() == 5

    def shown():
        return [a.message.split(" (")[0] for a in bus.current()]

    def waiting():
        return [a.message.split(" (")[0] for a in bus.waiting()]

    assert shown() == ["night 2", "night 4", "night 1"]  # the most severe first, then the newest
    assert waiting() == ["night 3", "night 0"]
    assert bus.dismiss(1) == 1  # room is made: the next one moves up
    assert shown() == ["night 4", "night 1", "night 3"] and waiting() == ["night 0"]
    bus.remove(bus.current()[0])  # a click on its ×
    assert shown() == ["night 1", "night 3", "night 0"] and waiting() == []
    # a new alert takes its place by level; the missed one it pushes out waits, it is not lost
    bus.post("happening now", "crit", timeout=1)
    assert shown() == ["happening now", "night 1", "night 3"] and waiting() == ["night 0"]
    bus.post("chatter", "info")  # an alert of today that finds no room is dropped, as always
    assert "chatter" in shown() and waiting() == ["night 3", "night 0"]
    assert _wait(lambda: "happening now" not in shown(), 3)  # it expires: a missed one moves back up
    assert shown() == ["night 1", "chatter", "night 3"] and waiting() == ["night 0"]
    assert bus.dismiss(4) == 1 and waiting() == []  # numbered on from the shown ones
    assert bus.dismiss(9) == 0
    assert bus.dismiss() == 3 and bus.current() == [] and bus.waiting() == []
    bus.close()


def test_alert_list_shows_the_waiting_ones_and_clear_takes_them_all(ictx, home):
    for n in range(5):
        _spooled(home, f"night {n}", 3600 - n * 60, "warn")
    ictx.alerts.spool = alertmod.spool_dir(home)
    ictx.alerts.drain()
    listed = [row for row in to_text(run_line("alert --list", ictx).output).splitlines() if row]
    assert len(listed) == 5
    assert listed[0].startswith("1. [warn] night 4 (missed, ") and listed[0].endswith("(sticky, from mcp · rounds)")
    assert listed[3].startswith("4. [warn] night 1 (missed, ")
    assert listed[3].endswith("(waiting for room in the panel, from mcp · rounds)")
    assert "1 alert dismissed" in to_text(run_line("alert -c 5", ictx).output)
    assert "4 alerts dismissed" in to_text(run_line("alert -c", ictx).output)
    assert "no alerts" in to_text(run_line("alert --list", ictx).output)


def test_the_spool_keeps_200_alerts_and_a_week(home):
    spool = alertmod.spool_dir(home)
    for n in range(alertmod.MAX_SPOOLED + 5):
        _spooled(home, f"old {n}", 5000 - n)
    _spooled(home, "last month", 30 * 86400)
    (spool / "notes.txt").write_text("not an alert")
    newest = alertmod.send("the newest", home=home)
    left = sorted(p.name for p in spool.glob("*.json"))
    assert len(left) == alertmod.MAX_SPOOLED and newest.name in left
    kept = [json.loads((spool / name).read_text())["message"] for name in left]
    assert "last month" not in kept and "old 0" not in kept and "old 5" not in kept  # the oldest went
    assert kept[0] == "old 6" and kept[-1] == "the newest" and (spool / "notes.txt").exists()
    bus = AlertBus()
    bus.spool = spool
    assert bus.drain() == alertmod.MAX_SPOOLED
    assert len(bus.current()) == alertmod.MAX_ALERTS and len(bus.waiting()) == alertmod.MAX_SPOOLED - alertmod.MAX_ALERTS


def test_command_line_client_and_shim(bus, home, capsys):
    assert alertmod.main(["-l", "crit", "-t", "9", "build", "failed"]) == 0
    assert _wait(lambda: any(a.message == "build failed" and a.level == "crit" and a.timeout == 9.0
                             for a in bus.current()))
    assert alertmod.main([]) == 2 and "message is needed" in capsys.readouterr().err
    assert alertmod.main(["-c"]) == 2
    assert alertmod.parse_args(["-s", "hello", "there"]) == ("hello there", "info", None, False, False, "")
    assert alertmod.parse_args(["--list"])[4] is True
    assert alertmod.parse_args(["-d", "/var/log/app.log", "errors"])[5] == "/var/log/app.log"
    folder = alertmod.shim_dir(home)
    script = folder / ("alert.cmd" if sys.platform == "win32" else "alert")
    assert script.exists() and "shellcraft.alerts" in script.read_text()
    env = alertmod.with_shim({"PATH": "/usr/bin"}, home)
    assert env["PATH"].startswith(str(folder) + os.pathsep)
    if sys.platform != "win32":
        import subprocess

        done = subprocess.run([str(script), "-l", "warn", "from the shim"], capture_output=True, text=True,
                              env={**os.environ, "PATH": env["PATH"], alertmod.SUB_ENV: "1:bash"})
        assert done.returncode == 0, done.stderr
        assert _wait(lambda: any(a.message == "from the shim" and a.source == "sub 1 (bash)" for a in bus.current()))


@pytest.fixture
def ictx(home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = ShellContext(registry=ModuleRegistry(tmp_path / "modules"))
    ctx.interactive = True
    ctx.ui = UI(all_themes({})["nord"], Console(file=io.StringIO()))
    ctx.alerts = AlertBus()
    return ctx


def test_alert_builtin_posts_lists_and_dismisses(ictx):
    assert run_line("alert deploy finished", ictx).status == 0
    run_line("alert -l warn -t 40 disk at 90%", ictx)
    run_line("alert -l crit -s pager is down", ictx)
    listed = to_text(run_line("alert --list", ictx).output)
    assert listed.startswith("1. [crit] pager is down  (sticky, from main shell)")
    assert "2. [warn] disk at 90%" in listed and "3. [info] deploy finished" in listed
    assert "1 alert dismissed" in to_text(run_line("alert -c 2", ictx).output)
    assert "2 alerts dismissed" in to_text(run_line("alert -c", ictx).output)
    assert "no alerts" in to_text(run_line("alert --list", ictx).output)
    with pytest.raises(PipelineError, match="message is needed"):
        run_line("alert", ictx)
    ictx.interactive = False
    ictx.allow_stateful = False
    with pytest.raises(PipelineError):
        run_line("alert over mcp", ictx)  # stateful: never through shellcraft_pipeline


def test_nested_shell_forwards_to_the_main_shell(ictx, bus, monkeypatch):
    monkeypatch.setenv("SHELLCRAFT_DEPTH", "1")
    monkeypatch.setenv(alertmod.SUB_ENV, "3:shellcraft --no-banner")
    out = to_text(run_line("alert -l warn from inside", ictx).output)
    assert "sent to the main shell" in out
    assert _wait(lambda: any(a.message == "from inside" and a.level == "warn"
                             and a.source == "sub 3 (shellcraft --no-banner) shellcraft [1]" for a in bus.current()))
    with pytest.raises(PipelineError, match="main ShellCraft"):
        run_line("alert --list", ictx)


def test_panel_lines_show_alerts_first_with_their_source(bus):
    from shellcraft.footer import panel_lines

    bus.post("all good", "info", source="main shell")
    bus.post("look here", "crit", source="sub 1 (bash)")
    lines = panel_lines([], 0, None, 80, alerts=bus.current())
    assert [row.style for row in lines] == ["alert.crit", "alert.info"]  # no stack without jobs
    assert lines[0][2].startswith(" look here · from sub 1 (bash)") and lines[0][2].rstrip().endswith("crit  ×")
    assert lines[1][2].startswith(" all good · from main shell") and "info · " in lines[1][2]
    long = AlertBus()
    long.post("x" * 200, "warn")
    text = panel_lines([], 0, None, 60, alerts=long.current())[0][2]
    assert "…" in text and len(text) <= 60


class _Job:
    def __init__(self, number, command="bash", running=True, status=0):
        self.number, self.command, self.running, self.status = number, command, running, status
        self.label = "running" if running else ("done" if status == 0 else f"exit {status}")

    def short(self, width=18):
        return self.command[:width]


def test_panel_folds_alerts_into_a_badge_and_shows_a_subshells_alert_inline():
    from shellcraft.footer import alert_rows, panel_lines

    bus = AlertBus()
    info = bus.post("Backup finished", "info", source="sub 1 (bash --norc)")
    warn = bus.post("Disk /home at 91%", "warn", source="sub 1 (bash --norc)")
    crit = bus.post("Payment API is down", "crit", source="mcp · claude")
    assert (info.job, warn.job, crit.job) == (1, 1, None)
    jobs = [_Job(1, "bash --norc"), _Job(2, "find / -name x", running=False)]
    rows = panel_lines(jobs, 0, None, 80, alerts=bus.current())
    assert [r.style for r in rows] == ["alert.crit", "current", "running", "done"]  # critical keeps its row
    main = rows[1]
    assert main.text.startswith(" ● main") and main.text.rstrip().endswith("● 1  ● 1  ● 1 ▸") and main.badge > 0
    assert [(main.text[s:e], style, role) for s, e, style, role in main.spans] == [
        (" ▾", None, "fold"), ("● 1", "alert.crit.mark", "badge"), ("● 1", "alert.warn.mark", "badge"),
        ("● 1", "alert.info.mark", "badge"), (" ▸ ", None, "badge")]
    bash = rows[2]
    assert bash.text.startswith(" ○ 1  bash --norc  ● Disk /home at 91% +1") and bash.text.rstrip() == bash.text.rstrip()
    assert "running" not in bash.text and bash.spans[0][2] == "alert.warn.mark"
    assert rows[3].text.rstrip().endswith("done  ×") and "s ·" not in rows[3].text  # no time on the right
    # unfolded: every alert gets a row, the badge shows ▾
    assert bus.toggle() is True
    rows = panel_lines(jobs, 0, None, 80, alerts=bus.current(), expanded=True)
    assert [r.style for r in rows][:3] == ["alert.crit", "alert.warn", "alert.info"]
    assert rows[3].text.rstrip().endswith("▾") and [r.alert for r in rows[:3]] == [crit, warn, info]
    assert alert_rows(bus.current(), jobs, False) == [crit] and alert_rows(bus.current(), [], False) == [crit, warn, info]
    # the × at the right end of every alert row is its close button, as on a finished subshell's row
    hovered = panel_lines(jobs, 0, warn, 80, alerts=bus.current(), expanded=True)
    end = len(hovered[1].text) - 2
    assert hovered[1].hovered and hovered[1].text.endswith("warn · 60s  × ") and hovered[1].close == end
    assert hovered[1].spans == ((end - 1, end + 2, None, "close"),) and hovered[1].text[end] == "×"
    assert not hovered[1].text.startswith(" ×")  # no icon on the left any more
    assert not hovered[0].hovered and hovered[0].text[hovered[0].close] == "×"
    # only critical alerts: no ▸, nothing to fold
    bus.remove(warn) and bus.remove(info)
    rows = panel_lines(jobs, 0, None, 80, alerts=bus.current())
    assert rows[1].text.rstrip().endswith("● 1") and not bus.remove(info)
    bus.close()


def test_footer_mouse_hover_close_right_click_and_badge(home):
    from rich.console import Console

    from shellcraft.footer import Footer, parse_mouse
    from shellcraft.themes import UI, all_themes

    bus = AlertBus()
    warn = bus.post("disk", "warn", source="sub 1 (bash)")
    crit = bus.post("down", "crit", source="pid 7")
    jobs = [_Job(1, "bash")]
    footer = Footer(UI(all_themes({})["nord"], Console(file=io.StringIO())), jobs, alerts=bus)
    footer.size = lambda: (24, 80)
    footer.enabled = True
    assert footer.height() == 3  # the critical row, main, bash
    footer.sequence()
    assert footer.row_tabs == [None, 0, 1] and footer.mouse("move", 5, 20) is None  # above the footer
    assert parse_mouse("\x1b[<2;5;22m") == ("up", 5, 22, "right") and parse_mouse("\x1b[<35;5;22M") == ("move", 5, 22, "none")
    assert footer.mouse("move", 5, 22) == ("handled", None) and footer.hover is crit
    assert footer.mouse("up", 5, 22, "left") == ("handled", None) and crit in bus.current()  # a left click elsewhere: nothing
    assert footer.mouse("up", 79, 22, "left") == ("handled", None) and crit not in bus.current()  # the ×
    assert footer.height() == 2 and footer.row_tabs == [0, 1]
    assert footer.mouse("up", 79, 23, "left") == ("handled", None) and bus.expanded  # the badge on main
    assert footer.row_tabs == [None, 0, 1] and footer.rows[0].alert is warn
    assert footer.mouse("up", 10, 22, "right") == ("handled", None) and bus.current() == [warn]  # right-click: copy
    assert bus.clip == "disk" and bus.copied is warn and footer.rows[0].text.rstrip().endswith("warn · 60s  ×")
    assert footer.mouse("move", 10, 22) == ("handled", None) and "copied · Alt-v pastes" in footer.rows[0].text
    assert footer.mouse("move", 10, 23) == ("handled", None) and bus.copied is None  # the hint goes with the mouse
    assert footer.mouse("up", 79, 22, "left") == ("handled", None) and not bus.current()  # the × dismisses
    assert footer.row_tabs == [0, 1]
    assert footer.mouse("up", 5, 23, "left") == ("switch", 0) and footer.mouse("up", 5, 24) == ("switch", 1)
    assert footer.mouse("up", 5, 24, "right") == ("handled", None)
    bus.close()


def test_content_travels_through_the_spool_and_the_clip(bus, home, ictx, capsys):
    """-C keeps a payload with the alert; a right-click copies it; Alt-v, Ctrl-] v, clip and
    $CLIP give it back."""
    written = alertmod.send("errors in the log", "warn", 30, source="sub 1 (bash)", content="/var/log/app.log")
    assert json.loads(written.read_text())["content"] == "/var/log/app.log"
    assert _wait(lambda: bus.current() and bus.current()[0].content == "/var/log/app.log")
    assert bus.copy(bus.current()[0]) == "/var/log/app.log" and bus.clip == "/var/log/app.log"
    bus.copy(bus.post("no payload", "info"))
    assert bus.clip == "no payload"  # the message when there is no content
    # the command-line client: -C -, from stdin
    import io as iomod

    monkey_stdin = iomod.StringIO("line 1\nline 2\n")
    import sys as sysmod

    real = sysmod.stdin
    sysmod.stdin = monkey_stdin
    try:
        assert alertmod.main(["-l", "crit", "build failed", "-d", "-"]) == 0
    finally:
        sysmod.stdin = real
    assert _wait(lambda: any(a.message == "build failed" and a.content == "line 1\nline 2\n" for a in bus.current()))
    # the builtin: -C, -C - and --list; the clip builtin and $CLIP
    run_line("alert -l warn -d /tmp/x.json 'see the file'", ictx)
    assert ictx.alerts.current()[0].content == "/tmp/x.json"
    run_line("echo payload | alert piped -d -", ictx)
    assert any(a.content == "payload\n" for a in ictx.alerts.current())
    assert "11 characters of content" in to_text(run_line("alert --list", ictx).output)  # the --list hint
    ictx.alerts.copy(ictx.alerts.current()[0])
    assert to_text(run_line("clip", ictx).output) == "/tmp/x.json\n"
    assert to_text(run_line("echo $CLIP", ictx).output) == "/tmp/x.json\n"
    assert to_text(run_line("clip | wc -c", ictx).output).strip() == "12"
    run_line("clip hello there", ictx)
    assert ictx.alerts.clip == "hello there"
    run_line("echo from a pipe | clip", ictx)
    assert ictx.alerts.clip == "from a pipe" and to_text(run_line("clip -c", ictx).output) == ""
    assert ictx.alerts.clip == "" and to_text(run_line("clip", ictx).output) == ""
    assert to_text(run_line("echo [$CLIP]", ictx).output) == "[]\n"  # an empty clip is an empty variable
    assert not run_line("alert --list", ictx).status


def test_mcp_tool_send_tui_alert(bus, registry):
    import anyio
    from mcp.client import Client

    from shellcraft.mcp_server import ALERT_TOOL, build_server

    def call(fn):
        async def main():
            async with Client(build_server(registry)) as client:
                return await fn(client)
        return anyio.run(main)

    tools = {t.name: t for t in call(lambda c: c.list_tools()).tools}
    assert ALERT_TOOL in tools and tools[ALERT_TOOL].input_schema["required"] == ["message"]
    assert tools[ALERT_TOOL].input_schema["properties"]["severity"]["enum"] == ["info", "warn", "crit", "warning", "critical"]
    ok = call(lambda c: c.call_tool(ALERT_TOOL, {"message": "model says hi", "severity": "warn",
                                                 "timeout_seconds": 20, "content": "cat /tmp/report.md"}))
    assert not ok.is_error and "alert shown (warn)" in ok.content[0].text and "with content" in ok.content[0].text
    assert _wait(lambda: any(a.message == "model says hi" and a.timeout == 20.0 and a.source.startswith("mcp · ")
                             and a.content == "cat /tmp/report.md" for a in bus.current()))
    assert "content" in tools[ALERT_TOOL].input_schema["properties"]
    legacy = call(lambda c: c.call_tool(ALERT_TOOL, {"message": "old client", "severity": "critical"}))
    assert not legacy.is_error and "alert shown (crit)" in legacy.content[0].text  # the long name still works
    bad_content = call(lambda c: c.call_tool(ALERT_TOOL, {"message": "x", "content": 5}))
    assert bad_content.is_error and "content" in bad_content.content[0].text
    bad = call(lambda c: c.call_tool(ALERT_TOOL, {"message": "x", "severity": "loud"}))
    assert bad.is_error and "severity" in bad.content[0].text
    empty = call(lambda c: c.call_tool(ALERT_TOOL, {"message": "   "}))
    assert empty.is_error and "required" in empty.content[0].text


def test_a_nested_shell_asks_main_for_a_subshell(bus, home, ictx, monkeypatch, tmp_path):
    """`sub` in a lite shell writes a request into the spool; the main shell's bus hands it to
    on_request instead of posting an alert; the attach loop and the prompt open it."""
    got: list = []
    bus.on_request = got.append
    path = alertmod.request_sub(["pwsh", "-NoLogo"], str(tmp_path))
    assert json.loads(path.read_text())["kind"] == "sub"
    assert _wait(lambda: got) and got[0]["args"] == ["pwsh", "-NoLogo"] and got[0]["cwd"] == str(tmp_path)
    assert bus.current() == [] and _wait(lambda: not list(bus.spool.glob("*.json")))
    # the builtin, in a nested shell
    monkeypatch.setenv("SHELLCRAFT_DEPTH", "1")
    monkeypatch.setenv(alertmod.SUB_ENV, "2:shellcraft")
    out = to_text(run_line("sub pwsh -NoLogo", ictx).output)
    assert "asked the main shell to open sub pwsh -NoLogo in the background" in out
    assert _wait(lambda: len(got) == 2) and got[1]["source"] == "sub 2 (shellcraft)"
    from shellcraft import builtins as bmod
    from shellcraft.jobs import JobTable

    ictx.jobs = JobTable(tmp_path / "jobs")
    with pytest.raises(PipelineError, match="only the main ShellCraft"):
        run_line("jobs", ictx)
    monkeypatch.delenv("SHELLCRAFT_DEPTH")
    # the main shell opens the requests in the background, never attached
    ictx.jobs.requests.append({"args": ["echo", "from", "a", "subshell"], "cwd": str(tmp_path), "source": "sub 2 (shellcraft)"})
    ictx.jobs.requests.append({"args": ["/bin/sleep", "5"], "cwd": "/nonexistent"})
    ictx.jobs.requests.append({"args": ["no-such-program-xyz"], "cwd": str(tmp_path)})
    notes = bmod.open_requested(ictx)
    assert notes[0].startswith("sub echo from a subshell (asked by sub 2 (shellcraft)): ") and "background" in notes[0]
    assert notes[1].startswith("[2] sub /bin/sleep 5 (asked by a subshell) started in the background (Ctrl-] 2 switches")
    assert "not a command" in notes[2] and len(ictx.jobs) == 2 and ictx.jobs.get(2).running  # nothing attached
    monkeypatch.setattr(bmod, "_attach", lambda ctx, job: pytest.fail("a requested subshell must not be attached"))
    assert bmod.open_requested(ictx) == [] and not ictx.jobs.requests
    ictx.jobs.kill(ictx.jobs.get(2))


def test_a_lite_subshell_uses_the_main_shells_clip_over_the_link(monkeypatch):
    """The main shell answers over its private channel; a lite shell's register is main's."""
    from multiprocessing.connection import Client

    from shellcraft import jobs as jobmod
    from shellcraft import mainlink

    main = AlertBus()
    link = mainlink.MainLink(main)
    try:
        for name, value in link.env.items():
            monkeypatch.setenv(name, value)
        lite = AlertBus()
        lite.remote_clip = mainlink.available()
        main.copy(main.post("errors", "crit", content="/var/log/app.log"))
        assert lite.clip == "/var/log/app.log"  # copied in main, read in the subshell
        lite.clip = "from the subshell"
        assert main.clip == "from the subshell"  # set in the subshell, seen by main
        assert mainlink.ask(("nonsense",), "default") == "default"
        family = "AF_PIPE" if sys.platform == "win32" else "AF_UNIX"
        from multiprocessing import AuthenticationError

        with pytest.raises(AuthenticationError):
            Client(link.address, family=family, authkey=b"wrong key")  # no key, no answer
        # only `sub shellcraft` is given the key
        jobmod.MAIN_LINK_ENV.update(link.env)
        try:
            env = jobmod.nested_shellcraft([])[1]
            assert env[mainlink.ADDRESS_ENV] == str(link.address) and env[mainlink.KEY_ENV] == link.key.hex()
        finally:
            jobmod.MAIN_LINK_ENV.clear()
    finally:
        link.close()
    assert lite.clip == "from the subshell"  # main gone: the subshell's own copy
    monkeypatch.delenv(mainlink.ADDRESS_ENV)
    assert not mainlink.available() and mainlink.ask(("clip-get",), "none") == "none"
    main.close()


def test_the_clip_history_keeps_ten_copies_newest_first(ictx, monkeypatch):
    from shellcraft import mainlink

    bus = ictx.alerts
    for name in ("first", "second", "third"):
        bus.copy(bus.post(name, "crit", content=f"/var/log/{name}.log"))
    assert bus.clips() == ["/var/log/third.log", "/var/log/second.log", "/var/log/first.log"]
    assert to_text(run_line("echo $CLIP $CLIP1 $CLIP2 [$CLIP9]", ictx).output) == \
        "/var/log/third.log /var/log/second.log /var/log/first.log []\n"
    assert to_text(run_line("echo $CLIP0", ictx).output) == "/var/log/third.log\n"
    assert to_text(run_line("clip 2", ictx).output) == "/var/log/first.log\n"
    listed = to_text(run_line("clip -l", ictx).output)
    assert listed.splitlines()[0].startswith("0\t/var/log/third.log") and "2\t/var/log/first.log" in listed
    assert "starts with the clip 1" in to_text(run_line("clip -i 1", ictx).output)
    assert ictx.prefill == "/var/log/second.log"
    with pytest.raises(PipelineError, match="no copy 7"):
        run_line("clip -i 7", ictx)
    bus.clip = "/var/log/first.log"  # copied again: to the front, not twice
    assert bus.clips() == ["/var/log/first.log", "/var/log/third.log", "/var/log/second.log"]
    for n in range(12):
        bus.clip = f"text {n}"
    assert len(bus.clips()) == 10 and bus.clip == "text 11" and bus.clip_at(9) == "text 2"
    run_line("clip -c", ictx)
    assert bus.clips() == [] and to_text(run_line("clip -l", ictx).output).startswith("no copies")
    # a lite subshell reads and pushes the main shell's history
    main = AlertBus()
    link = mainlink.MainLink(main)
    try:
        for name, value in link.env.items():
            monkeypatch.setenv(name, value)
        lite = AlertBus()
        lite.remote_clip = True
        main.clip = "older"
        main.clip = "newer"
        assert lite.clip_at(1) == "older" and lite.clips() == ["newer", "older"]
        lite.clip = "from the subshell"
        assert main.clips() == ["from the subshell", "newer", "older"]
    finally:
        link.close()
        main.close()


def test_short_level_names_old_names_still_work_and_d_carries_the_data(bus, home, ictx):
    assert alertmod.LEVELS == ("info", "warn", "crit")
    assert alertmod.normalize_level("warning") == "warn" and alertmod.normalize_level("CRITICAL") == "crit"
    assert alertmod.normalize_level("loud") is None
    assert alertmod.parse_args(["-l", "warning", "old", "name"])[1] == "warn"  # scripts written for 2.0
    assert alertmod.parse_args(["-l", "crit", "-d", "/tmp/x", "down"])[1::4] == ("crit", "/tmp/x")
    assert alertmod.parse_args(["--content", "kept", "m"])[5] == "kept"  # the old long name
    with pytest.raises(SystemExit):
        alertmod.parse_args(["-C", "/tmp/x", "down"])  # -C is gone: no more mixing it up with -c
    with pytest.raises(SystemExit):
        alertmod.parse_args(["-l", "loud", "m"])
    # a spool file from an older shell, with the long name
    (bus.spool / "0-old.json").write_text(json.dumps({"time": time.time(), "message": "old style",
                                                       "level": "critical"}))
    assert _wait(lambda: any(a.message == "old style" and a.level == "crit" for a in bus.current()))
    run_line("alert -l crit -d /var/log/a.log down", ictx)
    shown = ictx.alerts.current()[0]
    assert shown.level == "crit" and shown.content == "/var/log/a.log"
    assert "[crit] down" in to_text(run_line("alert --list", ictx).output)
    from shellcraft.builtins import BUILTINS

    assert BUILTINS["alert"].usage.startswith("alert [-l LEVEL (info,warn,crit)] [-t SECONDS | -s] [-d DATA]")
