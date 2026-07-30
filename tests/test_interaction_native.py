"""Spec 012 B-criteria: native approval callbacks surfaced as one uniform prompt.
No quota, no network: the SDK path uses an injected fake client; the ACP path runs
the scripted mock agent over real pipes; the wire path runs a real gateway."""

import asyncio
import sys

import pytest
import websockets

from podium import protocol
from podium.interaction import CANCELLED, Approvals
from podium.sessions.acp import AcpSession
from podium.sessions.sdk import SdkSession
from podium.workers.base import WORKERS, WorkerUnavailable
from podium.workers.mock import MOCK_ACP_AGENT


@pytest.fixture
def layer(sink):
    return Approvals(sink)


@pytest.fixture
def frames(sink):
    out: list[dict] = []
    sink.tap(out.append)
    return out


async def _until(predicate, timeout=10.0):
    async with asyncio.timeout(timeout):
        while True:
            found = predicate()
            if found:
                return found
            await asyncio.sleep(0.01)


def _first(frames, type_):
    return next((f for f in frames if f["type"] == type_), None)


# --- B1: the uniform layer ---------------------------------------------------

async def test_ask_emits_uniform_prompt_and_answer_resolves(layer, frames):
    waiter = asyncio.create_task(layer.ask(
        "s1", title="claude: allow tool Bash?", detail="{\"command\": \"ls\"}"))
    req = await _until(lambda: _first(frames, "approval.request"))
    request = req["request"]
    assert request["session_id"] == "s1"
    assert [o["id"] for o in request["options"]] == ["allow", "deny"]
    assert layer.pending() == [request]

    layer.answer(request["id"], "allow", actor="sujal")
    assert await waiter == "allow"
    resolved = _first(frames, "approval.resolved")
    assert (resolved["request_id"], resolved["value"], resolved["actor"]) == (
        request["id"], "allow", "sujal")
    assert layer.pending() == []


async def test_answer_validates_options_and_request_ids(layer, frames):
    waiter = asyncio.create_task(layer.ask("s1", title="allow?"))
    req = await _until(lambda: _first(frames, "approval.request"))
    with pytest.raises(KeyError):
        layer.answer("q_nope", "allow")
    with pytest.raises(ValueError):
        layer.answer(req["request"]["id"], "maybe")
    with pytest.raises(KeyError):  # session mismatch is a miss, not a resolve
        layer.answer(req["request"]["id"], "allow", session_id="other")
    layer.answer(req["request"]["id"], "deny")
    assert await waiter == "deny"


async def test_cancel_session_resolves_pending_as_cancelled(layer, frames):
    w1 = asyncio.create_task(layer.ask("s1", title="one"))
    w2 = asyncio.create_task(layer.ask("s1", title="two"))
    await _until(lambda: len(layer.pending("s1")) == 2)
    layer.cancel_session("s1")
    assert await w1 == CANCELLED and await w2 == CANCELLED
    assert layer.pending() == []
    resolved = [f for f in frames if f["type"] == "approval.resolved"]
    assert {(f["value"], f["actor"]) for f in resolved} == {("cancelled", "system")}


# --- B2: SDK can_use_tool → uniform prompt ------------------------------------

class FakeBlock:
    def __init__(self, text):
        self.text = text


class FakeAssistant:
    def __init__(self, text):
        self.content = [FakeBlock(text)]


class ResultMessage:  # name matters: the session duck-types on it
    content = []
    subtype = "success"


class FakeSdkClient:
    def __init__(self):
        self.queries: list[str] = []
        self.mode = None
        self._messages: asyncio.Queue = asyncio.Queue()

    async def connect(self):
        pass

    async def query(self, text):
        self.queries.append(text)

    async def receive_messages(self):
        while True:
            msg = await self._messages.get()
            if msg is None:
                return
            yield msg

    async def set_permission_mode(self, mode):
        self.mode = mode

    async def disconnect(self):
        self._messages.put_nowait(None)


