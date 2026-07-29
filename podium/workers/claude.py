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
                     kind: str | None = None, resume_key: str | None = None,
                     autonomy: str = policy.DEFAULT_AUTONOMY) -> Session:
        settings = policy.write_claude_settings(cwd, autonomy)
        # Initial prompt rides argv (starts the REPL with it already submitted) —
        # multiline-safe, unlike typing it into the PTY.
        argv = ["claude", "--settings", str(settings)]
        if autonomy == "bypass":
            # asks nothing at all; the peer-call guard hook still runs (decision 50)
            argv.append("--dangerously-skip-permissions")
        else:
            argv += ["--permission-mode", "acceptEdits"]
        if resume_key:
            # Continue the interrupted conversation instead of starting over:
            # context, plan, and completed work are all preserved (spec 005).
            argv += ["--resume", resume_key]
        if prompt:
            argv.append(prompt)
        return PtySession(sid, self.name, cwd, sink, argv,
                          env=policy.worker_env(cwd))
