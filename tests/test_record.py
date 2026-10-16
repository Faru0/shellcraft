"""record / script: the transcript, the console tee, the 🔴 REC prompt segment."""

import io

import pytest
from rich.console import Console

from shellcraft import recording
from shellcraft.context import to_text
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.themes import UI, all_themes


@pytest.fixture
def repl_shell(monkeypatch, tmp_path):
    from shellcraft import shell
    from shellcraft.context import ShellContext
    from shellcraft.loader import ModuleRegistry

    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shell, "create_prompt_toolkit_console", lambda cleanup: None)
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    ctx = ShellContext(registry=ModuleRegistry(tmp_path), ui=UI(all_themes({})["nord"], console), config={})
    with shell.Shell(ctx) as sh:
        yield sh, ctx


def test_recorder_strips_escapes_and_frames_the_session(tmp_path):
    rec = recording.Recorder(tmp_path / "t.txt")
    rec.command("echo hi")
    rec.output("\x1b[1;32mhi\x1b[0m\n")
    rec.output("partial")
    rec.note("subshell 1 attached")
    rec.command("ls")
    rec.output("a\r\nb\n\x1b]8;;http://x\x1b\\link\x1b]8;;\x1b\\\n")
    rec.close()
    rec.close()  # twice is fine
    text = (tmp_path / "t.txt").read_text()
    assert text.startswith("# ShellCraft session · started ")
    assert "] ❯ echo hi\nhi\npartial\n(subshell 1 attached)\n" in text
    assert "] ❯ ls\na\nb\nlink\n# ended " in text and "2 commands" in text


def test_record_builtin_tees_the_console_and_lights_the_prompt(tmp_path, monkeypatch, ctx):
    from shellcraft.footer import Footer

    raw = io.StringIO()
    ctx.ui = UI(all_themes({})["nord"], Console(file=raw, width=300))
    ctx.interactive = True
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    assert "not recording" in to_text(run_line("record status", ctx).output)
    with pytest.raises(PipelineError, match="not recording"):
        run_line("record stop", ctx)
    started = to_text(run_line("record", ctx).output)
    path = ctx.recording.path
    assert "recording to" in started and path.parent == tmp_path / "home" / "records" and path.exists()
    assert isinstance(ctx.ui.console.file, recording.Tee) and ctx.ui.console.file.raw is raw
    ctx.ui.console.print("[bold]hello[/] world")  # what a command prints: on the terminal and in the file
    ctx.recording.command("echo hello world")
    footer = Footer(ctx.ui, [], alerts=None)
    footer.size = lambda: (24, 80)
    footer.enabled = True
    footer.draw()  # the footer draws on the terminal only
    assert "main" in raw.getvalue() and "main" not in path.read_text()
    with pytest.raises(PipelineError, match="already recording"):
        run_line("record start other.txt", ctx)
    status = to_text(run_line("record status", ctx).output)
    assert str(path) in status.replace("\n", "") and "1 command" in status
    stopped = to_text(run_line("script stop", ctx).output)  # the Linux name is the same command
    assert "transcript saved" in stopped and ctx.recording is None and ctx.ui.console.file is raw
    text = path.read_text()
    assert "hello world\n" in text and "] ❯ echo hello world" in text and text.rstrip().endswith("1 command")
    # a chosen file, appended to; `record FILE` without `start`
    chosen = tmp_path / "notes.txt"
    chosen.write_text("old\n")
    run_line(f"script {chosen}", ctx)
    assert ctx.recording.path == chosen
    recording.stop(ctx)
    assert chosen.read_text().startswith("old\n# ShellCraft session")


def test_footer_bubbles_turn_red_for_the_shells_that_record(repl_shell, tmp_path, monkeypatch):
    from shellcraft import alerts as alertmod
    from shellcraft.footer import panel_lines
    from shellcraft.jobs import JobTable

    sh, ctx = repl_shell
    assert not [f for f in sh._prompt() if "REC" in f[1]]  # nothing in the prompt
    table = JobTable(tmp_path / "jobs")
    job = table.start_command("sleep", lambda: (0, ""))
    rows = panel_lines([job], 0, width=60, stack=True)
    red = (0, 3, "rec", "rec")
    assert rows[0].text.startswith(" ● main") and rows[1].text.startswith(" ○ 1  sleep") and red not in rows[0].spans
    recording.start(ctx, tmp_path / "s.txt")  # the main shell records: its own bubble, in red
    rows = panel_lines([job], 0, width=60, stack=True, recording=ctx.recording is not None)
    assert rows[0].text.startswith(" ● main") and red in rows[0].spans and red not in rows[1].spans
    rows = panel_lines([job], 1, width=60, stack=True, recording=True)  # attached to job 1: main not current
    assert rows[0].text.startswith(" ○ main") and red in rows[0].spans and rows[1].text.startswith(" ● 1 ")
    assert sh.footer.is_recording()
    # a subshell that records tells the main shell through the spool
    ctx.jobs = table
    sh._sub_recording({"kind": "recording", "on": True, "source": "sub 1 (shellcraft)"})
    assert job.recording
    rows = panel_lines([job], 1, width=60, stack=True)
    assert rows[1].text.startswith(" ● 1  sleep") and red in rows[1].spans
    rows = panel_lines([job], 0, width=60, stack=True)
    assert rows[1].text.startswith(" ○ 1  sleep") and red in rows[1].spans and red not in rows[0].spans
    sh._sub_recording({"kind": "recording", "on": False, "source": "sub 1 (shellcraft)"})
    assert not job.recording
    # the sender's side: inside a subshell, start / stop write the signal
    monkeypatch.setenv("SHELLCRAFT_DEPTH", "1")
    monkeypatch.setenv(alertmod.SUB_ENV, "1:shellcraft")
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    recording.stop(ctx)
    signals = sorted((tmp_path / "home" / "alerts").glob("*.json"))
    assert signals and '"kind": "recording"' in signals[-1].read_text() and '"on": false' in signals[-1].read_text()
    assert '"source": "sub 1 (shellcraft)"' in signals[-1].read_text()
    sh.close()  # leaving the shell ends a transcript still open
    assert ctx.recording is None and (tmp_path / "s.txt").read_text().rstrip().endswith("0 commands")
