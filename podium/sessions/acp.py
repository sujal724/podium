"""AcpSession — Agent Client Protocol over stdio (`kind="acp"`), Stage B.

Generic ACP client (JSON-RPC 2.0, one object per line): Gemini speaks it via
`gemini --acp` (RESEARCH §1.3), the mock worker via a scripted agent. LLD §2.2 named
this `gemini_acp`; it lands as plain `acp` because the protocol is agent-agnostic
(recorded in specs/011 plan).

Native approval channel: the agent's `session/request_permission` request parks on
the Approvals registry as the one uniform prompt; the chosen option id goes back as
the `selected` outcome (or `cancelled` when the session ends first). `set_mode`
maps to ACP `session/set_mode` — Gemini's setSessionMode, adjusting the approval
level mid-session.
"""

import asyncio
import contextlib
import json

from podium.interaction import CANCELLED, Approvals
from podium.sessions.base import Session
from podium.sink import Sink

PROTOCOL_VERSION = 1


class AcpSession(Session):
    def __init__(self, sid: str, label: str, cwd: str, sink: Sink,
                 interaction: Approvals, argv: list[str],
                 prompt: str = "", env: dict[str, str] | None = None) -> None:
        super().__init__(sid, label, "acp", cwd, sink)
        self.interaction = interaction
        self.argv = argv
        self.env = env
        self.mode: str | None = None
        self.modes: list[str] = []
        self._prompt = prompt
        self._proc: asyncio.subprocess.Process | None = None
        self._acp_session: str | None = None
        self._next_id = 0
        self._rpc: dict[int, asyncio.Future] = {}
        self._reader: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self._closed = False
        self._exited = asyncio.Event()

    async def start(self, initial_input: str | None = None) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            *self.argv, cwd=self.cwd, env=self.env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
        self._reader = asyncio.create_task(self._read_loop())
        await self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {"fs": {"readTextFile": False,
                                          "writeTextFile": False}}})
        new = await self._request("session/new", {"cwd": self.cwd,
                                                  "mcpServers": []})
        self._acp_session = new["sessionId"]
        modes = new.get("modes") or {}
        self.mode = modes.get("currentModeId")
        self.modes = [m["id"] for m in modes.get("availableModes", [])]
        self.set_status("running")
        if self._prompt:
            self._spawn(self._turn(self._prompt))

    # --- turns -----------------------------------------------------------------

    async def _turn(self, text: str) -> None:
        try:
            result = await self._request("session/prompt", {
                "sessionId": self._acp_session,
                "prompt": [{"type": "text", "text": text}]})
        except Exception as e:
            if not self._closed:
                self._emit_output(f"\n[acp] turn failed: {e}\n")
            return
        self._emit_output(f"\n[turn] {result.get('stopReason', 'end_turn')}\n")
        if self.status == "running":
            self.set_status("waiting_input")

    async def write(self, text: str) -> None:
        if self._proc is None or self._closed:
            raise RuntimeError(f"session {self.id} is not running")
        self.set_status("running")
        self._spawn(self._turn(text.rstrip("\r\n")))

    async def set_mode(self, mode: str) -> None:
        if self._proc is None or self._closed:
            raise RuntimeError(f"session {self.id} is not running")
        await self._request("session/set_mode", {"sessionId": self._acp_session,
                                                 "modeId": mode})
        self.mode = mode

    # --- JSON-RPC plumbing -------------------------------------------------------

    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _send(self, obj: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            return
        try:
            self._proc.stdin.write((json.dumps(obj) + "\n").encode())
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass  # agent died; teardown follows via the reader's EOF

    async def _request(self, method: str, params: dict) -> dict:
        self._next_id += 1
        rid = self._next_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._rpc[rid] = fut
        await self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                          "params": params})
        return await fut

    async def _read_loop(self) -> None:
        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if "method" in msg and "id" in msg:
                    self._spawn(self._handle_request(msg))
                elif "method" in msg:
                    self._handle_notification(msg)
                elif "id" in msg:
                    fut = self._rpc.pop(msg["id"], None)
                    if fut is not None and not fut.done():
                        if "error" in msg:
                            fut.set_exception(RuntimeError(str(msg["error"])))
                        else:
                            fut.set_result(msg.get("result") or {})
        finally:
            await self._teardown()

    # --- agent → client ---------------------------------------------------------

    async def _handle_request(self, msg: dict) -> None:
        method, params = msg.get("method"), msg.get("params") or {}
        if method == "session/request_permission":
            result = await self._on_permission(params)
            await self._send({"jsonrpc": "2.0", "id": msg["id"], "result": result})
        else:
            # fs/* is honest here: we advertised no fs capability in initialize.
            await self._send({"jsonrpc": "2.0", "id": msg["id"],
                              "error": {"code": -32601,
                                        "message": f"not supported: {method}"}})

    async def _on_permission(self, params: dict) -> dict:
        call = params.get("toolCall") or {}
        options = [{"id": o["optionId"], "label": o.get("name", o["optionId"]),
                    "kind": o.get("kind", "")} for o in params.get("options", [])]
        raw = call.get("rawInput")
        self.set_status("waiting_input")
        try:
            value = await self.interaction.ask(
                self.id, kind="tool",
                title=f"{self.label}: {call.get('title') or 'permission requested'}",
                detail=json.dumps(raw, default=str)[:2000] if raw else "",
                options=options, meta={"toolCallId": call.get("toolCallId")})
        finally:
            if self.status == "waiting_input" and not self._closed:
                self.set_status("running")
        if value == CANCELLED:
            return {"outcome": {"outcome": "cancelled"}}
        return {"outcome": {"outcome": "selected", "optionId": value}}

    def _handle_notification(self, msg: dict) -> None:
        if msg.get("method") != "session/update":
            return
        u = (msg.get("params") or {}).get("update") or {}
        kind = u.get("sessionUpdate")
        if kind in ("agent_message_chunk", "agent_thought_chunk"):
            content = u.get("content") or {}
            if content.get("type") == "text":
                self._emit_output(content.get("text", ""))
        elif kind == "tool_call":
            self._emit_output(f"\n[tool] {u.get('title') or u.get('toolCallId', '')}\n")
        elif kind == "tool_call_update" and u.get("status"):
            self._emit_output(f"[tool] {u.get('toolCallId', '')}: {u['status']}\n")
        elif kind == "current_mode_update":
            self.mode = u.get("currentModeId")
            self._emit_output(f"\n[mode] {self.mode}\n")

    # --- teardown -----------------------------------------------------------------

    async def _teardown(self) -> None:
        if self._closed:
            return
        self._closed = True
        for fut in self._rpc.values():
            if not fut.done():
                fut.set_exception(RuntimeError("acp session closed"))
        self._rpc.clear()
        self.interaction.cancel_session(self.id)
        with contextlib.suppress(ProcessLookupError):
            self._proc.terminate()
        self.exit_code = await self._proc.wait()
        self.set_status("exited" if self.exit_code == 0 else "error")
        self._exited.set()

    async def stop(self) -> None:
        if self._proc is None:
            return
        with contextlib.suppress(ProcessLookupError):
            self._proc.terminate()
        try:
            await asyncio.wait_for(self._exited.wait(), timeout=5)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                self._proc.kill()
            await self._exited.wait()

    async def wait(self) -> int:
        await self._exited.wait()
        return self.exit_code if self.exit_code is not None else -1
