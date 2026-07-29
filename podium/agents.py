"""Agent tree (spec 007) — Podium knows every actor working on a task.

One hierarchy per task: the worker **session** Podium spawned, the **subagents** that
session spawned natively (Claude's Task tool), and any **peer** harness it tried to
invoke. Nothing runs under a task that isn't a node here — including in bypass mode,
where peer attempts are recorded by the PATH shims rather than by permission policy.

Sources, all already flowing into the daemon:
- session lifecycle → the dispatcher/manager
- subagent lifecycle → Claude hooks (`PreToolUse` with `tool_name=Task`, `SubagentStop`)
- peer invocations → `.podium/peer.jsonl`, written by the shims (policy.py)
"""

import json

from podium import protocol
from podium.ids import new_id
from podium.sink import Sink
from podium.state import StateStore, now


class AgentTree:
    def __init__(self, state: StateStore, sink: Sink) -> None:
        self.state = state
        self.sink = sink

    # --- writers ------------------------------------------------------------

    def open_session(self, session_id: str, task_id: str | None, worker: str,
                     detail: str = "", data: dict | None = None) -> str:
        aid = new_id("a")
        self.state.execute(
            "INSERT INTO agents(id,task_id,session_id,parent_id,kind,worker,label,"
            "detail,status,started_at,data) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (aid, task_id, session_id, None, "session", worker, worker, detail,
             "running", now(), json.dumps(data or {})),
        )
        self._emit(task_id)
        return aid

    def open_subagent(self, session_id: str, label: str, detail: str = "",
                      data: dict | None = None, key: str | None = None) -> str | None:
        """A native subagent of a session (intra-vendor fan-out). Parented to the
        session's node so the tree shows who spawned whom."""
        parent = self._session_node(session_id)
        if parent is None:
            return None
        aid = new_id("a")
        self.state.execute(
            "INSERT INTO agents(id,task_id,session_id,parent_id,kind,worker,label,"
            "detail,status,started_at,data) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (aid, parent["task_id"], session_id, parent["id"], "subagent",
             parent["worker"], label, detail, "running", now(),
             json.dumps((data or {}) | {"key": key})),
        )
        self.sink.emit(protocol.narration(
            parent["task_id"], f"[{session_id}] subagent started: {label}"))
        self._emit(parent["task_id"])
        return aid

    def close_subagent(self, session_id: str, key: str | None,
                       status: str = "done") -> None:
        """Close by the worker's own tool_use_id when we have it — subagents run in
        PARALLEL, so closing "the latest running one" would pair them wrongly."""
        if key:
            rows = self.state.query(
                "SELECT id FROM agents WHERE session_id=? AND kind='subagent'"
                " AND status='running' AND json_extract(data,'$.key')=? LIMIT 1",
                (session_id, key))
            if rows:
                self.state.execute(
                    "UPDATE agents SET status=?, ended_at=? WHERE id=?",
                    (status, now(), rows[0]["id"]))
                self._emit(self._task_of(rows[0]["id"]))
                return
        self.close_latest(session_id, "subagent", status)

    def close_latest(self, session_id: str, kind: str = "subagent",
                     status: str = "done") -> None:
        """Fallback when no correlation id is available (e.g. a bare SubagentStop)."""
        rows = self.state.query(
            "SELECT id FROM agents WHERE session_id=? AND kind=? AND status='running'"
            " ORDER BY started_at DESC, rowid DESC LIMIT 1", (session_id, kind))
        if rows:
            self.state.execute(
                "UPDATE agents SET status=?, ended_at=? WHERE id=?",
                (status, now(), rows[0]["id"]))
            self._emit(self._task_of(rows[0]["id"]))

    def close_session(self, session_id: str, status: str = "done") -> None:
        self.state.execute(
            "UPDATE agents SET status=?, ended_at=? WHERE session_id=? AND"
            " status='running'", (status, now(), session_id))

    def record_peer(self, session_id: str, binary: str, command: str,
                    outcome: str = "blocked") -> str | None:
        """A worker invoked another harness. Recorded whatever the outcome — bypass
        mode cannot hide it, because the shim reports before refusing."""
        parent = self._session_node(session_id)
        aid = new_id("a")
        self.state.execute(
            "INSERT INTO agents(id,task_id,session_id,parent_id,kind,worker,label,"
            "detail,status,started_at,ended_at,data) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (aid, parent["task_id"] if parent else None, session_id,
             parent["id"] if parent else None, "peer", binary,
             f"peer: {binary}", command[:500], outcome, now(), now(),
             json.dumps({"command": command})),
        )
        task_id = parent["task_id"] if parent else None
        self.sink.emit(protocol.narration(
            task_id, f"[{session_id}] peer call {outcome}: {command[:80]}"))
        self._emit(task_id)
        return aid

    # --- readers ------------------------------------------------------------

    def tree(self, task_id: str) -> list[dict]:
        """Flat rows with depth, ordered parent-before-child — everything working on
        this task, at a glance."""
        rows = [dict(r) for r in self.state.query(
            "SELECT * FROM agents WHERE task_id=? ORDER BY started_at, rowid",
            (task_id,))]
        by_parent: dict[str | None, list[dict]] = {}
        for r in rows:
            by_parent.setdefault(r["parent_id"], []).append(r)
        out: list[dict] = []

        def walk(parent_id, depth):
            for node in by_parent.get(parent_id, []):
                out.append(node | {"depth": depth})
                walk(node["id"], depth + 1)

        walk(None, 0)
        seen = {n["id"] for n in out}
        out += [r | {"depth": 0} for r in rows if r["id"] not in seen]  # orphans
        return out

    def scope_tree(self, work) -> list[dict]:
        """The whole hierarchy in one list: workspace → project → task → session →
        subagent/peer. Depth is cumulative, so a client renders it by indenting."""
        board = work.board()
        out: list[dict] = []
        tasks_by_project: dict[str, list] = {}
        for t in board["tasks"]:
            tasks_by_project.setdefault(t["project_id"], []).append(t)
        projects_by_ws: dict[str, list] = {}
        for p in board["projects"]:
            projects_by_ws.setdefault(p["workspace_id"], []).append(p)
        for ws in board["workspaces"]:
            out.append({"kind": "workspace", "id": ws["id"], "label": ws["name"],
                        "status": "", "depth": 0})
            for proj in projects_by_ws.get(ws["id"], []):
                out.append({"kind": "project", "id": proj["id"],
                            "label": proj["name"], "status": "", "depth": 1})
                for task in tasks_by_project.get(proj["id"], []):
                    out.append({"kind": "task", "id": task["id"],
                                "label": task["title"], "status": task["status"],
                                "depth": 2})
                    for node in self.tree(task["id"]):
                        out.append(node | {"depth": node["depth"] + 3})
        return out

    def _session_node(self, session_id: str):
        rows = self.state.query(
            "SELECT * FROM agents WHERE session_id=? AND kind='session'"
            " ORDER BY started_at DESC LIMIT 1", (session_id,))
        return rows[0] if rows else None

    def _task_of(self, agent_id: str) -> str | None:
        rows = self.state.query("SELECT task_id FROM agents WHERE id=?", (agent_id,))
        return rows[0]["task_id"] if rows else None

    def _emit(self, task_id: str | None) -> None:
        if task_id:
            self.sink.emit({"type": "agents.updated", "task_id": task_id,
                            "agents": self.tree(task_id)})