async def test_sdk_session_uniform_prompt_and_permission_mapping(sink, layer, frames):
    client = FakeSdkClient()
    sess = SdkSession("s_sdk", "claude", "/tmp", sink, layer, prompt="do the task",
                      client_factory=lambda s: client)
    await sess.start()
    assert client.queries == ["do the task"]

    decision = asyncio.create_task(sess.can_use_tool("Bash", {"command": "rm -rf"}))
    req = await _until(lambda: _first(frames, "approval.request"))
    assert "Bash" in req["request"]["title"]
    assert "rm -rf" in req["request"]["detail"]
    assert sess.status == "waiting_input"

    layer.answer(req["request"]["id"], "deny")
    assert await decision == {"behavior": "deny"}  # fake-module PermissionResult shape
    assert sess.status == "running"

    client._messages.put_nowait(FakeAssistant("hello from claude"))
    client._messages.put_nowait(ResultMessage())
    await _until(lambda: "hello from claude" in sess.backlog())
    await _until(lambda: sess.status == "waiting_input")

    await sess.set_mode("acceptEdits")
    assert client.mode == "acceptEdits"
    await sess.stop()
    assert sess.status == "exited" and await sess.wait() == 0


async def test_sdk_cancelled_prompt_maps_to_deny(sink, layer, frames):
    client = FakeSdkClient()
    sess = SdkSession("s_sdk", "claude", "/tmp", sink, layer,
                      client_factory=lambda s: client)
    await sess.start()
    decision = asyncio.create_task(sess.can_use_tool("Edit", {}))
    await _until(lambda: layer.pending("s_sdk"))
    await sess.stop()  # teardown cancels pending → deny, no hang
    assert await decision == {"behavior": "deny"}


async def test_sdk_kind_without_package_is_honest(sink, layer, tmp_path):
    worker = WORKERS["claude"]()
    with pytest.raises(WorkerUnavailable, match="podium\\[sdk\\]"):
        worker.make_session("s1", sink, str(tmp_path), "hi", kind="sdk",
                            interaction=layer)


# --- B3: ACP request_permission + set_mode -------------------------------------

def _acp(sid, sink, layer, tmp_path, prompt=""):
    return AcpSession(sid, "mock", str(tmp_path), sink, layer,
                      [sys.executable, "-u", str(MOCK_ACP_AGENT)], prompt=prompt)


async def test_acp_uniform_prompt_round_trip(sink, layer, frames, tmp_path):
    sess = _acp("s_acp", sink, layer, tmp_path, prompt="[[ask]] may I write?")
    await sess.start()
    assert sess.mode == "default" and "yolo" in sess.modes

    req = await _until(lambda: _first(frames, "approval.request"))
    request = req["request"]
    assert [o["id"] for o in request["options"]] == ["allow_once", "reject_once"]
    assert "write podium-acp.txt" in request["title"]

    layer.answer(request["id"], "allow_once")
    await _until(lambda: "DECISION:allow_once" in sess.backlog())
    await _until(lambda: sess.status == "waiting_input")

    await sess.set_mode("yolo")
    await _until(lambda: "[mode] yolo" in sess.backlog())
    assert sess.mode == "yolo"

    await sess.write("thanks")
    await _until(lambda: "MOCK-ACP:thanks" in sess.backlog())
    await sess.stop()
    assert layer.pending() == []


async def test_acp_session_end_cancels_pending_prompt(sink, layer, frames, tmp_path):
    sess = _acp("s_acp2", sink, layer, tmp_path, prompt="[[ask]] pending forever")
    await sess.start()
    await _until(lambda: layer.pending("s_acp2"))
    await sess.stop()
    assert layer.pending() == []
    assert _first(frames, "approval.resolved")["value"] == "cancelled"


async def test_acp_cancel_answer_reaches_agent(sink, layer, frames, tmp_path):
    sess = _acp("s_acp3", sink, layer, tmp_path, prompt="[[ask]] your call")
    await sess.start()
    await _until(lambda: layer.pending("s_acp3"))
    layer.cancel_session("s_acp3")
    await _until(lambda: "DECISION:cancelled" in sess.backlog())
    await sess.stop()


# --- B4/B5: one prompt over the wire, honest degradation ------------------------

@pytest.fixture
async def daemon(tmp_path):
    from podium.gateway import Daemon
    d = Daemon(state_db=str(tmp_path / "state.db"), workdir=str(tmp_path / "work"))
    server = await d.serve(host="127.0.0.1", port=0)
    port = server.sockets[0].getsockname()[1]
    yield d, f"ws://127.0.0.1:{port}"
    server.close()
    await server.wait_closed()
    d.state.close()


async def _client(url):
    ws = await websockets.connect(url)
    await ws.recv()  # hello
    await ws.recv()  # snapshot
    return ws


