"""podiumd — the daemon: WebSocket gateway on 127.0.0.1 (localhost-only, single
operator, no auth — remote access only ever arrives via the authenticated coordinator,
a Stage B/C surface).

Frame routing: Stage-A frames are served; every later-stage frame answers `blocked`
with its surface + stage from the decision-44 registry. Every non-output frame is
persisted to `events`; session output goes to per-session transcript files.
"""

import asyncio
import contextlib
import logging
import os
import sys
from pathlib import Path

import websockets

import podium
from podium import protocol
from podium.config import CONFIG
from podium.dispatcher import Dispatcher
from podium.interaction import Approvals, InteractionLayer
from podium.manager import SessionManager
from podium.metering import Meter
from podium.sink import Sink
from podium.state import StateStore
from podium.surfaces import SURFACES, frame_surface
from podium.update import SelfUpdater, UpdateError
from podium.work.store import WorkStore

log = logging.getLogger("podiumd")


class Daemon:
    def __init__(self, state_db: str | None = None, workdir: str | None = None) -> None:
        self.sink = Sink()
        self.state = StateStore(state_db or CONFIG.state_db)
        self.work = WorkStore(self.state, self.sink)
        self.approvals = Approvals(self.sink)
        self.manager = SessionManager(self.sink, self.state, self.approvals)
        self.meter = Meter(self.state, self.sink)
        from podium.workspace import WorkspaceManager
        self.workspaces = WorkspaceManager(workdir or CONFIG.workdir)
        self.dispatcher = Dispatcher(self.work, self.manager, self.workspaces,
                                     self.meter, self.sink, self.state,
                                     CONFIG.default_worker)
        self.interaction = InteractionLayer(self.sink)
        self.updater = SelfUpdater(self.sink, restart_fn=self._restart_for_update)
        self._server = None
        self.last_winsize: tuple[int, int] | None = None
        self.dispatcher.winsize = lambda: self.last_winsize
        self.transcripts = Path(workdir or CONFIG.workdir).expanduser() / "transcripts"
        self.transcripts.mkdir(parents=True, exist_ok=True)
        self.sink.tap(self._persist)
        self.sink.tap(self._scan_prompts)

    def _persist(self, frame: dict) -> None:
        if frame.get("type") == "output":
            path = self.transcripts / f"{frame['session_id']}.log"
            with path.open("a") as f:
                f.write(frame["text"])
        else:
            self.state.log_event(frame.get("session_id"), frame["type"],
                                 {k: v for k, v in frame.items()
                                  if k not in ("type", "session_id")})

    def _scan_prompts(self, frame: dict) -> None:
        """Worker CLIs' own dialogs (trust, login) become uniform questions the
        operator answers from the cockpit — Podium relays, never decides (spec 002)."""
        if frame.get("type") != "output":
            return
        sess = self.manager.sessions.get(frame["session_id"])
        if sess is not None:
            self.interaction.scan(sess.id, sess.label, frame["text"])

    def boot(self) -> None:
        if CONFIG.autoresume:
            self.dispatcher.resume_interrupted()

    async def boot_async(self) -> None:
        """Adopt surviving tmux workers BEFORE re-queuing, so a restart never starts
        a second worker on a task whose first one is still running (spec 011)."""
        if CONFIG.autoresume:
            try:
                await self.dispatcher.adopt_live_sessions()
            except Exception as e:
                log.warning("adopting live sessions failed: %s", e)

    # --- one client connection ---------------------------------------------

    async def handle_client(self, ws) -> None:
        q = self.sink.subscribe()
        try:
            await ws.send(protocol.dumps(protocol.hello(
                podium.__version__, self.manager.availability())))
            snap = self.manager.snapshot()
            # A long-running worker's backlog once exceeded the frame limit and
            # disconnected EVERY client on connect. Send a bounded tail; the full
            # transcript lives on disk and in the session's own terminal.
            snap["backlogs"] = {sid: text[-64_000:]
                                for sid, text in snap["backlogs"].items()}
            await ws.send(protocol.dumps(protocol.snapshot(
                snap["sessions"], snap["backlogs"])))
            forward = asyncio.create_task(self._forward(ws, q))
            try:
                async for raw in ws:
                    try:
                        frame = protocol.loads(raw)
                        reply = await self.dispatch_frame(frame)
                    except Exception as e:
                        reply = protocol.error(str(e))
                    if reply is not None:
                        await ws.send(protocol.dumps(reply))
            finally:
                forward.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await forward
        except websockets.ConnectionClosed:
            pass
        finally:
            self.sink.unsubscribe(q)

    async def _forward(self, ws, q: asyncio.Queue) -> None:
        while True:
            frame = await q.get()
            await ws.send(protocol.dumps(frame))

    # --- frame routing ------------------------------------------------------

    async def dispatch_frame(self, frame: dict) -> dict | None:
        t = frame["type"]
        surface = frame_surface(t)
        if surface is not None:
            return protocol.blocked(surface.key, surface.stage, surface.note)
        handler = getattr(self, "on_" + t.replace(".", "_"), None)
        if handler is None:
            return protocol.error(f"unknown frame type {t!r}")
        return await handler(frame)

    # sessions
    async def on_spawn(self, f: dict) -> dict:
        sess = await self.manager.spawn(f["worker"], f.get("prompt", ""),
                                        cwd=f.get("cwd"), kind=f.get("kind"))
        return {"type": "spawned", "session": sess.info()}

    async def on_agent_send(self, f: dict) -> None:
        await self.manager.write(f["session_id"], f["text"])

    async def on_answer(self, f: dict) -> None:
        # One uniform answer path (LLD §18): a pending native approval resolves
        # through the Approvals registry first; a peer-call approval is answered to
        # the daemon; anything else translates to the keystrokes the worker's own
        # dialog expects (or a generic line answer) and lands in the PTY.
        rid = f.get("request_id") or f.get("question_id")
        if rid and rid in self.approvals:
            self.approvals.answer(rid, f["value"], actor=f.get("actor", "human"),
                                  session_id=f.get("session_id"))
            return
        qid = str(f.get("question_id", ""))
        if qid.startswith("peer:"):
            allow = str(f["value"]).strip() in ("1", "y", "yes", "approve",
                                                "Approve — run it")
            self.dispatcher.answer_peer(qid[len("peer:"):], allow,
                                        f.get("actor", "human"))
            return
        data = self.interaction.answer_bytes(qid, f["value"])
        await self.manager.write(f["session_id"], data)

    # interaction layer — native approvals (Stage B, spec 012)
    async def on_answer_native(self, f: dict) -> dict:
        self.approvals.answer(f["request_id"], f["value"],
                              actor=f.get("actor", "human"),
                              session_id=f.get("session_id"))
        return {"type": "answer.ack", "request_id": f["request_id"]}

    async def on_interaction_pending(self, f: dict) -> dict:
        return {"type": "approval.pending",
                "requests": self.approvals.pending(f.get("session_id"))}

    async def on_stop(self, f: dict) -> None:
        await self.manager.stop(f["session_id"])

    async def on_resize(self, f: dict) -> None:
        self.last_winsize = (int(f["rows"]), int(f["cols"]))
        self.manager.resize(f["session_id"], int(f["rows"]), int(f["cols"]))

    async def on_session_mode(self, f: dict) -> dict:
        """Change a RUNNING session's permission mode. Native-channel kinds (sdk/acp)
        switch through their own callback (SDK `set_permission_mode`, ACP
        `session/set_mode`); terminal kinds drive the CLI's own shift+tab cycle until
        its status line reports the target mode (spec 006). Modes are not fixed at
        spawn — a supervised session can go autonomous mid-flight and back."""
        from podium.interaction import CYCLE_KEY, detect_mode
        sid, target = f["session_id"], f["mode"]
        sess = self.manager.sessions.get(sid)
        if sess is None:
            return protocol.error(f"no live session {sid}")
        try:
            await sess.set_mode(target)
            return {"type": "mode.set", "session_id": sid, "mode": target}
        except NotImplementedError:
            pass          # no native mode channel — fall through to the PTY cycle
        if target not in ("supervised", "autonomous", "bypass", "plan"):
            return protocol.error(f"unknown mode {target!r}")
        for _ in range(6):                       # the CLI cycles a short ring
            current = detect_mode(sess.backlog()[-4000:])
            if current == target or (current is None and target == "supervised"):
                break
            await sess.write(CYCLE_KEY)
            await asyncio.sleep(0.4)
        sess.autonomy = target
        self.state.execute("UPDATE sessions SET kind=kind WHERE id=?", (sid,))
        self.sink.emit(protocol.session_status(sid, sess.status))
        self.sink.emit(protocol.narration(
            sess.task_id, f"[{sid}] mode → {target} (live change)"))
        return {"type": "session.mode", "session_id": sid, "mode": target,
                "detected": detect_mode(sess.backlog()[-4000:])}

    async def on_winsize(self, f: dict) -> None:
        """Cockpit pane size, remembered so future PTYs spawn at it (spec 003 rev 2)."""
        self.last_winsize = (int(f["rows"]), int(f["cols"]))

    async def on_list(self, f: dict) -> dict:
        return {"type": "sessions", "sessions": self.manager.list()}

    async def on_sessions_list(self, f: dict) -> dict:
        rows = self.state.query(
            "SELECT s.id, s.task_id, s.worker, s.kind, s.status, s.resume_key,"
            " s.cwd, s.created_at, t.title FROM sessions s"
            " LEFT JOIN tasks t ON t.id = s.task_id"
            " ORDER BY s.created_at DESC LIMIT ?", (int(f.get("limit", 20)),))
        return {"type": "sessions.snapshot", "sessions": [dict(r) for r in rows]}

    async def on_brain_send(self, f: dict) -> dict:
        # Brain v0: acknowledge; the conversational brain rides later surfaces.
        return protocol.narration(None, "brain: noted (conversational brain is a "
                                        "later increment; use work.* frames)")

    # work management
    async def on_work_create(self, f: dict) -> dict:
        entity, fields = f["entity"], dict(f.get("fields", {}))
        if entity == "workspace":
            wid = self.work.create_workspace(fields["name"])
        elif entity == "project":
            wid = self.work.create_project(
                fields["workspace_id"], fields["name"],
                repo_root=fields.get("repo_root"),
                base_branch=fields.get("base_branch", "main"))
        elif entity == "task":
            deps = fields.pop("deps", [])
            wid = self.work.create_task(
                fields.get("project_id"), fields["title"],
                description=fields.get("description", ""),
                parent_id=fields.get("parent_id"),
                priority=int(fields.get("priority", 2)),
                status=fields.get("status", "backlog"),
                assignee=fields.get("assignee"),
                labels=fields.get("labels"))
            for dep in deps:
                self.work.add_dependency(wid, dep)
        else:
            return protocol.error(f"unknown entity {entity!r}")
        return {"type": "work.created", "entity": entity, "id": wid}

    async def on_work_update(self, f: dict) -> None:
        fields = dict(f.get("fields", {}))
        deps = fields.pop("deps", [])
        if fields:
            self.work.update_task(f["id"], **fields)
        for dep in deps:
            self.work.add_dependency(f["id"], dep)

    async def on_work_list(self, f: dict) -> dict:
        return protocol.work_snapshot(self.work.board())

    async def on_task_assign(self, f: dict) -> None:
        self.work.update_task(f["task_id"], assignee=f["assignee"])

    async def on_task_claim(self, f: dict) -> dict:
        task = self.work.claim(f.get("worker", CONFIG.default_worker),
                               f.get("task_id"))
        return {"type": "claimed",
                "task": task.to_dict() if task else None}

    async def on_task_inbox(self, f: dict) -> dict:
        return protocol.task_inbox([t.to_dict() for t in self.work.proposed_tasks()])

    async def on_task_approve(self, f: dict) -> None:
        self.work.approve_task(f["task_id"], f.get("actor", "human"))

    async def on_task_reject(self, f: dict) -> None:
        self.work.reject_task(f["task_id"], f.get("actor", "human"),
                              f.get("reason", ""))

    async def on_task_edit(self, f: dict) -> None:
        self.work.update_task(f["task_id"], **f.get("fields", {}))

    # the loop
    async def on_task_run(self, f: dict) -> dict:
        await self.dispatcher.run_task(f["task_id"], f.get("worker"))
        return {"type": "task.running", "task_id": f["task_id"]}

    async def on_run_next(self, f: dict) -> dict:
        tid = await self.dispatcher.run_next(f.get("worker"))
        return {"type": "task.running" if tid else "idle", "task_id": tid}

    async def on_review_approve(self, f: dict):
        """`mode="pr"` opens a pull request instead of merging locally — the right
        route when the base branch is protected (or you simply want it reviewed)."""
        if f.get("mode") == "pr":
            return await self.dispatcher.approve_via_pr(
                f["task_id"], f.get("actor", "human"), f.get("title"))
        await self.dispatcher.approve(f["task_id"], f.get("actor", "human"))

    async def on_review_reject(self, f: dict) -> None:
        await self.dispatcher.reject(f["task_id"], f.get("feedback", ""),
                                     f.get("actor", "human"))

    async def on_review_diff(self, f: dict) -> dict:
        task = self.work.get_task(f["task_id"])
        proj = self.dispatcher._project(task)
        diff = self.workspaces.diff(proj["repo_root"], task.id,
                                    self.dispatcher.base_ref_for(task, proj))
        return protocol.review_ready(task.id, diff, self.workspaces.branch(task.id))

    # self-update (spec 002): watch → tell → operator-approved apply
    async def on_update_status(self, f: dict) -> dict:
        return self.updater.status()

    async def on_update_check(self, f: dict) -> dict:
        return await self.updater.check()

    async def on_update_apply(self, f: dict) -> dict:
        # Validate inline, but run the apply DETACHED from this connection's handler:
        # the restart path waits for client handlers to finish, and the handler that
        # requested the update can never finish while it's awaiting the apply — the
        # v0.2.0 self-deadlock found by the first real self-update (spec 002 fix).
        st = self.updater.status()
        if not st.get("available"):
            return protocol.error("update not applied", st.get("reason", ""))
        if st.get("behind", 0) == 0:
            return protocol.error("update not applied", "already up to date")

        async def run_apply():
            try:
                await self.updater.apply(f.get("actor", "human"))
            except Exception as e:
                self.sink.emit(protocol.error("update failed", str(e)))

        self._apply_task = asyncio.create_task(run_apply())
        return protocol.narration(
            None, f"applying update to v{st.get('remote_version')} — the daemon "
                  "restarts itself; clients reconnect")

    async def _restart_for_update(self) -> None:
        """Cancel runners (tasks stay `running` → auto-resume re-queues on boot),
        stop sessions, free the port, re-exec the new code in place. Every step is
        bounded — a stuck session or lingering client must never wedge the restart."""
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.dispatcher.interrupt_all("update"), timeout=10)
        for sid in list(self.manager.sessions):
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.manager.stop(sid), timeout=10)
            self.manager.mark_ended(sid)
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._server.wait_closed(), timeout=5)
        with contextlib.suppress(Exception):
            self.state.close()
        # Re-exec the SAME entry point so the process keeps its name: after an
        # update it used to come back as `python -m podium.gateway`, which broke
        # every `pkill -f podiumd` and made "did it restart?" unanswerable.
        script = Path(sys.argv[0]).name
        if script.startswith("podiumd"):
            log.info("re-exec %s (update applied)", sys.argv[0])
            os.execv(sys.argv[0], sys.argv)
        log.info("re-exec python -m podium.gateway (update applied)")
        os.execv(sys.executable, [sys.executable, "-m", "podium.gateway"])

    async def _update_watch(self) -> None:
        while True:
            await asyncio.sleep(CONFIG.update_check_s)
            try:
                await self.updater.check()
            except Exception as e:
                log.warning("update check failed: %s", e)

    async def on_agents_tree(self, f: dict) -> dict:
        """Everything Podium knows is working on a task: its session(s), their
        native subagents, and any peer-harness attempt (spec 007)."""
        return {"type": "agents.tree", "task_id": f["task_id"],
                "agents": self.dispatcher.agents.tree(f["task_id"])}

    async def on_session_target(self, f: dict) -> dict:
        """Where a session's REAL terminal is, so a client can point a pane at it
        instead of stealing its own (spec 014)."""
        sid = f.get("session_id")
        if not sid and f.get("task_id"):
            rows = self.state.query(
                "SELECT id FROM sessions WHERE task_id=? ORDER BY created_at DESC"
                " LIMIT 1", (f["task_id"],))
            sid = rows[0]["id"] if rows else None
        if not sid:
            return protocol.error("no session for that task")
        rows = self.state.query("SELECT * FROM sessions WHERE id=?", (sid,))
        if not rows:
            return protocol.error(f"no session {sid}")
        row = dict(rows[0])
        from podium.sessions.tmux import live_sessions
        name = f"podium-{sid}"
        return {"type": "session.target", "session_id": sid,
                "tmux": name, "live": name in live_sessions(),
                "cwd": row["cwd"], "resume_key": row["resume_key"],
                "attach": ["tmux", "attach", "-t", name],
                "resume": (["claude", "--resume", row["resume_key"]]
                           if row["resume_key"] else ["claude", "--continue"])}

    # ---- #5 review queue ------------------------------------------------
    async def on_review_queue(self, f: dict) -> dict:
        """Everything awaiting you — not one variable (spec 015)."""
        rows = self.state.query(
            "SELECT id,title,priority,updated_at FROM tasks WHERE status='review'"
            " ORDER BY priority, updated_at")
        out = []
        for r in rows:
            task = self.work.get_task(r["id"])
            proj = self.dispatcher._project(task)
            base = self.dispatcher.base_ref_for(task, proj)
            out.append({**dict(r), "merge_target": base,
                        "branch": self.workspaces.branch(r["id"])})
        return {"type": "review.snapshot", "tasks": out}

    # ---- #6 housekeeping ------------------------------------------------
    async def on_sessions_gc(self, f: dict) -> dict:
        """Collapse dead sessions: keep the newest `keep` per task, drop the rest
        from the listing (rows stay for history)."""
        keep = int(f.get("keep", 3))
        removed = self.state.execute(
            "UPDATE sessions SET status='archived' WHERE id IN ("
            "  SELECT id FROM (SELECT id, ROW_NUMBER() OVER ("
            "    PARTITION BY task_id ORDER BY created_at DESC) rn FROM sessions"
            "    WHERE status IN ('exited','error','interrupted')) WHERE rn > ?)",
            (keep,)).rowcount
        return {"type": "sessions.gc", "archived": removed, "kept_per_task": keep}

    async def on_approvals_pending(self, f: dict) -> dict:
        """Questions still waiting on you — a missed dialog is not lost (spec 016)."""
        pend = [{"id": q.id, "session_id": q.session_id, "kind": q.pattern.id,
                 "q": q.pattern.question, "choices": list(q.choices)}
                for q in self.interaction.pending.values()]
        pend += [{"id": f"peer:{rid}", "session_id": sid, "kind": "peer",
                  "q": f"run {ev.get('command', '')}?",
                  "choices": ["Approve — run it", "Deny"]}
                 for rid, (wt, ev, sid) in self.dispatcher.pending_peers.items()]
        return {"type": "approvals.pending", "questions": pend}

    async def on_task_detail(self, f: dict) -> dict:
        """Everything about one task in one answer (spec 012)."""
        from podium import detail
        return detail.build(f["task_id"], self.work, self.dispatcher, self.state)

    async def on_task_requeue(self, f: dict) -> dict:
        """Put a blocked task back in the queue (its retry resumes if it was
        interrupted, or starts fresh after a rejection)."""
        self.work.set_status(f["task_id"], "ready", actor=f.get("actor", "human"),
                             data={"reason": "re-queued by operator"})
        return protocol.narration(f["task_id"], "re-queued")

    async def on_agents_scope(self, f: dict) -> dict:
        """The whole hierarchy: workspace → project → task → session → subagent/peer."""
        return {"type": "agents.scope",
                "nodes": self.dispatcher.agents.scope_tree(self.work)}

    # quota + surfaces
    async def on_quota_query(self, f: dict) -> dict:
        return {"type": "quota.snapshot",
                "workers": {name: self.meter.gauge.state(name)
                            for name in ("claude", "gemini", "codex")}}

    async def on_surfaces(self, f: dict) -> dict:
        return {"type": "surfaces",
                "surfaces": [s.__dict__ | {"frames": list(s.frames)}
                             for s in SURFACES.values()]}

    # --- serve ---------------------------------------------------------------

    async def serve(self, host: str | None = None, port: int | None = None):
        await self.boot_async()
        self.boot()
        host_ = host if host is not None else CONFIG.host
        port_ = port if port is not None else CONFIG.port
        if port_:
            import socket
            probe = socket.socket()
            # Probe exactly as asyncio will bind, or the check lies: without
            # SO_REUSEADDR a lingering socket from a just-killed daemon fails the
            # probe while the real server would bind fine — reported as a phantom
            # "already in use" that no pkill could clear.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((host_, port_))
            except OSError:
                holder = ""
                with contextlib.suppress(Exception):
                    out = __import__("subprocess").run(
                        ["ss", "-tlnp"], capture_output=True, text=True).stdout
                    holder = next((l.split("pid=")[1].split(",")[0]
                                   for l in out.splitlines()
                                   if f":{port_} " in l and "pid=" in l), "")
                raise SystemExit(
                    f"podiumd: {host_}:{port_} is already in use"
                    + (f" by PID {holder}" if holder else "")
                    + ".\nAnother daemon is running — stop it first "
                      "(`pkill -f podiumd`), or set PODIUM_PORT.")
            finally:
                probe.close()
        self._server = await websockets.serve(
            self.handle_client,
            host if host is not None else CONFIG.host,
            port if port is not None else CONFIG.port,
            max_size=16 * 1024 * 1024)
        if CONFIG.update_check_s > 0 and self.updater.repo is not None:
            self._watch_task = asyncio.create_task(self._update_watch())
        return self._server


