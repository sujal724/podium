"""Interaction layer (HLD §7, LLD §7) — Stage B slice: native approval callbacks.

One uniform question/approval experience across drivers: a session's native callback
(Claude SDK `can_use_tool`, ACP `session/request_permission`) parks here as a pending
request, goes out on the sink as a single `approval.request` frame, and the answer —
from the TUI, the CLI, or (Stage C) the Brain per autonomy gates — resolves the
awaiting callback. The sentinel/clarifier degraded fallbacks are registered later
increments; native channels are authoritative when present.
"""

import asyncio

from podium import protocol
from podium.ids import new_id
from podium.sink import Sink

# Resolution handed to a waiting callback when its session ends before an answer
# arrives; adapters translate it into their driver's cancelled/denied outcome.
CANCELLED = "__cancelled__"

APPROVE_DENY = (
    {"id": "allow", "label": "Allow", "kind": "allow_once"},
    {"id": "deny", "label": "Deny", "kind": "reject_once"},
)


class InteractionLayer:
    def __init__(self, sink: Sink) -> None:
        self._sink = sink
        self._pending: dict[str, dict] = {}          # request_id → uniform record
        self._futures: dict[str, asyncio.Future] = {}

    def __contains__(self, request_id: str) -> bool:
        return request_id in self._pending

    def pending(self, session_id: str | None = None) -> list[dict]:
        return [r for r in self._pending.values()
                if session_id is None or r["session_id"] == session_id]

    async def ask(self, session_id: str, title: str, detail: str = "",
                  options: tuple | list = APPROVE_DENY, kind: str = "approval",
                  meta: dict | None = None) -> str:
        """Park a native callback as one uniform prompt; resolves to the chosen
        option id (or CANCELLED if the session ends first)."""
        record = {"id": new_id("q"), "session_id": session_id, "kind": kind,
                  "title": title, "detail": detail,
                  "options": [dict(o) for o in options], "meta": meta or {}}
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[record["id"]] = record
        self._futures[record["id"]] = fut
        self._sink.emit(protocol.approval_request(session_id, record))
        return await fut

    def answer(self, request_id: str, value: str, actor: str = "human",
               session_id: str | None = None) -> dict:
        record = self._pending.get(request_id)
        if record is None or (session_id and record["session_id"] != session_id):
            raise KeyError(f"no pending approval {request_id!r}")
        value = str(value)
        valid = {o["id"] for o in record["options"]}
        if valid and value not in valid:
            raise ValueError(
                f"invalid answer {value!r} for {request_id} (options: {sorted(valid)})")
        self._resolve(record, value, actor)
        return record

    def cancel_session(self, session_id: str) -> None:
        """Session ended: resolve its pending prompts as cancelled — a dangling
        prompt would be a fake surface (decision 44)."""
        for record in [r for r in self._pending.values()
                       if r["session_id"] == session_id]:
            self._resolve(record, CANCELLED, actor="system")

    def _resolve(self, record: dict, value: str, actor: str) -> None:
        rid = record["id"]
        self._pending.pop(rid, None)
        fut = self._futures.pop(rid, None)
        if fut is not None and not fut.done():
            fut.set_result(value)
        self._sink.emit(protocol.approval_resolved(
            record["session_id"], rid,
            "cancelled" if value == CANCELLED else value, actor))
