"""A2: a PTY session streams real output live; write() reaches the child's stdin
(the takeover path)."""

import asyncio
import sys

from podium.ids import new_id
from podium.sessions.pty import PtySession
from podium.sink import Sink
from podium.workers.mock import MOCK_CLI


async def _drain_until(q: asyncio.Queue, needle: str, timeout: float = 10.0) -> str:
    seen = ""
    async with asyncio.timeout(timeout):
        while needle not in seen:
            frame = await q.get()
            if frame.get("type") == "output":
                seen += frame["text"]
    return seen


async def test_stream_and_takeover(tmp_path):
    sink = Sink()
    q = sink.subscribe()
    sess = PtySession(new_id("s"), "mock", str(tmp_path), sink,
                      [sys.executable, "-u", str(MOCK_CLI), "[[ask]] [[nocommit]]"])
    await sess.start()
    await _drain_until(q, "QUESTION: proceed?")
    assert sess.status == "running"
    await sess.write("yes\r")                      # human takeover path
    seen = await _drain_until(q, "ANSWER:yes")
    assert "mock-cli ready" in sess.backlog()
    assert "ANSWER:yes" in seen
    assert await sess.wait() == 0
    assert sess.status == "exited"


async def test_stop_terminates(tmp_path):
    sink = Sink()
    sess = PtySession(new_id("s"), "mock", str(tmp_path), sink,
                      [sys.executable, "-u", str(MOCK_CLI), "[[ask]] [[nocommit]]"])
    await sess.start()
    await asyncio.sleep(0.2)
    await sess.stop()
    assert sess.status in ("exited", "error")


async def test_exec_failure_is_error_status(tmp_path):
    sink = Sink()
    sess = PtySession(new_id("s"), "mock", str(tmp_path), sink,
                      ["definitely-not-a-binary-xyz"])
    await sess.start()
    assert await sess.wait() == 127
    assert sess.status == "error"
