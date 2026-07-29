"""A4 (honest surfaces over the wire) + the loop driven end-to-end through the
WebSocket gateway, exactly as the TUI/CLI drive it."""

import asyncio
import json

import pytest
import websockets

from podium import protocol
from podium.gateway import Daemon


@pytest.fixture
async def daemon(tmp_path, repo):
    d = Daemon(state_db=str(tmp_path / "state.db"), workdir=str(tmp_path / "work"))
    d.dispatcher.default_worker = "mock"
    server = await d.serve(host="127.0.0.1", port=0)
    port = server.sockets[0].getsockname()[1]
    yield d, f"ws://127.0.0.1:{port}", repo
    server.close()
    await server.wait_closed()
    d.state.close()


async def _client(url):
    ws = await websockets.connect(url)
    hello = protocol.loads(await ws.recv())
    snap = protocol.loads(await ws.recv())
    assert hello["type"] == "hello" and snap["type"] == "snapshot"
    return ws, hello


async def _recv_type(ws, type_, timeout=15.0):
    async with asyncio.timeout(timeout):
        while True:
            frame = protocol.loads(await ws.recv())
            if frame["type"] == type_:
                return frame


async def test_hello_reports_worker_posture(daemon):
    d, url, repo = daemon
    ws, hello = await _client(url)
    workers = {w["name"]: w for w in hello["workers"]}
    assert "codex" in workers  # registered from day one (A7)
    if not workers["codex"]["ok"]:
        assert workers["codex"]["hint"]  # blocked-with-hint, never silent
    assert workers["mock"]["ok"]
    await ws.close()


async def test_blocked_frames_answer_stage(daemon):
    d, url, repo = daemon
    ws, _ = await _client(url)
    for frame_type, surface, stage in [
        ("tms.sync", "tms", "B"),
        ("library.search", "library", "B"),
        ("autonomy.set", "autopilot", "C"),
        ("team.create", "teams", "C"),
        ("session.promote", "handoff", "C"),
    ]:
        await ws.send(protocol.dumps({"type": frame_type}))
        reply = await _recv_type(ws, "blocked")
        assert (reply["surface"], reply["stage"]) == (surface, stage)
    await ws.close()


async def test_unknown_frame_is_error(daemon):
    d, url, repo = daemon
    ws, _ = await _client(url)
    await ws.send(protocol.dumps({"type": "no.such.frame"}))
    reply = await _recv_type(ws, "error")
    assert "unknown frame" in reply["message"]
    await ws.close()


async def test_quota_gauge_honest_over_wire(daemon):
    d, url, repo = daemon
    ws, _ = await _client(url)
    await ws.send(protocol.dumps({"type": "quota.query"}))
    reply = await _recv_type(ws, "quota.snapshot")
    assert reply["workers"]["claude"]["window_5h"] == "unavailable"
    await ws.close()


async def test_full_loop_over_wire(daemon):
    d, url, repo = daemon
    ws, _ = await _client(url)

    async def create(entity, fields):
        await ws.send(protocol.dumps(
            {"type": "work.create", "entity": entity, "fields": fields}))
        return (await _recv_type(ws, "work.created"))["id"]

    wid = await create("workspace", {"name": "w"})
    pid = await create("project", {"workspace_id": wid, "name": "p",
                                   "repo_root": str(repo)})
    tid = await create("task", {"project_id": pid, "title": "wire task",
                                "status": "ready"})
    await ws.send(protocol.dumps({"type": "task.run", "task_id": tid,
                                  "worker": "mock"}))
    review = await _recv_type(ws, "review.ready")
    assert review["task_id"] == tid
    assert "+" in review["diff"]
    await ws.send(protocol.dumps({"type": "review.approve", "task_id": tid,
                                  "actor": "sujal"}))
    async with asyncio.timeout(10):
        while True:
            frame = await _recv_type(ws, "task.updated")
            if frame["task"]["id"] == tid and frame["task"]["status"] == "done":
                break
    assert (repo / "podium-task.txt").exists()
    await ws.close()


async def test_live_output_and_takeover_over_wire(daemon):
    d, url, repo = daemon
    ws, _ = await _client(url)
    await ws.send(protocol.dumps({"type": "spawn", "worker": "mock",
                                  "prompt": "[[ask]] [[nocommit]]",
                                  "cwd": str(repo)}))
    spawned = await _recv_type(ws, "spawned")
    sid = spawned["session"]["id"]
    seen = ""
    async with asyncio.timeout(15):
        while "QUESTION: proceed?" not in seen:
            frame = protocol.loads(await ws.recv())
            if frame["type"] == "output" and frame["session_id"] == sid:
                seen += frame["text"]
    await ws.send(protocol.dumps({"type": "agent.send", "session_id": sid,
                                  "text": "y\r"}))
    async with asyncio.timeout(15):
        while "ANSWER:y" not in seen:
            frame = protocol.loads(await ws.recv())
            if frame["type"] == "output" and frame["session_id"] == sid:
                seen += frame["text"]
    await ws.close()


async def test_events_persisted(daemon):
    d, url, repo = daemon
    ws, _ = await _client(url)
    await ws.send(protocol.dumps({"type": "work.create", "entity": "workspace",
                                  "fields": {"name": "w2"}}))
    wid = (await _recv_type(ws, "work.created"))["id"]
    await ws.send(protocol.dumps({"type": "work.create", "entity": "task",
                                  "fields": {"title": "evt", "status": "ready"}}))
    await _recv_type(ws, "work.created")
    rows = d.state.query("SELECT data FROM events WHERE type='task.updated'")
    assert rows, "sink tap should persist broadcast frames to the events table"
    assert json.loads(rows[-1]["data"])["task"]["title"] == "evt"
    assert wid
    await ws.close()
