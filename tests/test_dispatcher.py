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


async def test_resume_key_only_after_interrupt(rig):
    """E4: interrupted work continues the same worker conversation; rejected work
    starts fresh so the operator's feedback reshapes it."""
    disp, work, proj, repo = rig
    t = work.create_task(proj, "resume policy", status="ready")
    disp.state.execute(
        "INSERT INTO sessions(id,task_id,worker,kind,cwd,status,resume_key,created_at)"
        " VALUES('s_old',?,'mock','pty','/tmp','exited','claude-uuid-9',1)", (t,))
    assert disp.resume_key_for(t, "mock") is None          # nothing happened yet
    work._log(t, "interrupted", "system", {"reason": "update"})
    assert disp.resume_key_for(t, "mock") == "claude-uuid-9"
    work._log(t, "review.rejected", "human", {"feedback": "wrong approach"})
    assert disp.resume_key_for(t, "mock") is None          # rejection ⇒ fresh start


async def test_autonomy_resolution_order(rig):
    """E1: task overrides project overrides daemon default; junk falls back safe."""
    disp, work, proj, repo = rig
    t = work.create_task(proj, "modes", status="ready")
    task = work.get_task(t)
    assert disp.autonomy_for(task, {"autonomy": None}) == "supervised"
    assert disp.autonomy_for(task, {"autonomy": "autonomous"}) == "autonomous"
    work.update_task(t, autonomy="supervised")
    assert disp.autonomy_for(work.get_task(t), {"autonomy": "autonomous"}) == "supervised"
    work.update_task(t, autonomy="nonsense")
    assert disp.autonomy_for(work.get_task(t), {"autonomy": "bogus"}) == "supervised"


async def test_agent_tree_records_sessions_subagents_and_peers(rig):
    """Spec 007: for a task, Podium can show every actor — the session it spawned,
    the subagents that session spawned, and any peer-harness attempt (which bypass
    mode cannot hide, because the shim reports before refusing)."""
    disp, work, proj, repo = rig
    t = work.create_task(proj, "[[ask]] [[nocommit]] tree", status="ready")
    await disp.run_task(t)
    async with asyncio.timeout(15):
        while not disp.manager.sessions:
            await asyncio.sleep(0.05)
    sid = next(iter(disp.manager.sessions))
    wt = work.get_task(t).worktree
    pdir = Path(wt) / ".podium"
    pdir.mkdir(exist_ok=True)
    (pdir / "hooks.jsonl").write_text(
        '{"hook_event_name":"PreToolUse","tool_name":"Task","session_id":"c-1",'
        '"tool_input":{"subagent_type":"explorer","description":"map the repo"}}\n'
        '{"hook_event_name":"SubagentStop","session_id":"c-1"}\n')
    (pdir / "peer.jsonl").write_text(
        '{"binary":"gemini","session":"%s","command":"gemini -p hi",'
        '"outcome":"blocked"}\n' % sid)
    async with asyncio.timeout(10):
        while True:
            kinds = [a["kind"] for a in disp.agents.tree(t)]
            if {"session", "subagent", "peer"} <= set(kinds):
                break
            await asyncio.sleep(0.1)
    tree = disp.agents.tree(t)
    session_node = next(a for a in tree if a["kind"] == "session")
    subagent = next(a for a in tree if a["kind"] == "subagent")
    peer = next(a for a in tree if a["kind"] == "peer")
    assert subagent["parent_id"] == session_node["id"] and subagent["depth"] == 1
    assert subagent["label"] == "explorer" and subagent["status"] == "done"
    assert peer["parent_id"] == session_node["id"]      # attributed to its spawner
    assert peer["status"] == "blocked" and "gemini -p hi" in peer["detail"]
    await disp.interrupt_all()
    for s in list(disp.manager.sessions):
        await disp.manager.stop(s)


async def test_parallel_subagents_pair_by_tool_use_id(rig):
    """Claude runs subagents in PARALLEL; closing 'the latest running one' would
    pair them wrongly. Correlate by the worker's own tool_use_id."""
    disp, work, proj, repo = rig
    t = work.create_task(proj, "[[ask]] [[nocommit]] parallel", status="ready")
    await disp.run_task(t)
    async with asyncio.timeout(15):
        while not disp.manager.sessions:
            await asyncio.sleep(0.05)
    sid = next(iter(disp.manager.sessions))
    a = disp.agents
    a.open_subagent(sid, "explorer", "map repo", key="toolu_A")
    a.open_subagent(sid, "reviewer", "check tests", key="toolu_B")
    a.close_subagent(sid, "toolu_A")            # the FIRST one finishes first
    tree = {n["label"]: n["status"] for n in a.tree(t) if n["kind"] == "subagent"}
    assert tree == {"explorer": "done", "reviewer": "running"}
    a.close_subagent(sid, "toolu_B")
    tree = {n["label"]: n["status"] for n in a.tree(t) if n["kind"] == "subagent"}
    assert tree == {"explorer": "done", "reviewer": "done"}
    await disp.interrupt_all()
    for s in list(disp.manager.sessions):
        await disp.manager.stop(s)


