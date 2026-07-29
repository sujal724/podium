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


def test_tmux_backend_selection_and_honest_fallback(monkeypatch, tmp_path):
    """I4 (spec 010): tmux is used for real terminal panes when available; when it
    isn't, Podium says so plainly and the emulated pane remains — never a fake."""
    import podium.workers.claude as cw
    from podium.sessions.tmux import TmuxSession
    from podium.sessions.pty import PtySession

    monkeypatch.setattr(cw.CONFIG, "term_backend", "auto")
    monkeypatch.setattr(cw, "tmux_available", lambda: True)
    sess = cw.ClaudeWorker().make_session("s_t", Sink(), str(tmp_path), "go")
    assert isinstance(sess, TmuxSession) and sess.kind == "tmux"
    assert sess.attach_command() == ["tmux", "attach", "-t", "podium-s_t"]

    monkeypatch.setattr(cw, "tmux_available", lambda: False)
    sess = cw.ClaudeWorker().make_session("s_p", Sink(), str(tmp_path), "go")
    assert isinstance(sess, PtySession) and sess.kind == "pty"

    monkeypatch.setattr(cw.CONFIG, "term_backend", "pty")
    monkeypatch.setattr(cw, "tmux_available", lambda: True)
    assert isinstance(cw.ClaudeWorker().make_session("s_f", Sink(), str(tmp_path),
                                                     "go"), PtySession)


async def test_tmux_session_reports_missing_tmux(tmp_path, monkeypatch):
    from podium.sessions import tmux as tmod
    monkeypatch.setattr(tmod, "available", lambda: False)
    sess = tmod.TmuxSession("s_x", "claude", str(tmp_path), Sink(), ["claude"])
    with pytest.raises(tmod.TmuxUnavailable, match="not installed"):
        await sess.start()
