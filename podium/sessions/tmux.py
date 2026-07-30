"""TmuxSession — the worker runs in a REAL terminal (spec 010).

No emulation: the CLI lives in a tmux pane rendered by the operator's own terminal,
with native input. Podium keeps full visibility by capturing a *copy* of the pane's
bytes (`pipe-pane`) into the transcript it already tails — so metering, rate-limit
scanning, interaction prompts, hooks and the agent tree work exactly as before, off
the capture path rather than the display path.

Takeover works from either side: type directly into the pane, or `write()` from the
cockpit (`send-keys`) — same PTY.
"""

import asyncio
import codecs
import shutil
import subprocess
from pathlib import Path

from podium.config import CONFIG
from podium.sessions.base import Session
from podium.sink import Sink

POLL_S = 0.2


class TmuxUnavailable(RuntimeError):
    pass


def available() -> bool:
    return shutil.which("tmux") is not None


def _tmux(*args: str, check: bool = True) -> str:
    proc = subprocess.run(["tmux", *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise TmuxUnavailable(f"tmux {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


class TmuxSession(Session):
    def __init__(self, sid: str, label: str, cwd: str, sink: Sink,
                 argv: list[str], env: dict[str, str] | None = None,
                 rows: int | None = None, cols: int | None = None) -> None:
        super().__init__(sid, label, "tmux", cwd, sink)
        self.argv = argv
        self.env = env
        self.tmux_name = f"podium-{sid}"
        self._rows = rows or CONFIG.pty_rows
        self._cols = cols or CONFIG.pty_cols
        self._capture = Path(cwd) / ".podium" / f"{sid}.capture"
        self._offset = 0
        self._reader: asyncio.Task | None = None
        self._exited = asyncio.Event()

    async def start(self, initial_input: str | None = None) -> None:
        if not available():
            raise TmuxUnavailable(
                "tmux is not installed — install it for real terminal panes, or set "
                "PODIUM_TERM_BACKEND=pty for the emulated pane")
        self._capture.parent.mkdir(parents=True, exist_ok=True)
        self._capture.touch()
        env_args: list[str] = []
        for key, value in (self.env or {}).items():
            if key in ("PATH", "PODIUM_SESSION_ID", "PODIUM_TASK_ID",
                       "PODIUM_DIR", "PODIUM_PEER_DEPTH", "TERM"):
                env_args += ["-e", f"{key}={value}"]
        _tmux("new-session", "-d", "-s", self.tmux_name, "-c", self.cwd,
              "-x", str(self._cols), "-y", str(self._rows), *env_args, *self.argv)
        # capture a copy of everything the pane renders — the daemon's eyes
        _tmux("pipe-pane", "-o", "-t", self.tmux_name,
              f"cat >> {self._capture!s}")
        self.set_status("running")
        self._reader = asyncio.create_task(self._read_loop())
        if initial_input:
            await self.write(initial_input + "\r")

    async def _read_loop(self) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        while True:
            try:
                with self._capture.open("rb") as f:
                    f.seek(self._offset)
                    data = f.read()
                    self._offset = f.tell()
            except OSError:
                data = b""
            if data:
                text = decoder.decode(data)
                if text:
                    self._emit_output(text)
            if not self._alive():
                break
            await asyncio.sleep(POLL_S)
        self.exit_code = 0
        self.set_status("exited")
        self._exited.set()

    def _alive(self) -> bool:
        proc = subprocess.run(["tmux", "has-session", "-t", self.tmux_name],
                              capture_output=True)
        return proc.returncode == 0

    async def write(self, text: str) -> None:
        if not self._alive():
            raise RuntimeError(f"session {self.id} is not running")
        # -l sends the text literally; a trailing CR becomes an Enter keypress
        if text.endswith("\r") or text.endswith("\n"):
            body = text[:-1]
            if body:
                _tmux("send-keys", "-t", self.tmux_name, "-l", body, check=False)
            _tmux("send-keys", "-t", self.tmux_name, "Enter", check=False)
        else:
            _tmux("send-keys", "-t", self.tmux_name, "-l", text, check=False)

    async def stop(self) -> None:
        if self._alive():
            _tmux("kill-session", "-t", self.tmux_name, check=False)
        try:
            await asyncio.wait_for(self._exited.wait(), timeout=10)
        except TimeoutError:
            if self._reader:
                self._reader.cancel()
            self.set_status("exited")
            self._exited.set()

    def resize(self, rows: int, cols: int) -> None:
        self._rows, self._cols = rows, cols
        if self._alive():
            _tmux("resize-window", "-t", self.tmux_name, "-x", str(cols),
                  "-y", str(rows), check=False)

    async def wait(self) -> int:
        await self._exited.wait()
        return self.exit_code if self.exit_code is not None else 0

    # --- display -----------------------------------------------------------

    def attach_command(self) -> list[str]:
        """How an operator attaches to this session's real terminal."""
        return ["tmux", "attach", "-t", self.tmux_name]

    @staticmethod
    def join_into(window: str, session_name: str) -> None:
        """Move a worker's pane INTO the cockpit window — one page, two real
        terminals (spec 010)."""
        _tmux("join-pane", "-h", "-s", f"{session_name}:", "-t", window)
