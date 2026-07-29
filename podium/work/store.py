"""WorkStore — the native Linear-like work model over the StateStore (LLD §9,
WORKFLOW §2–§3). Standalone with zero TMS.

Rules enforced here, not by convention:
- Nothing in `proposed` runs: `ready_tasks`/`claim` only see human-cleared work.
- Human dependency edges are authoritative; cycles are rejected at insert.
- `ready → assigned` is an atomic claim — two claimers never grab the same task.
- Every transition logs a `task_events` row and emits `task.updated`.
"""

import json
from dataclasses import dataclass, field

from podium import protocol
from podium.ids import new_id
from podium.sink import Sink
from podium.state import StateStore, now

STATUSES = (
    "proposed", "backlog", "ready", "assigned", "running", "verifying",
    "review", "done", "blocked", "parked", "discarded",
)
# Dependency edges of this kind gate readiness; `relates` edges never do.
GATING_KIND = "blocked-by"


@dataclass
class Task:
    id: str
    project_id: str | None
    parent_id: str | None
    title: str
    description: str
    status: str
    priority: int
    assignee: str | None
    origin: str
    detector: str | None
    labels: list[str] = field(default_factory=list)
    worktree: str | None = None

    @classmethod
    def from_row(cls, r) -> "Task":
        return cls(
            id=r["id"], project_id=r["project_id"], parent_id=r["parent_id"],
            title=r["title"], description=r["description"] or "", status=r["status"],
            priority=r["priority"], assignee=r["assignee"], origin=r["origin"],
            detector=r["detector"], labels=json.loads(r["labels"] or "[]"),
            worktree=r["worktree"],
        )

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class CycleError(ValueError):
    pass