async def _recv_type(ws, type_, timeout=15.0):
    async with asyncio.timeout(timeout):
        while True:
            frame = protocol.loads(await ws.recv())
            if frame["type"] == type_:
                return frame


async def test_uniform_prompt_over_the_wire(daemon, tmp_path):
    d, url = daemon
    ws = await _client(url)
    await ws.send(protocol.dumps({"type": "spawn", "worker": "mock", "kind": "acp",
                                  "prompt": "[[ask]] over the wire",
                                  "cwd": str(tmp_path)}))
    spawned = await _recv_type(ws, "spawned")
    sid = spawned["session"]["id"]

    req = (await _recv_type(ws, "approval.request"))["request"]
    assert req["session_id"] == sid

    await ws.send(protocol.dumps({"type": "interaction.pending"}))
    pending = await _recv_type(ws, "approval.pending")
    assert [r["id"] for r in pending["requests"]] == [req["id"]]

    await ws.send(protocol.dumps({"type": "answer.native", "session_id": sid,
                                  "request_id": req["id"], "value": "allow_once"}))
    await _recv_type(ws, "answer.ack")
    resolved = await _recv_type(ws, "approval.resolved")
    assert resolved["value"] == "allow_once"

    seen = ""
    async with asyncio.timeout(15):
        while "DECISION:allow_once" not in seen:
            frame = protocol.loads(await ws.recv())
            if frame["type"] == "output" and frame["session_id"] == sid:
                seen += frame["text"]

    await ws.send(protocol.dumps({"type": "session.mode", "session_id": sid,
                                  "mode": "acceptEdits"}))
    mode = await _recv_type(ws, "mode.set")
    assert mode["mode"] == "acceptEdits"

    await ws.send(protocol.dumps({"type": "interaction.pending"}))
    assert (await _recv_type(ws, "approval.pending"))["requests"] == []
    await ws.send(protocol.dumps({"type": "stop", "session_id": sid}))
    await ws.close()


async def test_interaction_surface_is_live_and_errors_are_errors(daemon):
    d, url = daemon
    ws = await _client(url)
    await ws.send(protocol.dumps({"type": "surfaces"}))
    surfaces = await _recv_type(ws, "surfaces")
    interaction = next(s for s in surfaces["surfaces"] if s["key"] == "interaction")
    assert interaction["live"] is True

    # unknown request id → error, never blocked (the surface is live now)
    await ws.send(protocol.dumps({"type": "answer.native", "session_id": "s?",
                                  "request_id": "q_nope", "value": "allow"}))
    err = await _recv_type(ws, "error")
    assert "q_nope" in err["message"]
    await ws.close()


async def test_pty_answer_path_unchanged_and_mode_is_honest(daemon, tmp_path):
    d, url = daemon
    ws = await _client(url)
    await ws.send(protocol.dumps({"type": "spawn", "worker": "mock",
                                  "prompt": "[[ask]] [[nocommit]]",
                                  "cwd": str(tmp_path)}))
    sid = (await _recv_type(ws, "spawned"))["session"]["id"]
    seen = ""
    async with asyncio.timeout(15):
        while "QUESTION: proceed?" not in seen:
            frame = protocol.loads(await ws.recv())
            if frame["type"] == "output" and frame["session_id"] == sid:
                seen += frame["text"]
    # Stage-A answer frame still lands in the PTY when nothing native is pending
    await ws.send(protocol.dumps({"type": "answer", "session_id": sid, "value": "y"}))
    async with asyncio.timeout(15):
        while "ANSWER:y" not in seen:
            frame = protocol.loads(await ws.recv())
            if frame["type"] == "output" and frame["session_id"] == sid:
                seen += frame["text"]
    # A PTY session has no native mode channel; a mode outside the CLI's own
    # cycle ring answers an error — never a silent no-op (spec 006 handles the
    # supervised/autonomous/bypass/plan ring via shift+tab).
    await ws.send(protocol.dumps({"type": "session.mode", "session_id": sid,
                                  "mode": "acceptEdits"}))
    err = await _recv_type(ws, "error")
    assert "acceptEdits" in err["message"]
    await ws.send(protocol.dumps({"type": "stop", "session_id": sid}))
    await ws.close()


async def test_unknown_kind_is_refused(sink, layer, tmp_path):
    worker = WORKERS["mock"]()
    with pytest.raises(WorkerUnavailable, match="no session kind"):
        worker.make_session("s1", sink, str(tmp_path), "hi", kind="warp",
                            interaction=layer)
