"""PtySession — a real PTY around any CLI (LLD §2.2, concrete in Stage A).

The child gets a genuine terminal (no block-buffered pipes — RESEARCH §1.1); raw bytes
stream to the sink at full fidelity; `write()` feeds the child's stdin, so live human
takeover and daemon driving share one path.
"""

import asyncio
import fcntl
import os
import pty
import signal
import struct
import termios

from podium.config import CONFIG
from podium.sessions.base import Session
from podium.sink import Sink


class PtySession(Session):
    def __init__(
        self, sid: str, label: str, cwd: str, sink: Sink,
        argv: list[str], env: dict[str, str] | None = None,
        rows: int | None = None, cols: int | None = None,
    ) -> None:
        super().__init__(sid, label, "pty", cwd, sink)
        self.argv = argv
        self.env = env
        self.pid: int | None = None
        self._fd: int | None = None
        self._exited = asyncio.Event()
        self._rows = rows or CONFIG.pty_rows
        self._cols = cols or CONFIG.pty_cols

    async def start(self, initial_input: str | None = None) -> None:
        loop = asyncio.get_running_loop()
        pid, fd = pty.fork()
        if pid == 0:  # child
            try:
                os.chdir(self.cwd)
                env = dict(os.environ if self.env is None else self.env)
                env.setdefault("TERM", "xterm-256color")
                os.execvpe(self.argv[0], self.argv, env)
            except Exception:
                os._exit(127)
        self.pid, self._fd = pid, fd
        self.resize(self._rows, self._cols)
        os.set_blocking(fd, False)
        loop.add_reader(fd, self._on_readable)
        self.set_status("running")
        if initial_input:
            await asyncio.sleep(0.1)
            await self.write(initial_input + "\r")

    def _on_readable(self) -> None:
        try:
            data = os.read(self._fd, 65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if data:
            self._emit_output(data.decode(errors="replace"))
        else:
            self._teardown()

    def _teardown(self) -> None:
        loop = asyncio.get_running_loop()
        if self._fd is not None:
            loop.remove_reader(self._fd)
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        if self.pid is not None:
            try:
                _, status = os.waitpid(self.pid, 0)
                self.exit_code = os.waitstatus_to_exitcode(status)
            except ChildProcessError:
                self.exit_code = self.exit_code if self.exit_code is not None else -1
            self.pid = None
        self.set_status("exited" if self.exit_code == 0 else
                        ("error" if self.exit_code else "exited"))
        self._exited.set()

    async def write(self, text: str) -> None:
        if self._fd is None:
            raise RuntimeError(f"session {self.id} is not running")
        data = text.encode()
        while data:
            try:
                n = os.write(self._fd, data)
                data = data[n:]
            except BlockingIOError:
                await asyncio.sleep(0.01)
            except OSError:
                return  # PTY closed under us; teardown will follow via reader

    async def stop(self) -> None:
        pid = self.pid
        if pid is None:
            return
        # The child is a session leader (pty.fork), so signal its whole process
        # group — CLIs spawn subprocesses that must not outlive the session.
        self._signal(pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(self._exited.wait(), timeout=5)
        except TimeoutError:
            self._signal(pid, signal.SIGKILL)
            try:
                await asyncio.wait_for(self._exited.wait(), timeout=5)
            except TimeoutError:
                # last resort: the reader never saw EOF; tear down directly
                self._teardown()

    @staticmethod
    def _signal(pid: int, sig: int) -> None:
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, OSError):
                pass

    def resize(self, rows: int, cols: int) -> None:
        if self._fd is None:
            return
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            fcntl.ioctl(self._fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            pass

    async def wait(self) -> int:
        await self._exited.wait()
        return self.exit_code if self.exit_code is not None else -1
