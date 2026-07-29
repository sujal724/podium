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
from podium.work.store import WorkStore

log = logging.getLogger("podiumd")


class Daemon:
    def __init__(self, state_db: str | None = None, workdir: str | None = None) -> None:
        self.sink = Sink()
        self.state = StateStore(state_db or CONFIG.state_db)
        self.work = WorkStore(self.state, self.sink)
        self.interaction = InteractionLayer(self.sink)
        self.manager = SessionManager(self.sink, self.state, self.interaction)
        self.meter = Meter(self.state, self.sink)
        from podium.workspace import WorkspaceManager
        self.workspaces = WorkspaceManager(workdir or CONFIG.workdir)
        self.dispatcher = Dispatcher(self.work, self.manager, self.workspaces,
                                     self.meter, self.sink, self.state,
                                     CONFIG.default_worker)
        self.transcripts = Path(workdir or CONFIG.workdir).expanduser() / "transcripts"
        self.transcripts.mkdir(parents=True, exist_ok=True)
        self.sink.tap(self._persist)

    def _persist(self, frame: dict) -> None:
        if frame.get("type") == "output":
            path = self.transcripts / f"{frame['session_id']}.log"
            with path.open("a") as f:
                f.write(frame["text"])
        else:
            self.state.log_event(frame.get("session_id"), frame["type"],
                                 {k: v for k, v in frame.items()
                                  if k not in ("type", "session_id")})

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
        # One uniform answer path: a pending native approval resolves through the
        # interaction layer; anything else is the Stage-A PTY write + newline.
        rid = f.get("request_id") or f.get("question_id")
        if rid and rid in self.interaction:
            self.interaction.answer(rid, f["value"], actor=f.get("actor", "human"),
                                    session_id=f.get("session_id"))
            return
        await self.manager.write(f["session_id"], str(f["value"]) + "\r")

    # interaction layer (Stage B, spec 002)
    async def on_answer_native(self, f: dict) -> dict:
        self.interaction.answer(f["request_id"], f["value"],
                                actor=f.get("actor", "human"),
                                session_id=f.get("session_id"))
        return {"type": "answer.ack", "request_id": f["request_id"]}

    async def on_interaction_pending(self, f: dict) -> dict:
        return {"type": "approval.pending",
                "requests": self.interaction.pending(f.get("session_id"))}

    async def on_session_mode(self, f: dict) -> dict:
        await self.manager.set_mode(f["session_id"], f["mode"])
        return {"type": "mode.set", "session_id": f["session_id"],
                "mode": f["mode"]}

    async def on_stop(self, f: dict) -> None:
        await self.manager.stop(f["session_id"])

    async def on_resize(self, f: dict) -> None:
        self.manager.resize(f["session_id"], int(f["rows"]), int(f["cols"]))

    async def on_list(self, f: dict) -> dict:
        return {"type": "sessions", "sessions": self.manager.list()}

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
        return await websockets.serve(
            self.handle_client,
            host if host is not None else CONFIG.host,
            port if port is not None else CONFIG.port,
            max_size=16 * 1024 * 1024)


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
