"""Gemini worker — PTY drive from Stage A; Stage B adds `kind="acp"` (`gemini --acp`,
JSON-RPC over stdio) with native approval callbacks + setSessionMode landing on the
interaction layer as the uniform prompt (spec 002).
ToS gray: unattended use is operator opt-in (PODIUM_WORKERS_HEADLESS)."""

import os
from pathlib import Path

from podium import policy
from podium.config import CONFIG
from podium.sessions.acp import AcpSession
from podium.sessions.base import Session
from podium.sessions.pty import PtySession
from podium.sink import Sink
from podium.workers.base import Availability, Worker, register

OAUTH_CREDS = Path("~/.gemini/oauth_creds.json").expanduser()


@register
class GeminiWorker(Worker):
    name = "gemini"
    kinds = ["pty", "acp"]

    def available(self) -> Availability:
        on_path = self._on_path("gemini")
        has_creds = OAUTH_CREDS.exists()
        ok = on_path and has_creds
        hint = "" if ok else (
            "install: npm install -g @google/gemini-cli" if not on_path
            else "login: run `gemini` once and sign in with Google"
        )
        return Availability(ok=ok, on_path=on_path,
                            auth="oauth" if has_creds else "none",
                            tos="gray", headless_ok="gemini" in CONFIG.workers_headless,
                            hint=hint)

    def make_session(self, sid: str, sink: Sink, cwd: str, prompt: str,
                     kind: str | None = None, interaction=None) -> Session:
        kind = self.check_kind(kind)
        # No settings-level deny surface wired yet: the peer-call deny rides the task
        # preamble (policy.PREAMBLE_DENY, prepended by the dispatcher) — recorded
        # increment, see surfaces.py `peer.deny`.
        env = {k: v for k, v in os.environ.items()
               if k not in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY")}
        _ = policy  # deny lands in the prompt for this adapter
        if kind == "acp":
            return AcpSession(sid, self.name, cwd, sink, interaction,
                              ["gemini", "--acp"], prompt=prompt, env=env)
        argv = ["gemini"] + (["-i", prompt] if prompt else [])
        return PtySession(sid, self.name, cwd, sink, argv, env=env)
