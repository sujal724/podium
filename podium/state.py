"""StateStore — SQLite (WAL) holding the full LLD §19 schema from day one.

The whole schema exists now even though only the Stage-A tables are written, so later
stages add behavior without migrations that change shipped semantics (decision 44).
Deviation recorded in specs/001 plan: the dependency DAG is normalized into `task_deps`
(+ `project_relations`) instead of a `tasks.deps` JSON column — readiness, unblock-count,
and cycle checks are relational queries (WORKFLOW §2.1).
"""

import json
import re
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
-- core runtime
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, spec JSON, mode TEXT, status TEXT,
    created_at INT, ended_at INT);
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, run_id TEXT, task_id TEXT,
    worker TEXT, kind TEXT, cwd TEXT, status TEXT, resume_key TEXT,
    created_at INT, ended_at INT);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, session_id TEXT, ts INT,
    type TEXT, data JSON);
CREATE TABLE IF NOT EXISTS handoffs(id TEXT PRIMARY KEY, run_id TEXT, from_stage TEXT,
    to_stage TEXT, md TEXT, json JSON, approved INT, edited_diff TEXT);
CREATE TABLE IF NOT EXISTS metrics(id INTEGER PRIMARY KEY, run_id TEXT, session_id TEXT,
    task_id TEXT, actor TEXT, kind TEXT, value REAL, ts INT);
-- intelligence (Stage C writers; tables exist now)
CREATE TABLE IF NOT EXISTS graph_nodes(id TEXT PRIMARY KEY, repo TEXT, kind TEXT,
    name TEXT, path TEXT, span JSON);
CREATE TABLE IF NOT EXISTS graph_edges(src TEXT, dst TEXT, kind TEXT);
CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY, repo TEXT, path TEXT, span JSON,
    text TEXT, node_id TEXT, provenance JSON);
CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY, kind TEXT, scope TEXT,
    text TEXT, refs JSON, ts INT);
CREATE TABLE IF NOT EXISTS guidelines(id TEXT PRIMARY KEY, scope TEXT, text TEXT,
    approved INT, source TEXT, ts INT);
CREATE TABLE IF NOT EXISTS verifications(id TEXT PRIMARY KEY, task_id TEXT,
    session_id TEXT, score REAL, ok INT, findings JSON, ts INT);
CREATE TABLE IF NOT EXISTS embed_cache(key TEXT PRIMARY KEY, vec BLOB, model TEXT, ts INT);
CREATE TABLE IF NOT EXISTS rerank_cache(key TEXT PRIMARY KEY, scores JSON, model TEXT, ts INT);
-- work management (LLD §9/§19; DAG normalized per specs/001)
CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY, name TEXT, created_at INT);
CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, workspace_id TEXT, name TEXT,
    autonomy TEXT, repo_root TEXT, base_branch TEXT DEFAULT 'main', created_at INT);
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, project_id TEXT, parent_id TEXT,
    title TEXT, description TEXT,
    status TEXT DEFAULT 'backlog',
    priority INT DEFAULT 2, labels JSON, assignee TEXT,
    origin TEXT DEFAULT 'human', detector TEXT, approved_by TEXT, approved_at INT,
    resources JSON, workflow TEXT, autonomy TEXT, worktree TEXT,
    base_ref TEXT,                     -- explicit branch to build on;
                                       -- else the parent task's branch, else project base
    created_at INT, updated_at INT);
CREATE TABLE IF NOT EXISTS task_deps(from_task TEXT, to_task TEXT,
    kind TEXT DEFAULT 'blocked-by', origin TEXT DEFAULT 'human',
    PRIMARY KEY(from_task, to_task, kind));
CREATE TABLE IF NOT EXISTS project_relations(from_project TEXT, to_project TEXT,
    kind TEXT, PRIMARY KEY(from_project, to_project, kind));
CREATE TABLE IF NOT EXISTS task_events(id INTEGER PRIMARY KEY, task_id TEXT, ts INT,
    type TEXT, actor TEXT, data JSON);
CREATE TABLE IF NOT EXISTS tms_links(native_id TEXT, adapter TEXT, external_id TEXT,
    source_of_truth TEXT, synced_at INT);
