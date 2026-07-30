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


async def test_worker_dialog_round_trip_over_wire(daemon):
    """B1: the mock renders a Claude-style trust dialog → Podium raises a uniform
    question → the operator's answer lands in the PTY → the session proceeds."""
    d, url, repo = daemon
    ws, _ = await _client(url)
    await ws.send(protocol.dumps({"type": "spawn", "worker": "mock",
                                  "prompt": "[[trust]] [[nocommit]]",
                                  "cwd": str(repo)}))
    sid = (await _recv_type(ws, "spawned"))["session"]["id"]
    q = await _recv_type(ws, "question")
    assert q["session_id"] == sid
    assert q["question"]["choices"][0].startswith("Yes")
    await ws.send(protocol.dumps({"type": "answer", "session_id": sid,
                                  "question_id": q["question"]["id"], "value": "1"}))
    seen = ""
    async with asyncio.timeout(15):
        while "TRUSTED" not in seen:
            frame = protocol.loads(await ws.recv())
            if frame["type"] == "output" and frame["session_id"] == sid:
                seen += frame["text"]
    await ws.close()


async def test_update_status_over_wire(daemon):
    d, url, repo = daemon
    ws, _ = await _client(url)
    await ws.send(protocol.dumps({"type": "update.status"}))
    st = await _recv_type(ws, "update.status")
    assert "installed" in st
    assert st.get("available") in (True, False)  # honest either way, never absent
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


async def test_review_queue_and_pending_and_gc(daemon):
    """Specs 015/016: everything awaiting the operator is a QUEUE (not one
    variable), missed approvals are recoverable, and dead sessions are collapsed."""
    d, url, repo = daemon
    ws = d.work.create_workspace("w")
    proj = d.work.create_project(ws, "p", repo_root=str(repo))
    t1 = d.work.create_task(proj, "first", status="ready")
    t2 = d.work.create_task(proj, "second", status="ready")
    d.work.set_status(t1, "review")
    d.work.set_status(t2, "review")

    q = await d.dispatch_frame({"type": "review.queue"})
    ids = [x["id"] for x in q["tasks"]]
    assert t1 in ids and t2 in ids                     # both actionable, not just one
    assert all(x["merge_target"] == "main" for x in q["tasks"])
    assert all(x["branch"].startswith("task/") for x in q["tasks"])

    # a pending approval survives being missed
    d.interaction.scan("s_q", "claude", "Do you want to proceed?\n1. Yes\n2. No\n")
    pend = await d.dispatch_frame({"type": "approvals.pending"})
    assert pend["questions"] and pend["questions"][0]["choices"][:1] == ["Yes"]

    # gc archives all but the newest N dead sessions per task
    for i in range(5):
        d.state.execute(
            "INSERT INTO sessions(id,task_id,worker,kind,cwd,status,created_at)"
            " VALUES(?,?,'claude','tmux','/tmp','exited',?)", (f"s_{i}", t1, i))
    res = await d.dispatch_frame({"type": "sessions.gc", "keep": 2})
    assert res["archived"] == 3
    live = d.state.query("SELECT id FROM sessions WHERE task_id=? AND status='exited'",
                         (t1,))
    assert len(live) == 2


async def test_session_target_points_at_the_real_terminal(daemon):
    """Spec 014: a client asks WHERE a session's terminal is instead of stealing
    its own pane."""
    d, url, repo = daemon
    ws = d.work.create_workspace("w")
    proj = d.work.create_project(ws, "p", repo_root=str(repo))
    t = d.work.create_task(proj, "x", status="ready")
    d.state.execute(
        "INSERT INTO sessions(id,task_id,worker,kind,cwd,status,resume_key,created_at)"
        " VALUES('s_tg',?,'claude','tmux',?,'running','uuid-1',1)", (t, str(repo)))
    tgt = await d.dispatch_frame({"type": "session.target", "task_id": t})
    assert tgt["tmux"] == "podium-s_tg"
    assert tgt["attach"] == ["tmux", "attach", "-t", "podium-s_tg"]
    assert tgt["resume"] == ["claude", "--resume", "uuid-1"]
    assert tgt["live"] is False        # no tmux here — stated, not faked


async def test_review_queue_is_answerable_after_a_restart(daemon):
    """Dogfood: the cockpit's review pane only filled from the live `review.ready`
    broadcast, so work that finished before you connected was invisible. The queue
    must be QUERYABLE, not just announced."""
    d, url, repo = daemon
    ws_id = d.work.create_workspace("w")
    proj = d.work.create_project(ws_id, "p", repo_root=str(repo))
    t = d.work.create_task(proj, "finished while you were away", status="ready")
    d.work.set_status(t, "review")           # finished before any client connected
    ws, _ = await _client(url)               # connect AFTER the fact
    await ws.send(protocol.dumps({"type": "review.queue"}))
    snap = await _recv_type(ws, "review.snapshot")
    assert [x["id"] for x in snap["tasks"]] == [t]
    assert snap["tasks"][0]["merge_target"] == "main"
    await ws.close()


def test_cli_has_no_duplicate_subcommands():
    """A merge added a second `pending` subparser; argparse raised on registration,
    so EVERY podium command crashed before doing anything. Guard the parser itself."""
    import argparse
    from unittest.mock import patch
    from podium import cli
    seen = []
    real = argparse._SubParsersAction.add_parser

    def spy(self, name, **kw):
        assert name not in seen, f"duplicate subcommand: {name}"
        seen.append(name)
        return real(self, name, **kw)

    with patch.object(argparse._SubParsersAction, "add_parser", spy), \
            patch("sys.argv", ["podium", "--help"]), \
            pytest.raises(SystemExit):
        cli.main()
    assert "pending" in seen and len(seen) == len(set(seen))


def test_board_groups_by_what_needs_attention():
    """Operator finding: a flat list with the status in brackets made 'is anything
    done? running? waiting on me?' unanswerable at a glance. Group by attention."""
    from podium.cli import GROUPS
    order = [name for name, _, _ in GROUPS]
    assert order[0] == "needs you" and order[-1] == "done"
    needs_you = next(st for name, _, st in GROUPS if name == "needs you")
    assert {"review", "blocked", "proposed"} == set(needs_you)
    running = next(st for name, _, st in GROUPS if name == "running")
    assert "running" in running and "verifying" in running
    # every lifecycle status has exactly one home — nothing can vanish from the board
    from podium.work.store import STATUSES
    homes = [st for _, _, sts in GROUPS for st in sts]
    assert len(homes) == len(set(homes)), "a status may not appear in two groups"
    missing = set(STATUSES) - set(homes) - {"discarded"}
    assert not missing, f"statuses with no group: {missing}"


async def test_port_probe_does_not_false_positive(tmp_path, repo):
    """The pre-bind check must probe with the SAME options asyncio uses. Without
    SO_REUSEADDR it reported a phantom 'already in use' after a daemon was killed —
    an error no pkill could clear, because the port was genuinely free."""
    import socket
    from podium.gateway import Daemon
    # hold a socket the way a just-stopped daemon leaves one: bound with REUSEADDR
    holder = socket.socket()
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind(("127.0.0.1", 0))
    port = holder.getsockname()[1]
    holder.close()                      # released, but recently used

    d = Daemon(state_db=str(tmp_path / "s.db"), workdir=str(tmp_path / "w"))
    server = await d.serve(host="127.0.0.1", port=port)   # must NOT raise SystemExit
    assert server.sockets[0].getsockname()[1] == port
    server.close()
    await server.wait_closed()
    d.state.close()
