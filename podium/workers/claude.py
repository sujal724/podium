"""Claude worker — the safe unattended default (ToS clean, subscription auth).

Stage A drives the real `claude` CLI under a PTY with the task prompt as the initial
message, a generated settings file carrying the peer-call deny rules + lifecycle hooks
(decision 50), and `--permission-mode acceptEdits` so a dispatched task can edit its
isolated worktree without a human at the keyboard. SDK/stream-json kinds are Stage B+.
"""

import os
from pathlib import Path

from podium import policy
from podium.sessions.base import Session
from podium.sessions.pty import PtySession
from podium.sink import Sink
from podium.workers.base import Availability, Worker, register

CREDENTIALS = Path("~/.claude/.credentials.json").expanduser()


@register
class ClaudeWorker(Worker):
    name = "claude"
    kinds = ["pty"]

    def available(self) -> Availability:
        on_path = self._on_path("claude")
        has_creds = CREDENTIALS.exists()
        warning = ""
        if os.environ.get("ANTHROPIC_API_KEY"):
            warning = ("ANTHROPIC_API_KEY is set and would override subscription auth; "
                       "it is stripped from worker sessions (RESEARCH §2).")
        ok = on_path and has_creds
        hint = "" if ok else (
            "install: npm install -g @anthropic-ai/claude-code" if not on_path
            else "login: run `claude` once and authenticate (subscription)"
        )
        return Availability(ok=ok, on_path=on_path,
                            auth="subscription" if has_creds else "none",
                            tos="clean", headless_ok=True, hint=hint, warning=warning)

    def make_session(self, sid: str, sink: Sink, cwd: str, prompt: str,
                     kind: str | None = None) -> Session:
        settings = policy.write_claude_settings(cwd)
        # Initial prompt rides argv (starts the REPL with it already submitted) —
        # multiline-safe, unlike typing it into the PTY.
        argv = ["claude", "--settings", str(settings), "--permission-mode", "acceptEdits"]
        if prompt:
            argv.append(prompt)
        env = {k: v for k, v in os.environ.items()
               if k not in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY")}
        return PtySession(sid, self.name, cwd, sink, argv, env=env)