CREATE TABLE IF NOT EXISTS resources(id TEXT PRIMARY KEY, task_id TEXT, url TEXT,
    path TEXT, kind TEXT, status TEXT, ingested_at INT);
-- agent tree (spec 007): every actor Podium knows about, in one hierarchy —
-- worker sessions, their native subagents, and peer-harness invocations.
CREATE TABLE IF NOT EXISTS agents(id TEXT PRIMARY KEY, task_id TEXT, session_id TEXT,
    parent_id TEXT,            -- another agents.id (subagent of / spawned by)
    kind TEXT,                 -- "session" | "subagent" | "peer"
    worker TEXT, label TEXT, detail TEXT,
    status TEXT,               -- running | done | blocked | error
    started_at INT, ended_at INT, data JSON);
CREATE INDEX IF NOT EXISTS agents_task ON agents(task_id);
-- quota (LLD §8.3)
CREATE TABLE IF NOT EXISTS quota_ledger(id INTEGER PRIMARY KEY, worker TEXT, window TEXT,
    spent REAL, budget REAL, window_start INT, window_reset INT, ts INT);
-- teams / bookmarks / management / library / media: created at their stages' first
-- write path; their *surfaces* are registered blocked (surfaces.py) so nothing is faked.
"""


def cols_add(cols: dict[str, str], raw: str) -> None:
    """Parse one column definition; skip table constraints (PRIMARY KEY(...) etc.)."""
    text = " ".join(line.split("--")[0] for line in raw.splitlines()).strip()
    if not text:
        return
    parts = text.split()
    name = parts[0]
    if name.upper() in ("PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "CONSTRAINT"):
        return
    decl = " ".join(parts[1:]) or "TEXT"
    if "PRIMARY KEY" in decl.upper() or "UNIQUE" in decl.upper():
        decl = decl.split()[0]           # ALTER TABLE cannot add PK/UNIQUE columns
    cols[name] = decl


def now() -> int:
    return int(time.time())


class StateStore:
    """One writer connection, thread-guarded (the TUI/gateway are asyncio; workers'
    output taps arrive on the loop thread). WAL keeps readers unblocked."""

    def __init__(self, path: str) -> None:
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.executescript(SCHEMA)
            self._migrate()
            self._db.commit()

    def _migrate(self) -> None:
        """`CREATE TABLE IF NOT EXISTS` never alters an EXISTING table, so a column
        added in a later version would be missing from an older database (found in
        dogfooding: v0.4.0's tasks.base_ref broke a live board). Add any column the
        schema declares that the table lacks — additive only, never destructive."""
        for table, ddl in self._declared_columns().items():
            have = {r["name"] for r in
                    self._db.execute(f"PRAGMA table_info({table})").fetchall()}
            if not have:
                continue
            for name, decl in ddl.items():
                if name not in have:
                    self._db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    @staticmethod
    def _declared_columns() -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for match in re.finditer(
                r"CREATE TABLE IF NOT EXISTS (\w+)\((.*?)\);", SCHEMA, re.S):
            table, body = match.group(1), match.group(2)
            # strip comments BEFORE splitting: a comma inside a `-- comment` would
            # otherwise split one column definition into two
            body = "\n".join(line.split("--")[0] for line in body.splitlines())
            cols: dict[str, str] = {}
            depth = 0
            current = ""
            for ch in body:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                if ch == "," and depth == 0:
                    cols_add(cols, current); current = ""
                else:
                    current += ch
            cols_add(cols, current)
            out[table] = cols
        return out

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._db.execute(sql, params)
            self._db.commit()
            return cur

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    def transaction(self):
        """Context manager for multi-statement atomic sections (e.g. atomic claim)."""
        return _Txn(self)

    def log_event(self, session_id: str | None, type_: str, data: dict) -> None:
        self.execute(
            "INSERT INTO events(session_id, ts, type, data) VALUES(?,?,?,?)",
            (session_id, now(), type_, json.dumps(data)),
        )

    def close(self) -> None:
        with self._lock:
            self._db.close()


class _Txn:
    def __init__(self, store: StateStore) -> None:
        self.store = store

    def __enter__(self) -> sqlite3.Connection:
        self.store._lock.acquire()
        return self.store._db

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                self.store._db.commit()
            else:
                self.store._db.rollback()
        finally:
            self.store._lock.release()
