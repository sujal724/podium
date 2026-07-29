"""A3: hierarchy CRUD, DAG + cycles, readiness, atomic claim, inbox provenance."""

import threading

import pytest

from podium.work.store import CycleError


def test_hierarchy_crud(work):
    ws = work.create_workspace("w1")
    p = work.create_project(ws, "proj", repo_root="/tmp/x")
    t = work.create_task(p, "do a thing", description="details", priority=1)
    sub = work.create_task(p, "subtask", parent_id=t)
    board = work.board()
    assert [w["id"] for w in board["workspaces"]] == [ws]
    assert board["projects"][0]["id"] == p
    ids = {task["id"] for task in board["tasks"]}
    assert {t, sub} <= ids
    assert work.get_task(sub).parent_id == t


def test_dependency_gates_readiness(work):
    p = work.create_project(work.create_workspace("w"), "p")
    a = work.create_task(p, "a", status="ready")
    b = work.create_task(p, "b", status="ready")
    work.add_dependency(b, a)  # b blocked by a
    ready = {t.id for t in work.ready_tasks()}
    assert a in ready and b not in ready
    work.set_status(a, "done")
    assert b in {t.id for t in work.ready_tasks()}


def test_cross_project_dependency_and_cycle_rejection(work):
    ws = work.create_workspace("w")
    p1, p2 = work.create_project(ws, "p1"), work.create_project(ws, "p2")
    a = work.create_task(p1, "a", status="ready")
    b = work.create_task(p2, "b", status="ready")
    work.add_dependency(b, a)          # cross-project edge OK (decision 43)
    with pytest.raises(CycleError):
        work.add_dependency(a, b)      # would close the loop
    with pytest.raises(CycleError):
        work.add_dependency(a, a)


def test_backlog_not_claimable(work):
    p = work.create_project(work.create_workspace("w"), "p")
    t = work.create_task(p, "untriaged")  # backlog
    assert work.ready_tasks() == []
    assert work.claim("claude") is None
    work.update_task(t, status="ready")
    assert work.claim("claude").id == t


def test_pull_order_priority_then_unblock_count(work):
    p = work.create_project(work.create_workspace("w"), "p")
    low = work.create_task(p, "low", priority=3, status="ready")
    high = work.create_task(p, "high", priority=0, status="ready")
    unblocker = work.create_task(p, "unblocker", priority=1, status="ready")
    plain = work.create_task(p, "plain", priority=1, status="ready")
    for _ in range(2):
        blocked = work.create_task(p, "blocked", status="ready")
        work.add_dependency(blocked, unblocker)
    order = [t.id for t in work.ready_tasks()]
    assert order[0] == high
    assert order.index(unblocker) < order.index(plain)
    assert order[-1] == low


def test_atomic_claim_single_owner(work):
    p = work.create_project(work.create_workspace("w"), "p")
    work.create_task(p, "only", status="ready")
    results = []

    def claimer(name):
        results.append(work.claim(name))

    threads = [threading.Thread(target=claimer, args=(f"w{i}",)) for i in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    wins = [r for r in results if r is not None]
    assert len(wins) == 1


def test_inbox_flow_with_provenance(work):
    p = work.create_project(work.create_workspace("w"), "p")
    tid = work.propose_task("fix flake", "verifier saw it", "verifier", project_id=p)
    t = work.get_task(tid)
    assert (t.status, t.origin, t.detector) == ("proposed", "auto", "verifier")
    # proposed never runs
    assert work.claim("claude") is None
    work.approve_task(tid, "sujal")
    row = work.state.query("SELECT approved_by FROM tasks WHERE id=?", (tid,))[0]
    assert row["approved_by"] == "sujal"
    assert work.claim("claude").id == tid


def test_inbox_reject(work):
    tid = work.propose_task("noise", "", "gap")
    work.reject_task(tid, "sujal", reason="not now")
    assert work.get_task(tid).status == "discarded"
    assert work.proposed_tasks() == []


def test_status_validation(work):
    p = work.create_project(work.create_workspace("w"), "p")
    t = work.create_task(p, "x")
    with pytest.raises(ValueError):
        work.update_task(t, status="nonsense")
    with pytest.raises(ValueError):
        work.create_task(p, "y", status="done")


def test_additive_migration_on_legacy_db(tmp_path):
    """Dogfood regression: CREATE TABLE IF NOT EXISTS never alters an existing
    table, so a column added in a later version was missing from a live database
    (v0.4.0's tasks.base_ref broke the board). Opening migrates additively."""
    import sqlite3
    from podium.sink import Sink
    from podium.state import StateStore
    from podium.work.store import WorkStore
    path = str(tmp_path / "legacy.db")
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE tasks(id TEXT PRIMARY KEY, project_id TEXT,"
               " parent_id TEXT, title TEXT, description TEXT, status TEXT,"
               " priority INT, labels JSON, assignee TEXT, origin TEXT,"
               " detector TEXT, approved_by TEXT, approved_at INT, resources JSON,"
               " workflow TEXT, autonomy TEXT, worktree TEXT, created_at INT,"
               " updated_at INT)")
    db.execute("INSERT INTO tasks(id,title,status,priority,origin)"
               " VALUES('t_old','legacy','ready',1,'human')")
    db.commit(); db.close()
    store = StateStore(path)
    cols = {r["name"] for r in store.query("PRAGMA table_info(tasks)")}
    assert "base_ref" in cols
    task = WorkStore(store, Sink()).get_task("t_old")   # would KeyError before
    assert task.title == "legacy" and task.base_ref is None
    store.close()
