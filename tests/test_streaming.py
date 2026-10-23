"""Streaming between pipeline segments: segments run at the same time, lines flow as produced,
an early-finishing reader stops its producers, errors and statuses keep their old meaning."""

import _thread
import threading
import time

import pytest

from shellcraft import streams
from shellcraft.context import Styled, to_text
from shellcraft.pipeline import PipelineError, run_line


def out(line, ctx):
    return to_text(run_line(line, ctx).output)


def test_yes_head_returns_at_once(ctx):
    started = time.monotonic()
    assert out("yes | head -3", ctx) == "y\ny\ny\n"
    assert out("yes hello | head -n 2", ctx) == "hello\nhello\n"
    assert time.monotonic() - started < 3


def test_a_slow_producer_is_stopped_when_head_has_enough(ctx, registry, tmp_path):
    from shellcraft.loader import ModuleRegistry

    stopped = threading.Event()
    (tmp_path / "slowgen.py").write_text(
        "import time\n"
        "STOPPED = []\n"
        "def run(args, stdin):\n"
        "    try:\n"
        "        for i in range(100):\n"
        "            yield f'line{i}'\n"
        "            time.sleep(0.2)\n"
        "    finally:\n"
        "        STOPPED.append(True)\n")
    ctx.registry = ModuleRegistry(tmp_path)
    ctx.registry.load()
    started = time.monotonic()
    assert out("slowgen | head -1", ctx) == "line0\n"
    assert time.monotonic() - started < 3  # not 100 × 0.2 s
    import sys

    module = sys.modules["shellcraft_modules.slowgen"]
    assert _wait(lambda: module.STOPPED == [True])  # the generator was closed
    stopped.set()


def _wait(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_order_and_whole_results_are_kept(ctx, tmp_path):
    (tmp_path / "f.txt").write_text("".join(f"{i}\n" for i in range(500)))
    assert out("cat f.txt | grep 4 | head -3", ctx) == "4\n14\n24\n"
    assert out("cat f.txt | wc -l", ctx).strip() == "500"
    assert out("cat f.txt | tail -2", ctx) == "498\n499\n"
    assert out("cat f.txt | cat | cat | head -2", ctx) == "0\n1\n"
    # a final grep that finished quickly still returns one styled value (colors, the pager)
    result = run_line("cat f.txt | grep -n 49", ctx)
    assert isinstance(result.output, Styled) and to_text(result.output).startswith("50:49\n")
    assert out("cat f.txt | tee copy.txt | head -1", ctx) == "0\n"
    assert (tmp_path / "copy.txt").read_text().startswith("0\n")


def test_errors_and_statuses_keep_their_meaning(ctx):
    with pytest.raises(PipelineError) as exc:
        run_line("nosuchcommand | head -1", ctx)
    assert exc.value.index == 1 and exc.value.total == 2 and exc.value.status == 127
    assert run_line("echo hi | grep zzz", ctx).status == 1
    assert run_line("echo hi | grep zzz | cat", ctx).status == 0  # the last command's, as in a shell
    assert out("nosuchcommand 2>&1 | grep -c found", ctx) == "1\n"
    assert out("echo a b | grep -q a", ctx) == "" and run_line("echo a | grep -q a", ctx).status == 0


def test_mcp_style_contexts_still_collect(ctx):
    ctx.allow_stateful = ctx.allow_writes = ctx.allow_redirect = False
    assert out("yes | head -2", ctx) == "y\ny\n"
    assert out("echo x | cat", ctx) == "x\n"


def test_tail_f_into_grep_streams_matches_live(ctx, tmp_path):
    path = tmp_path / "app.log"
    path.write_text("start\n")
    seen = []
    ctx.interactive = True
    ctx.live = lambda item: seen.append(to_text(item))

    def writer():
        time.sleep(0.5)
        with open(path, "a") as fh:
            fh.write("ok\nERROR one\n")
        time.sleep(0.6)
        with open(path, "a") as fh:
            fh.write("ERROR two\nok\n")
        time.sleep(0.8)
        _thread.interrupt_main()

    threading.Thread(target=writer, daemon=True).start()
    with pytest.raises(KeyboardInterrupt):
        run_line("tail -f app.log | grep ERROR", ctx)
    assert "".join(seen) == "ERROR one\nERROR two\n" and ctx.last_status == 130


def test_lines_join_partial_chunks():
    cancel = threading.Event()
    channel = streams.Channel(cancel)
    for chunk in ("ab", "c\nde", "f\n", "tail"):
        channel.put(chunk)
    channel.end()
    assert list(streams.Lines(channel)) == ["abc\n", "def\n", "tail"]
    assert streams.Lines("x\ny").read() == "x\ny"
    failing = streams.Channel(cancel)
    failing.put("one\n")
    failing.fail(PipelineError(1, 2, "a", "boom"))
    with pytest.raises(streams.UpstreamFailed):
        list(streams.Lines(failing))
    cancel.set()
    with pytest.raises(streams.Cancelled):
        streams.Channel(cancel).put("x")


def test_a_module_checking_modkit_cancellation_stops_when_the_pipeline_does(tmp_path, monkeypatch):
    """One mechanism: the pipeline's stop flag is the module's modkit cancel event."""
    import time

    from shellcraft.context import ShellContext
    from shellcraft.loader import ModuleRegistry

    (tmp_path / "ticker.py").write_text(
        "from shellcraft import modkit\n"
        "def run(args, stdin):\n"
        "    n = 0\n"
        "    while True:\n"
        "        modkit.check_cancel()\n"
        "        n += 1\n"
        "        yield f'tick {n}\\n'\n"
        "        modkit.sleep(0.01)\n")
    registry = ModuleRegistry(tmp_path)
    registry.load()
    ctx = ShellContext(registry=registry)
    started = time.monotonic()
    out = to_text(run_line("ticker | head -2", ctx).output)
    assert out == "tick 1\ntick 2\n" and time.monotonic() - started < 3


# ── batching between segments ────────────────────────────────────────────────

def test_lines_travel_between_segments_in_chunks():
    import threading

    from shellcraft.streams import BATCH_LINES, Batcher, Channel

    out = Channel(threading.Event())
    batch = Batcher(out)
    for n in range(BATCH_LINES + 10):
        batch.put(f"{n}\n")
    batch.close()
    out.end()
    kinds = []
    while (entry := out.get(timeout=1)) is not None and entry[0] != "end":
        kinds.append(entry[1].count("\n"))
    assert kinds == [BATCH_LINES, 10]


def test_a_single_slow_line_is_not_held_back():
    import threading

    from shellcraft.streams import BATCH_WAIT, Batcher, Channel

    out = Channel(threading.Event())
    batch = Batcher(out)
    started = time.monotonic()
    batch.put("one\n")
    entry = out.get(timeout=1)
    assert entry == ("data", "one\n") and time.monotonic() - started < BATCH_WAIT * 10
    batch.close()


def test_a_cancelled_pipeline_stops_the_batching_producer():
    import threading

    from shellcraft.streams import Batcher, Cancelled, Channel

    cancel = threading.Event()
    batch = Batcher(Channel(cancel))
    batch.put("a\n")
    cancel.set()
    with pytest.raises(Cancelled):
        batch.put("b\n")


def test_the_last_segment_hands_its_items_on_in_lists(ctx):
    from shellcraft.context import to_text

    result = run_line("seq 1 2000 | grep 1", ctx)
    assert to_text(result.output).count("\n") == len([n for n in range(1, 2001) if "1" in str(n)])