async def _amain() -> None:
    daemon = Daemon()
    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()

    def _shutdown(sig: str) -> None:
        """A clean shutdown (laptop power-off, systemd stop) must be
        DISTINGUISHABLE from a crash: mark in-flight tasks interrupted so the next
        boot resumes them, and leave the tmux workers alone — they survive and are
        adopted on the next start (spec 011)."""
        log.info("shutdown signal %s — recording interrupts", sig)
        for task_id in list(daemon.dispatcher.running):
            with contextlib.suppress(Exception):
                daemon.work._log(task_id, "interrupted", "system",
                                 {"reason": f"daemon shutdown ({sig})"})
        with contextlib.suppress(Exception):
            daemon.state.execute(
                "INSERT INTO events(session_id, ts, type, data)"
                " VALUES(NULL, strftime('%s','now'), 'daemon.shutdown', ?)",
                (f'{{"signal": "{sig}"}}',))
        stopping.set()

    for sig in ("SIGTERM", "SIGINT", "SIGHUP"):
        with contextlib.suppress(NotImplementedError, AttributeError):
            loop.add_signal_handler(getattr(__import__("signal"), sig),
                                    _shutdown, sig)
    server = await daemon.serve()
    log.info("podiumd listening on %s", CONFIG.ws_url)
    print(f"podiumd {podium.__version__} listening on {CONFIG.ws_url}")
    await stopping.wait()
    server.close()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(server.wait_closed(), timeout=5)
    daemon.state.close()
    print("podiumd stopped — tmux workers keep running and are adopted on restart")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
