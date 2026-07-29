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
from podium.interaction import InteractionLayer
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
        self.manager = SessionManager(self.sink, self.state)
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

    # --- one client connection ---------------------------------------------

    async def handle_client(self, ws) -> None:
        q = self.sink.subscribe()
        try:
            await ws.send(protocol.dumps(protocol.hello(
                podium.__version__, self.manager.availability())))
            snap = self.manager.snapshot()
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
        # Known prompts translate to the exact keystrokes the worker's dialog expects;
        # anything else is a generic line answer. Native callbacks are Stage B.
        data = self.interaction.answer_bytes(f.get("question_id", ""), f["value"])
        await self.manager.write(f["session_id"], data)

    async def on_stop(self, f: dict) -> None:
        await self.manager.stop(f["session_id"])

    async def on_resize(self, f: dict) -> None:
        self.last_winsize = (int(f["rows"]), int(f["cols"]))
        self.manager.resize(f["session_id"], int(f["rows"]), int(f["cols"]))

    async def on_session_mode(self, f: dict) -> dict:
        """Change a RUNNING session's permission mode (spec 006): drive the CLI's
        own shift+tab cycle until its status line reports the target mode. Modes are
        not fixed at spawn — a supervised session can go autonomous mid-flight and
        back, like taking the wheel."""
        from podium.interaction import CYCLE_KEY, detect_mode
        sid, target = f["session_id"], f["mode"]
        sess = self.manager.sessions.get(sid)
        if sess is None:
            return protocol.error(f"no live session {sid}")
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

    async def on_review_approve(self, f: dict) -> None:
        await self.dispatcher.approve(f["task_id"], f.get("actor", "human"))

    async def on_review_reject(self, f: dict) -> None:
        await self.dispatcher.reject(f["task_id"], f.get("feedback", ""),
                                     f.get("actor", "human"))

    async def on_review_diff(self, f: dict) -> dict:
        task = self.work.get_task(f["task_id"])
        proj = self.dispatcher._project(task)
        diff = self.workspaces.diff(proj["repo_root"], task.id, proj["base_branch"])
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
        self.boot()
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
    server = await daemon.serve()
    log.info("podiumd listening on %s", CONFIG.ws_url)
    print(f"podiumd {podium.__version__} listening on {CONFIG.ws_url}")
    await asyncio.get_running_loop().create_future()  # run forever
    del server


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
