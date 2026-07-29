"""Env-driven settings (PODIUM_*). Defaults keep single-machine, Claude-first working."""

import os
from dataclasses import dataclass, field
from pathlib import Path


def _home(sub: str) -> str:
    return str(Path(os.environ.get("PODIUM_HOME", "~/.podium")).expanduser() / sub)


@dataclass
class Config:
    host: str = os.environ.get("PODIUM_HOST", "127.0.0.1")
    port: int = int(os.environ.get("PODIUM_PORT", "8765"))
    state_db: str = os.environ.get("PODIUM_STATE_DB", "") or _home("state.db")
    workdir: str = os.environ.get("PODIUM_WORKDIR", "") or _home("work")
    default_worker: str = os.environ.get("PODIUM_DEFAULT_WORKER", "claude")
    pty_rows: int = int(os.environ.get("PODIUM_PTY_ROWS", "40"))
    pty_cols: int = int(os.environ.get("PODIUM_PTY_COLS", "120"))
    autoresume: bool = os.environ.get("PODIUM_AUTORESUME", "1") not in ("0", "false")
    # Workers opted into unattended/headless dispatch (posture-gated; RESEARCH §2).
    workers_headless: list[str] = field(
        default_factory=lambda: [
            w.strip()
            for w in os.environ.get("PODIUM_WORKERS_HEADLESS", "claude").split(",")
            if w.strip()
        ]
    )
    # Test/dogfood hook: enable the mock worker.
    enable_mock: bool = os.environ.get("PODIUM_ENABLE_MOCK", "0") not in ("0", "false", "")
    # Self-update watch interval in seconds; 0 disables the periodic check (spec 002).
    update_check_s: int = int(os.environ.get("PODIUM_UPDATE_CHECK_S", "3600"))
    # Mirror worktree Claude sessions into the parent repo's `claude --resume`
    # picker (spec 004).
    session_mirror: bool = os.environ.get("PODIUM_SESSION_MIRROR", "1") \
        not in ("0", "false")

    @property
    def ws_url(self) -> str:
        return f"ws://{self.host}:{self.port}"


CONFIG = Config()
