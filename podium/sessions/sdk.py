"""SdkSession — Claude Agent SDK (`kind="sdk"`, LLD §2.2), Stage B.

Persistent bidirectional `ClaudeSDKClient`; the SDK bundles and shells to the real
`claude` binary over stream-json with the control channel implemented (RESEARCH §1.1).
Its `can_use_tool` callback is the native approval channel: every permission request
parks on the Approvals registry as the one uniform prompt, and the answer maps back to
a PermissionResult. `set_mode` maps to the SDK's `set_permission_mode`.

The SDK package is an optional dependency (`pip install 'podium[sdk]'`); the import
happens lazily in the default client factory so nothing else pays for it, and tests
inject a fake factory.
"""

import asyncio
import contextlib
import json

from podium.interaction import APPROVE_DENY, Approvals
from podium.sessions.base import Session
from podium.sink import Sink


def _default_client_factory(session: "SdkSession"):
    try:
        import claude_agent_sdk as sdk
    except ModuleNotFoundError as e:  # pragma: no cover — guarded at make_session
        raise RuntimeError(
            "session kind 'sdk' needs the Claude Agent SDK: "
            "pip install 'podium[sdk]'") from e
    session._sdk = sdk
    extra = {}
    if session.settings:
        extra["settings"] = str(session.settings)
    if session.env is not None:
        extra["env"] = session.env
    options = sdk.ClaudeAgentOptions(
        cwd=session.cwd,
        permission_mode=session.permission_mode,
        can_use_tool=session.can_use_tool,
        **extra,
    )
    return sdk.ClaudeSDKClient(options=options)


class SdkSession(Session):
    def __init__(self, sid: str, label: str, cwd: str, sink: Sink,
                 interaction: Approvals, prompt: str = "",
                 settings: str | None = None, env: dict[str, str] | None = None,
                 permission_mode: str = "default", client_factory=None) -> None:
        super().__init__(sid, label, "sdk", cwd, sink)
        self.interaction = interaction
        self.settings = settings
        self.env = env
        # "default" so permission requests actually flow to can_use_tool — the
        # uniform prompt is the point of this kind (acceptEdits is the PTY path).
        self.permission_mode = permission_mode
        self._prompt = prompt
        self._client_factory = client_factory or _default_client_factory
        self._client = None
        self._sdk = None                 # module handle for PermissionResult types
        self._reader: asyncio.Task | None = None
        self._exited = asyncio.Event()

    async def start(self, initial_input: str | None = None) -> None:
        self._client = self._client_factory(self)
        await self._client.connect()
        self.set_status("running")
        self._reader = asyncio.create_task(self._read_loop())
        if self._prompt:
            await self._client.query(self._prompt)

    # --- native approval channel → uniform prompt ---------------------------

    async def can_use_tool(self, tool_name: str, tool_input: dict, context=None):
        self.set_status("waiting_input")
        try:
            value = await self.interaction.ask(
                self.id, kind="tool",
                title=f"{self.label}: allow tool {tool_name}?",
                detail=json.dumps(tool_input, default=str)[:2000],
                options=APPROVE_DENY, meta={"tool": tool_name})
        finally:
            if self.status == "waiting_input":
                self.set_status("running")
        return self._permission_result(value == "allow")

    def _permission_result(self, allow: bool):
        if self._sdk is None:  # fake-client path (tests): plain shape
            return {"behavior": "allow" if allow else "deny"}
        if allow:
            return self._sdk.PermissionResultAllow()
        return self._sdk.PermissionResultDeny(
            message="denied via the Podium interaction layer")

    # --- stream normalization -------------------------------------------------

    async def _read_loop(self) -> None:
        try:
            async for msg in self._client.receive_messages():
                self._on_message(msg)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._emit_output(f"\n[sdk] stream error: {e}\n")
            self.exit_code = 1
        finally:
            self.interaction.cancel_session(self.id)
            if self.exit_code is None:
                self.exit_code = 0
            self.set_status("exited" if self.exit_code == 0 else "error")
            self._exited.set()

    def _on_message(self, msg) -> None:
        for block in getattr(msg, "content", None) or []:
            text = getattr(block, "text", None)
            if text is not None:
                self._emit_output(text if text.endswith("\n") else text + "\n")
            elif getattr(block, "name", None):
                self._emit_output(f"[tool] {block.name}\n")
        if type(msg).__name__ == "ResultMessage":
            # End of a turn, not of the session — the client stays connected.
            self._emit_output(f"[result] {getattr(msg, 'subtype', 'done')}\n")
            self.set_status("waiting_input")

    # --- control ---------------------------------------------------------------

    async def write(self, text: str) -> None:
        if self._client is None:
            raise RuntimeError(f"session {self.id} is not running")
        self.set_status("running")
        await self._client.query(text.rstrip("\r\n"))

    async def set_mode(self, mode: str) -> None:
        if self._client is None:
            raise RuntimeError(f"session {self.id} is not running")
        await self._client.set_permission_mode(mode)
        self._emit_output(f"\n[mode] {mode}\n")

    async def stop(self) -> None:
        if self._client is None:
            return
        with contextlib.suppress(Exception):
            await self._client.disconnect()
        if self._reader is not None:
            try:
                await asyncio.wait_for(self._exited.wait(), timeout=5)
            except TimeoutError:
                self._reader.cancel()
                await self._exited.wait()

    async def wait(self) -> int:
        await self._exited.wait()
        return self.exit_code if self.exit_code is not None else -1
