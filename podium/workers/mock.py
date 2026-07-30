"""Mock worker — a tiny scripted CLI under a real PTY (A1/A2), plus a scripted ACP
agent (`kind="acp"`) proving the uniform approval prompt (spec 012 B-criteria) —
all without spending any quota. Only registered when PODIUM_ENABLE_MOCK is set,
so it can never be routed real work by accident."""

import sys
from pathlib import Path

from podium.config import CONFIG
from podium.sessions.acp import AcpSession
from podium.sessions.base import Session
from podium.sessions.pty import PtySession
from podium.sink import Sink
from podium.workers.base import Availability, Worker, register

MOCK_CLI = Path(__file__).parent / "mock_cli.py"
MOCK_ACP_AGENT = Path(__file__).parent / "mock_acp_agent.py"


class MockWorker(Worker):
    name = "mock"
    kinds = ["pty", "acp"]

    def available(self) -> Availability:
        return Availability(ok=True, on_path=True, auth="none", tos="n/a",
                            headless_ok=True)

    def make_session(self, sid: str, sink: Sink, cwd: str, prompt: str,
                     kind: str | None = None, resume_key: str | None = None,
                     autonomy: str = "supervised", interaction=None) -> Session:
        kind = self.check_kind(kind)
        if kind == "acp":
            return AcpSession(sid, self.name, cwd, sink, interaction,
                              [sys.executable, "-u", str(MOCK_ACP_AGENT)],
                              prompt=prompt)
        argv = [sys.executable, "-u", str(MOCK_CLI), prompt]
        if resume_key:
            argv.append(f"--resume={resume_key}")
        return PtySession(sid, self.name, cwd, sink, argv)


if CONFIG.enable_mock:
    register(MockWorker)