async def test_scope_tree_is_the_full_hierarchy(rig):
    """F4: workspace → project → task → session → subagent/peer in one view."""
    disp, work, proj, repo = rig
    t = work.create_task(proj, "[[nocommit]] scoped", status="ready")
    disp.agents.open_session("s_fake", t, "claude", detail="scoped")
    disp.agents.open_subagent("s_fake", "explorer", key="k1")
    disp.agents.record_peer("s_fake", "gemini", "gemini -p hi")
    nodes = disp.agents.scope_tree(work)
    kinds = [(n["kind"], n["depth"]) for n in nodes]
    assert ("workspace", 0) in kinds and ("project", 1) in kinds
    assert ("task", 2) in kinds and ("session", 3) in kinds
    assert ("subagent", 4) in kinds and ("peer", 4) in kinds


async def test_parent_cannot_merge_before_subtasks(rig):
    """G1 (spec 008): children branch off the parent's branch and approve deletes
    that branch — so the parent must wait for them."""
    disp, work, proj, repo = rig
    parent = work.create_task(proj, "parent feature", status="ready")
    child = work.create_task(proj, "child bit", parent_id=parent, status="ready")
    await disp.run_task(parent)
    await _await_status(work, parent, ("review",))
    with pytest.raises(DispatchError, match="unfinished subtasks"):
        await disp.approve(parent)
    work.set_status(child, "done")
    await disp.approve(parent)                      # now it may land
    assert work.get_task(parent).status == "done"


async def test_subtask_branches_off_parent_branch(rig):
    """G2: a subtask's base is its parent's branch, created even if the parent
    hasn't run yet — so base → parent → child stacks."""
    disp, work, proj, repo = rig
    parent = work.create_task(proj, "parent", status="ready")
    child = work.create_task(proj, "child", parent_id=parent, status="ready")
    base = disp.base_ref_for(work.get_task(child), disp._project(work.get_task(child)))
    assert base == f"task/{parent}"
    grandchild = work.create_task(proj, "grandchild", parent_id=child, status="ready")
    assert disp.base_ref_for(work.get_task(grandchild),
                             disp._project(work.get_task(grandchild))) == f"task/{child}"
    # an explicit override wins over the parent chain
    work.update_task(grandchild, base_ref="release/2.0")
    assert disp.base_ref_for(work.get_task(grandchild),
                             disp._project(work.get_task(grandchild))) == "release/2.0"


async def test_arbitrary_depth_subtasks(rig):
    """G5: subtasks nest arbitrarily deep — bases chain all the way down, the merge
    guard sees DESCENDANTS (not just direct children), and the hierarchy stays a tree."""
    from podium.work.store import CycleError
    disp, work, proj, repo = rig
    ids, parent = [], None
    for i in range(5):                                   # 5 levels deep
        parent = work.create_task(proj, f"level {i}", parent_id=parent, status="ready")
        ids.append(parent)
    p = disp._project(work.get_task(ids[-1]))
    for i in range(1, 5):                                # each stacks on its parent
        assert disp.base_ref_for(work.get_task(ids[i]), p) == f"task/{ids[i - 1]}"
    assert disp.base_ref_for(work.get_task(ids[0]), p) == "main"
    # the ROOT refuses to merge while a deep descendant is unfinished
    await disp.run_task(ids[0])
    await _await_status(work, ids[0], ("review",))
    with pytest.raises(DispatchError, match=ids[-1]):
        await disp.approve(ids[0])
    for tid in ids[1:]:
        work.set_status(tid, "done")
    await disp.approve(ids[0])
    assert work.get_task(ids[0]).status == "done"
    # and the hierarchy must stay a tree
    with pytest.raises(CycleError):
        work.update_task(ids[0], parent_id=ids[-1])
    with pytest.raises(CycleError):
        work.update_task(ids[2], parent_id=ids[2])


async def test_scope_tree_nests_subtasks(rig):
    """G6: the whole-hierarchy view indents subtasks under their parents."""
    disp, work, proj, repo = rig
    a = work.create_task(proj, "A", status="ready")
    b = work.create_task(proj, "B", parent_id=a, status="ready")
    work.create_task(proj, "B1", parent_id=b, status="ready")
    depths = {n["label"]: n["depth"] for n in disp.agents.scope_tree(work)
              if n["kind"] == "task"}
    assert depths["A"] == 2 and depths["B"] == 3 and depths["B1"] == 4
