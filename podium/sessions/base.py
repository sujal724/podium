"""Session ABC (LLD §2.1). Everything downstream depends only on this contract.

Concrete kinds: `pty` (Stage A), `sdk` + `acp` (Stage B interaction layer — Claude
Agent SDK and Agent Client Protocol, both with native approval callbacks). The rest
(headless/api/codex_rpc) are registered blocked surfaces (surfaces.py), not silent
absences.
"""

from abc import ABC, abstractmethod

from podium import protocol
from podium.sink import Sink

BUFFER_CAP = 2_000_000  # bytes of transcript kept in memory for late-attaching clients

STATUSES = ("starting", "running", "waiting_input", "exited", "error")


class Session(ABC):
    def __init__(self, sid: str, label: str, kind: str, cwd: str, sink: Sink) -> None:
        self.id = sid
        self.label = label          # worker name: "claude" | "gemini" | ...
        self.kind = kind
        self.status = "starting"
        self.cwd = cwd
        self.exit_code: int | None = None
        # Which driver owns write(): human/Brain vs the autonomy controller (LLD §25).
        # Stage A sessions are dispatched autonomous and takeover-able at the PTY level;
        # the drive_mode *swap* machinery is a Stage C surface.
        self.drive_mode = "interactive"
        # Which permission posture this session runs under, and whether it picked up
        # a previous conversation — both are shown in the cockpit, never implicit.
        self.autonomy = "supervised"
        self.resumed = False
        self.task_id: str | None = None
        self._sink = sink
        self._buffer = bytearray()

    def info(self) -> dict:
        return {"id": self.id, "label": self.label, "kind": self.kind,
                "status": self.status, "cwd": self.cwd, "drive_mode": self.drive_mode,
                "autonomy": self.autonomy, "resumed": self.resumed,
                "task_id": self.task_id, "exit_code": self.exit_code}

    def backlog(self) -> str:
        return self._buffer.decode(errors="replace")

    def _emit_output(self, text: str) -> None:
        self._buffer += text.encode()
        if len(self._buffer) > BUFFER_CAP:
            del self._buffer[: len(self._buffer) - BUFFER_CAP]
        self._sink.emit(protocol.output(self.id, text))

    def set_status(self, status: str) -> None:
        if status == self.status:
            return
        self.status = status
        self._sink.emit(protocol.session_status(self.id, status))

    @abstractmethod
    async def start(self, initial_input: str | None = None) -> None: ...

    @abstractmethod
    async def write(self, text: str) -> None:
        """One shared input path — daemon and human takeover both land here."""

    @abstractmethod
    async def stop(self) -> None: ...

    def resize(self, rows: int, cols: int) -> None:  # optional capability
        pass

    async def set_mode(self, mode: str) -> None:  # optional capability
        """Uniform approval-mode control (SDK `set_permission_mode`; ACP
        `session/set_mode`). Raises on kinds without a native mode channel —
        an honest error, never a silent no-op."""
        raise NotImplementedError(f"session kind {self.kind!r} has no mode control")

    async def wait(self) -> int:
        """Block until the session exits; return exit code."""
        raise NotImplementedError
