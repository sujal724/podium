"""A2: a PTY session streams real output live; write() reaches the child's stdin
(the takeover path)."""

import asyncio
import sys

import pytest

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


async def test_split_utf8_across_reads_not_corrupted(tmp_path):
    """Regression: a multibyte glyph split across PTY reads must not become
    replacement-char garbage (the 'glitchy text' from the dogfood run)."""
    sink = Sink()
    outputs = []
    sink.tap(lambda f: outputs.append(f["text"]) if f.get("type") == "output" else None)
    sess = PtySession(new_id("s"), "mock", str(tmp_path), sink, ["true"])
    star = "✶".encode()          # 3 bytes
    sess._decoder  # decoder exists
    text1 = sess._decoder.decode(star[:2])
    text2 = sess._decoder.decode(star[2:])
    assert text1 == "" and text2 == "✶"   # held across reads, decoded whole
    assert "�" not in text1 + text2


async def test_exec_failure_is_error_status(tmp_path):
    sink = Sink()
    sess = PtySession(new_id("s"), "mock", str(tmp_path), sink,
                      ["definitely-not-a-binary-xyz"])
    await sess.start()
    assert await sess.wait() == 127
    assert sess.status == "error"


async def test_real_workers_require_tmux_and_say_so(tmp_path, monkeypatch):
    """Spec 010 rev 2: coding workers always run in a REAL terminal. Podium never
    re-renders a worker's UI, so a missing multiplexer is a stated error rather
    than a silent downgrade to an emulated pane (decision 44)."""
    import podium.workers.claude as cw
    from podium.sessions.tmux import TmuxSession
    sess = cw.ClaudeWorker().make_session("s_t", Sink(), str(tmp_path), "go")
    assert isinstance(sess, TmuxSession) and sess.kind == "tmux"
    assert sess.attach_command() == ["tmux", "attach", "-t", "podium-s_t"]


async def test_tmux_session_reports_missing_tmux(tmp_path, monkeypatch):
    from podium.sessions import tmux as tmod
    monkeypatch.setattr(tmod, "available", lambda: False)
    sess = tmod.TmuxSession("s_x", "claude", str(tmp_path), Sink(), ["claude"])
    with pytest.raises(tmod.TmuxUnavailable, match="not installed"):
        await sess.start()