class WorkStore:
    def __init__(self, state: StateStore, sink: Sink | None = None) -> None:
        self.state = state
        self.sink = sink or Sink()

    # --- creation -----------------------------------------------------------

    def create_workspace(self, name: str) -> str:
        wid = new_id("w")
        self.state.execute(
            "INSERT INTO workspaces(id,name,created_at) VALUES(?,?,?)", (wid, name, now())
        )
        return wid

    def create_project(
        self, workspace_id: str, name: str,
        repo_root: str | None = None, base_branch: str = "main",
    ) -> str:
        pid = new_id("p")
        self.state.execute(
            "INSERT INTO projects(id,workspace_id,name,repo_root,base_branch,created_at)"
            " VALUES(?,?,?,?,?,?)",
            (pid, workspace_id, name, repo_root, base_branch, now()),
        )
        return pid

    def create_task(
        self, project_id: str | None, title: str, description: str = "",
        parent_id: str | None = None, priority: int = 2, status: str = "backlog",
        assignee: str | None = None, labels: list[str] | None = None,
        actor: str = "human",
    ) -> str:
        if status not in ("backlog", "ready"):
            raise ValueError("authored tasks start in backlog or ready")
        tid = new_id("t")
        ts = now()
        self.state.execute(
            "INSERT INTO tasks(id,project_id,parent_id,title,description,status,priority,"
            "labels,assignee,origin,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, project_id, parent_id, title, description, status, priority,
             json.dumps(labels or []), assignee, "human", ts, ts),
        )
        self._log(tid, "created", actor, {"status": status})
        self._emit(tid)
        return tid

    def propose_task(
        self, title: str, description: str, detector: str,
        project_id: str | None = None, priority: int | None = None,
        refs: list[str] | None = None,
    ) -> str:
        """Detector-proposed work → the approval inbox. Never straight to ready
        (decision 35)."""
        tid = new_id("t")
        ts = now()
        self.state.execute(
            "INSERT INTO tasks(id,project_id,title,description,status,priority,labels,"
            "origin,detector,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (tid, project_id, title, description, "proposed", priority or 2,
             json.dumps([]), "auto", detector, ts, ts),
        )
        self._log(tid, "proposed", f"detector:{detector}", {"refs": refs or []})
        self.sink.emit(protocol.task_proposed(self.get_task(tid).to_dict()))
        return tid

    # --- inbox (decision 35) ------------------------------------------------

    def proposed_tasks(self) -> list[Task]:
        return self._tasks_where("status='proposed'")

    def approve_task(self, task_id: str, actor: str) -> None:
        t = self.get_task(task_id)
        if t.status != "proposed":
            raise ValueError(f"{task_id} is not in the inbox (status={t.status})")
        self.state.execute(
            "UPDATE tasks SET status='ready', approved_by=?, approved_at=?, updated_at=?"
            " WHERE id=?",
            (actor, now(), now(), task_id),
        )
        self._log(task_id, "approved", actor, {})
        self._emit(task_id)

    def reject_task(self, task_id: str, actor: str, reason: str = "") -> None:
        t = self.get_task(task_id)
        if t.status != "proposed":
            raise ValueError(f"{task_id} is not in the inbox (status={t.status})")
        self.state.execute(
            "UPDATE tasks SET status='discarded', updated_at=? WHERE id=?",
            (now(), task_id),
        )
        self._log(task_id, "rejected", actor, {"reason": reason})
        self._emit(task_id)

    # --- updates ------------------------------------------------------------

    def update_task(self, task_id: str, actor: str = "human", **fields) -> None:
        allowed = {"title", "description", "status", "priority", "assignee", "labels",
                   "worktree", "parent_id", "project_id"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"cannot update fields: {sorted(bad)}")
        if "status" in fields and fields["status"] not in STATUSES:
            raise ValueError(f"unknown status {fields['status']!r}")
        if "labels" in fields:
            fields["labels"] = json.dumps(fields["labels"])
        sets = ", ".join(f"{k}=:{k}" for k in fields)
        fields |= {"id": task_id, "updated_at": now()}
        self.state.execute(f"UPDATE tasks SET {sets}, updated_at=:updated_at WHERE id=:id",
                           fields)
        self._log(task_id, "updated", actor, {k: v for k, v in fields.items()
                                              if k not in ("id", "updated_at")})
        self._emit(task_id)

    def set_status(self, task_id: str, status: str, actor: str = "system",
                   data: dict | None = None) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        self.state.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                           (status, now(), task_id))
        self._log(task_id, f"status:{status}", actor, data or {})
        self._emit(task_id)

    # --- dependency DAG (decision 43) --------------------------------------

    def add_dependency(self, task_id: str, depends_on: str,
                       kind: str = GATING_KIND, origin: str = "human") -> None:
        """`task_id` is blocked by `depends_on`. Cross-project edges allowed;
        cycles rejected (the DAG stays a DAG — contradictions become questions,
        not silent state)."""
        if task_id == depends_on:
            raise CycleError("a task cannot depend on itself")
        if kind == GATING_KIND and self._reaches(depends_on, task_id):
            raise CycleError(f"{task_id} -> {depends_on} would create a cycle")
        self.state.execute(
            "INSERT OR IGNORE INTO task_deps(from_task,to_task,kind,origin)"
            " VALUES(?,?,?,?)",
            (task_id, depends_on, kind, origin),
        )
        self._log(task_id, "dep.added", origin, {"on": depends_on, "kind": kind})

    def dependencies(self, task_id: str) -> list[str]:
        return [r["to_task"] for r in self.state.query(
            "SELECT to_task FROM task_deps WHERE from_task=? AND kind=?",
            (task_id, GATING_KIND))]

    def _reaches(self, src: str, dst: str) -> bool:
        seen, frontier = set(), [src]
        while frontier:
            cur = frontier.pop()
            if cur == dst:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            frontier += self.dependencies(cur)
        return False

    # --- readiness + claim (LLD §8.2, §9) -----------------------------------

    # Claimable = triaged (`ready`) AND every gating dep done. `backlog` is authored but
    # not yet schedulable (WORKFLOW §3) — triage moves it to `ready`, the dep gate is here.
    _READY_SQL = """
        SELECT t.* FROM tasks t
        WHERE t.status = 'ready'
          AND NOT EXISTS (
            SELECT 1 FROM task_deps d JOIN tasks dep ON dep.id = d.to_task
            WHERE d.from_task = t.id AND d.kind = 'blocked-by'
              AND dep.status NOT IN ('done','discarded'))
        ORDER BY t.priority ASC,
                 (SELECT COUNT(*) FROM task_deps u
                  WHERE u.to_task = t.id AND u.kind = 'blocked-by') DESC,
                 t.created_at ASC
    """
    # Pull order: priority first (P0 wins), then unblock-count (how many tasks this one
    # unblocks) — the V1 heuristic; critical-path proper is a registered later increment.

    def ready_tasks(self) -> list[Task]:
        return [Task.from_row(r) for r in self.state.query(self._READY_SQL)]

    def claim(self, worker: str, task_id: str | None = None) -> Task | None:
        """Atomically move the best ready task (or the given one, if ready) to
        `assigned` for `worker`. Single-owner guarantee under concurrency."""
        with self.state.transaction() as db:
            if task_id:
                row = db.execute(
                    f"SELECT * FROM ({self._READY_SQL}) WHERE id=?", (task_id,)
                ).fetchone()
            else:
                row = db.execute(f"{self._READY_SQL} LIMIT 1").fetchone()
            if row is None:
                return None
            cur = db.execute(
                "UPDATE tasks SET status='assigned', assignee=?, updated_at=?"
                " WHERE id=? AND status='ready' RETURNING *",
                (worker, now(), row["id"]),
            )
            claimed = cur.fetchone()
        if claimed is None:
            return None
        self._log(claimed["id"], "claimed", worker, {})
        self.sink.emit(protocol.task_claimed(claimed["id"], worker))
        self._emit(claimed["id"])
        return Task.from_row(claimed)

    # --- reads --------------------------------------------------------------

    def get_task(self, task_id: str) -> Task:
        rows = self.state.query("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not rows:
            raise KeyError(task_id)
        return Task.from_row(rows[0])

    def _tasks_where(self, where: str, params: tuple = ()) -> list[Task]:
        return [Task.from_row(r) for r in
                self.state.query(f"SELECT * FROM tasks WHERE {where}"
                                 " ORDER BY priority, created_at", params)]

    def board(self) -> dict:
        """Full hierarchy snapshot for the board pane / `work.list`."""
        return {
            "workspaces": [dict(r) for r in self.state.query(
                "SELECT * FROM workspaces ORDER BY created_at")],
            "projects": [dict(r) for r in self.state.query(
                "SELECT * FROM projects ORDER BY created_at")],
            "tasks": [t.to_dict() for t in self._tasks_where(
                "status != 'discarded'")],
            "deps": [dict(r) for r in self.state.query("SELECT * FROM task_deps")],
        }

    def feedback(self, task_id: str) -> list[dict]:
        return [json.loads(r["data"]) for r in self.state.query(
            "SELECT data FROM task_events WHERE task_id=? AND type='review.rejected'"
            " ORDER BY ts", (task_id,))]

    # --- internals ----------------------------------------------------------

    def _log(self, task_id: str, type_: str, actor: str, data: dict) -> None:
        self.state.execute(
            "INSERT INTO task_events(task_id,ts,type,actor,data) VALUES(?,?,?,?,?)",
            (task_id, now(), type_, actor, json.dumps(data)),
        )

    def _emit(self, task_id: str) -> None:
        self.sink.emit(protocol.task_updated(self.get_task(task_id).to_dict()))
