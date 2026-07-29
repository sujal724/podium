"""A1 (the bootstrap loop) + A6 (auto-resume seed), end-to-end with the mock worker:
claim → worktree → PTY run → commit → review(diff) → approve merges / reject re-queues.
"""

import asyncio
from pathlib import Path

import pytest

from podium.dispatcher import Dispatcher, DispatchError
from podium.manager import SessionManager
from podium.metering import Meter
from podium.workspace import WorkspaceManager


@pytest.fixture
def rig(state, sink, work, repo, tmp_path):
    manager = SessionManager(sink, state)
    wm = WorkspaceManager(str(tmp_path / "work"))
    meter = Meter(state, sink)
    disp = Dispatcher(work, manager, wm, meter, sink, state, default_worker="mock")
    ws = work.create_workspace("w")
    proj = work.create_project(ws, "p", repo_root=str(repo), base_branch="main")
    return disp, work, proj, repo


async def _await_status(work, task_id, statuses, timeout=15.0):
    async with asyncio.timeout(timeout):
        while work.get_task(task_id).status not in statuses:
            await asyncio.sleep(0.05)
    return work.get_task(task_id).status


async def test_full_loop_approve(rig):
    disp, work, proj, repo = rig
    t = work.create_task(proj, "write the file", status="ready")
    await disp.run_task(t)
    assert await _await_status(work, t, ("review", "blocked")) == "review"
    await disp.approve(t, actor="sujal")
    assert work.get_task(t).status == "done"
    assert (repo / "podium-task.txt").exists()
    # ledger got the session
    assert work.state.query("SELECT * FROM quota_ledger WHERE worker='mock'")


async def test_reject_requeues_with_feedback_then_approve(rig):
    disp, work, proj, repo = rig
    t = work.create_task(proj, "first attempt", status="ready")
    await disp.run_task(t)
    await _await_status(work, t, ("review",))
    await disp.reject(t, "please also do X", actor="sujal")
    assert work.get_task(t).status == "ready"
    assert work.feedback(t)[0]["feedback"] == "please also do X"
    # retry: prompt now carries the feedback; loop completes again
    await disp.run_task(t)
    await _await_status(work, t, ("review",))
    await disp.approve(t)
    assert work.get_task(t).status == "done"


async def test_no_commit_blocks(rig):
    disp, work, proj, repo = rig
    t = work.create_task(proj, "[[nocommit]] just chat", status="ready")
    await disp.run_task(t)
    assert await _await_status(work, t, ("blocked",)) == "blocked"


async def test_rate_limited_worker_defers_dispatch(rig):
    disp, work, proj, repo = rig
    t1 = work.create_task(proj, "[[ratelimit]] [[nocommit]]", status="ready")
    await disp.run_task(t1)
    await _await_status(work, t1, ("blocked",))
    assert not disp.meter.gauge.may_dispatch("mock")
    t2 = work.create_task(proj, "next", status="ready")
    with pytest.raises(DispatchError):
        await disp.run_task(t2)
    assert await disp.run_next() is None


async def test_unclaimable_task_raises(rig):
    disp, work, proj, repo = rig
    t = work.create_task(proj, "still backlog")  # not triaged
    with pytest.raises(DispatchError):
        await disp.run_task(t)


async def test_interrupt_all_requeues_instead_of_blocking(rig):
    """D1 (spec 004): an update/shutdown interrupt must not run the finish path —
    the task stays `running` and boot auto-resume re-queues it (the v0.3.2 dogfood
    flaw: it went `blocked` with 'no commits')."""
    disp, work, proj, repo = rig
    t = work.create_task(proj, "[[ask]] [[nocommit]] long running", status="ready")
    await disp.run_task(t)
    async with asyncio.timeout(15):
        while not disp.manager.sessions:
            await asyncio.sleep(0.05)
    await _await_status(work, t, ("running",))
    interrupted = await disp.interrupt_all("update")
    assert interrupted == [t]
    for sid in list(disp.manager.sessions):
        await disp.manager.stop(sid)
    await asyncio.sleep(0.2)  # any stray finish-path would land here
    assert work.get_task(t).status == "running"      # interrupted, NOT blocked
    assert disp.resume_interrupted() == [t]
    assert work.get_task(t).status == "ready"


async def test_hook_capture_stores_resume_key(rig, tmp_path):
    """D2: the claude session id arriving via hook events lands in
    sessions.resume_key."""
    disp, work, proj, repo = rig
    t = work.create_task(proj, "[[ask]] [[nocommit]] capture", status="ready")
    await disp.run_task(t)
    async with asyncio.timeout(15):
        while not disp.manager.sessions:
            await asyncio.sleep(0.05)
    sid = next(iter(disp.manager.sessions))
    wt = work.get_task(t).worktree
    (Path(wt) / ".podium").mkdir(exist_ok=True)
    (Path(wt) / ".podium" / "hooks.jsonl").write_text(
        '{"hook_event_name": "PreToolUse", "session_id": "abcd-1234"}\n')
    async with asyncio.timeout(10):
        while True:
            rows = disp.state.query("SELECT resume_key FROM sessions WHERE id=?",
                                    (sid,))
            if rows and rows[0]["resume_key"] == "abcd-1234":
                break
            await asyncio.sleep(0.1)
    await disp.interrupt_all()
    for s in list(disp.manager.sessions):
        await disp.manager.stop(s)


async def test_spawn_uses_cockpit_winsize(rig):
    """C6 (spec 003 rev 2): new PTYs spawn at the cockpit's pane size, so the CLI
    never paints for a width the pane doesn't have."""
    disp, work, proj, repo = rig
    disp.winsize = lambda: (33, 155)
    t = work.create_task(proj, "[[ask]] [[nocommit]] sized", status="ready")
    await disp.run_task(t)
    async with asyncio.timeout(15):
        while not disp.manager.sessions:
            await asyncio.sleep(0.05)
    sess = next(iter(disp.manager.sessions.values()))
    assert (sess._rows, sess._cols) == (33, 155)
    await disp.interrupt_all()
    await disp.manager.stop(sess.id)


async def test_resume_interrupted(rig):
    disp, work, proj, repo = rig
    t = work.create_task(proj, "was running", status="ready")
    claimed = work.claim("mock", t)
    work.set_status(claimed.id, "running")
    requeued = disp.resume_interrupted()
    assert requeued == [t]
    assert work.get_task(t).status == "ready"


async def test_run_next_picks_best_ready(rig):
    disp, work, proj, repo = rig
    work.create_task(proj, "low prio", priority=3, status="ready")
    high = work.create_task(proj, "high prio", priority=0, status="ready")
    tid = await disp.run_next()
    assert tid == high
    await _await_status(work, high, ("review",))
    await disp.approve(high)
