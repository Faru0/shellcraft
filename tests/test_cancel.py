"""Cooperative cancellation: modkit.cancelled() / check_cancel() / sleep(), set by Ctrl-C in the
spinner runner and by an MCP call's timeout."""

import _thread
import threading
import time

import anyio
import pytest

from shellcraft import modkit
from shellcraft.context import ShellContext
from shellcraft.loader import ModuleRegistry


def test_the_api_outside_and_inside_a_cancel_scope():
    assert not modkit.cancelled()
    modkit.check_cancel()  # nothing to cancel: no error
    modkit.sleep(0.01)
    event = threading.Event()

    def body():
        assert not modkit.cancelled()
        event.set()
        assert modkit.cancelled()
        started = time.monotonic()
        with pytest.raises(modkit.Cancelled):
            modkit.sleep(30)  # wakes at once
        assert time.monotonic() - started < 1
        with pytest.raises(modkit.Cancelled):
            modkit.check_cancel()
        return "ran"

    assert modkit.run_cancellable(body, event) == "ran"
    assert not modkit.cancelled()  # the scope ends with the call
    assert issubclass(modkit.Cancelled, modkit.ModuleError)


def test_ctrl_c_at_the_prompt_cancels_the_running_module(tmp_path):
    from shellcraft.output import make_spinner_runner

    ctx = ShellContext(registry=ModuleRegistry(tmp_path), config={"settings": {"spinner": False}})
    runner = make_spinner_runner(ctx)
    seen = {}

    def module():
        try:
            for _ in range(300):
                modkit.check_cancel()
                time.sleep(0.01)
        except modkit.Cancelled:
            seen["cancelled"] = True
            raise
        return "finished"

    threading.Timer(0.2, _thread.interrupt_main).start()  # Ctrl-C while the module runs
    with pytest.raises(KeyboardInterrupt):
        runner("module", module)
    deadline = time.monotonic() + 2
    while "cancelled" not in seen and time.monotonic() < deadline:
        time.sleep(0.01)
    assert seen.get("cancelled") is True


def test_an_mcp_timeout_cancels_the_abandoned_thread():
    from shellcraft.mcp_server import _call

    seen = {}

    def slow_tool():
        try:
            for _ in range(500):
                modkit.check_cancel()
                time.sleep(0.01)
        except modkit.Cancelled:
            seen["cancelled"] = True
            raise
        return "finished"

    result = anyio.run(lambda: _call(slow_tool, timeout=0.2))
    assert result.is_error and "abandoned" in result.content[0].text
    deadline = time.monotonic() + 2
    while "cancelled" not in seen and time.monotonic() < deadline:
        time.sleep(0.01)
    assert seen.get("cancelled") is True
