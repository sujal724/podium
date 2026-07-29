"""Codex worker — registered from day one, `blocked` until installed + logged in
(V1 §2: never a fake; the adapter exists, its availability tells the truth)."""

from pathlib import Path

from podium.sessions.base import Session
from podium.sink import Sink
from podium.workers.base import Availability, Worker, register

AUTH = Path("~/.codex/auth.json").expanduser()


@register
class CodexWorker(Worker):
    name = "codex"
    kinds = ["pty"]

    def available(self) -> Availability:
        on_path = self._on_path("codex")
        has_creds = AUTH.exists()
        ok = on_path and has_creds
        hint = "" if ok else "install the Codex CLI and run `codex login`"
        return Availability(ok=ok, on_path=on_path,
                            auth="subscription" if has_creds else "none",
                            tos="gray", headless_ok=False, hint=hint)

    def make_session(self, sid: str, sink: Sink, cwd: str, prompt: str,
                     kind: str | None = None) -> Session:
        from podium.sessions.pty import PtySession
        return PtySession(sid, self.name, cwd, sink, ["codex"])
