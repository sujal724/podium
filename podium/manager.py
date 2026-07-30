"""SessionManager (LLD §4): spawn/write/stop/list/snapshot over registered workers."""

from podium import protocol
from podium.config import CONFIG
from podium.ids import new_id
from podium.sessions.base import Session
from podium.sink import Sink
from podium.state import StateStore, now
from podium.workers.base import WORKERS, WorkerUnavailable


class SessionManager:
    def __init__(self, sink: Sink, state: StateStore, interaction=None) -> None:
        self.sessions: dict[str, Session] = {}
        self._sink = sink
        self._state = state
        self._interaction = interaction

    def availability(self) -> list[dict]:
        out = []
        for name, cls in WORKERS.items():
            av = cls().available()
            out.append({"name": name, **av.to_dict()})
        return out

    async def spawn(self, worker: str, prompt: str, cwd: str | None = None,
                    kind: str | None = None, task_id: str | None = None,
                    rows: int | None = None, cols: int | None = None,
                    resume_key: str | None = None,
                    autonomy: str = "supervised") -> Session:
        if worker not in WORKERS:
            raise WorkerUnavailable(f"unknown worker {worker!r}")
        w = WORKERS[worker]()
        av = w.available()
        if not av.ok:
            raise WorkerUnavailable(av.hint or f"{worker} unavailable")
        sid = new_id("s")
        sess = w.make_session(sid, self._sink, cwd or CONFIG.workdir, prompt, kind,
                              resume_key=resume_key, autonomy=autonomy,
                              interaction=self._interaction)
        sess.autonomy = autonomy
        sess.resumed = bool(resume_key)
        sess.task_id = task_id
        # Spawn at the cockpit's pane size when known: a PTY that starts at the
        # right width never interleaves old- and new-width repaints (spec 003 rev 2).
        if rows and cols and hasattr(sess, "_rows"):
            sess._rows, sess._cols = rows, cols
        self.sessions[sid] = sess
        self._state.execute(
            "INSERT INTO sessions(id,task_id,worker,kind,cwd,status,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (sid, task_id, worker, sess.kind, sess.cwd, "starting", now()),
        )
        self._sink.emit(protocol.session_created(sess.info()))
        # Prompt is embedded in the session's argv by the adapter (multiline-safe);
        # start() takes no initial input on the PTY path.
        await sess.start()
        self._state.execute("UPDATE sessions SET status=? WHERE id=?",
                            (sess.status, sid))
        return sess

    async def write(self, sid: str, text: str) -> None:
        await self._get(sid).write(text)

    async def stop(self, sid: str) -> None:
        await self._get(sid).stop()

    def resize(self, sid: str, rows: int, cols: int) -> None:
        self._get(sid).resize(rows, cols)

    async def set_mode(self, sid: str, mode: str) -> None:
        await self._get(sid).set_mode(mode)

    def mark_ended(self, sid: str) -> None:
        sess = self.sessions.get(sid)
        if sess:
            self._state.execute(
                "UPDATE sessions SET status=?, ended_at=? WHERE id=?",
                (sess.status, now(), sid),
            )

    def list(self) -> list[dict]:
        return [s.info() for s in self.sessions.values()]

    def snapshot(self) -> dict:
        return {"sessions": self.list(),
                "backlogs": {sid: s.backlog() for sid, s in self.sessions.items()}}

    def _get(self, sid: str) -> Session:
        if sid not in self.sessions:
            raise KeyError(f"no session {sid}")
        return self.sessions[sid]
