"""Interaction layer (HLD §7, LLD §7) — two slices of one uniform question surface.

The operator has power over the workers, not the other way around. Two channels feed
the same cockpit experience:

- **Native approvals (Stage B, authoritative when present):** a session's own
  callback (Claude SDK `can_use_tool`, ACP `session/request_permission`) parks on
  the `Approvals` registry, goes out as a single `approval.request` frame, and the
  answer — TUI, CLI, or (Stage C) the Brain per autonomy gates — resolves the
  awaiting callback in its driver's idiom.
- **PTY-pattern fallback (Stage-A slice):** when a worker CLI stops to ask on its
  own screen (Claude's "do you trust this folder?" dialog and friends),
  `InteractionLayer` detects it in the PTY stream, raises a uniform `question`
  frame, and writes the operator's answer back into the same PTY.

Podium never answers on its own and never edits a worker's config behind the
operator's back. The sentinel/clarifier degraded paths are registered later
increments.
"""

import asyncio
import re
from dataclasses import dataclass

from podium import protocol
from podium.ids import new_id
from podium.sink import Sink

# --- native approval registry (Stage B, spec 011) ------------------------------

# Resolution handed to a waiting callback when its session ends before an answer
# arrives; adapters translate it into their driver's cancelled/denied outcome.
CANCELLED = "__cancelled__"

APPROVE_DENY = (
    {"id": "allow", "label": "Allow", "kind": "allow_once"},
    {"id": "deny", "label": "Deny", "kind": "reject_once"},
)


class Approvals:
    """Pending-approval registry between a native callback and its answer."""

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


# --- PTY-pattern fallback (Stage-A slice) --------------------------------------

_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[=>]")
_WS = re.compile(r"\s+")
_CHOICE = re.compile(r"^[\s❯>]*([1-9])[.)]\s+(\S.*?)\s*$")
TAIL_CHARS = 2000


def canonicalize(text: str) -> str:
    """PTY streams interleave escape sequences mid-word; matching happens on the
    ANSI-stripped, whitespace-free, lowercased form."""
    return _WS.sub("", _ANSI.sub("", text)).lower()


def strip_ansi(text: str) -> str:
    """ANSI-stripped but layout-preserving — used to read a prompt's own options."""
    return _ANSI.sub("", text)


def parse_choices(text: str) -> list[str]:
    """Pull a prompt's numbered options ('1. Yes', '2. No') out of screen text,
    so Podium offers exactly what the worker offers — never a guessed menu."""
    found: dict[int, str] = {}
    for line in text.splitlines():
        m = _CHOICE.match(line)
        if m:
            found[int(m.group(1))] = m.group(2)[:80]
    out = []
    for i in range(1, 10):
        if i not in found:
            break
        out.append(found[i])
    return out


@dataclass(frozen=True)
class PromptPattern:
    id: str                      # stable kind, e.g. "claude.trust"
    workers: tuple[str, ...]     # adapters this applies to
    needle: str                  # sought in the canonicalized tail
    question: str                # Podium's phrasing of the worker's question
    choices: tuple[tuple[str, str], ...]   # (label shown, bytes written on pick)
    dynamic: bool = False        # read the options off the screen instead


PATTERNS: list[PromptPattern] = [
    PromptPattern(
        "claude.trust", ("claude", "mock"), "itrustthisfolder",
        "Claude asks: do you trust this workspace folder? "
        "(Podium worktrees contain the project's own code.)",
        (("Yes — trust this folder", "1\r"),
         ("No — exit the session", "2\r")),
    ),
    PromptPattern(
        "claude.login", ("claude",), "selectloginmethod",
        "Claude is not logged in — the session is at the login screen. "
        "Take over the terminal to authenticate, or stop the session.",
        (("Stop the session", "\x03"),),
    ),
    # Permission/approval prompts: the worker's own options are read off the
    # screen, so Podium relays exactly what it offers (tool approvals vary).
    PromptPattern(
        "claude.permission", ("claude", "mock"), "doyouwanttoproceed",
        "The worker needs your approval to proceed (see the session pane for the "
        "command).", (), dynamic=True,
    ),
    # Bypass mode's own risk acceptance. Podium NEVER answers this itself — the
    # operator accepts the risk or doesn't, whatever mode was requested.
    PromptPattern(
        "claude.bypass_accept", ("claude",), "youacceptallresponsibility",
        "Bypass mode asks you to accept full responsibility for unprompted actions "
        "in this session. Podium will not answer this for you.", (), dynamic=True,
    ),
]

# What a live session's permission mode looks like in the CLI's own status line,
# and the mode each maps to. Used to switch modes on a RUNNING session (spec 006).
MODE_MARKERS = {
    "bypassinglepermissions": "bypass",
    "bypasspermissionson": "bypass",
    "acceptedidson": "autonomous",
    "accepteditson": "autonomous",
    "planmodeon": "plan",
}
CYCLE_KEY = "\x1b[Z"        # shift+tab — the CLI's own mode cycle


def detect_mode(text: str) -> str | None:
    """Read the session's current permission mode off its status line."""
    flat = canonicalize(text)
    for marker, mode in MODE_MARKERS.items():
        if marker in flat:
            return mode
    return None


@dataclass
class PendingQuestion:
    id: str
    session_id: str
    pattern: PromptPattern
    choices: tuple[str, ...] = ()   # labels actually offered (dynamic prompts)


class InteractionLayer:
    def __init__(self, sink: Sink) -> None:
        self.sink = sink
        self._tails: dict[str, str] = {}
        self._raw_tails: dict[str, str] = {}
        self._asked: dict[str, set[str]] = {}
        self._seq = 0
        self.pending: dict[str, PendingQuestion] = {}

    def scan(self, session_id: str, worker: str, text: str) -> PendingQuestion | None:
        """Feed a chunk of session output; emits a question when a known prompt
        appears. A pattern re-arms once its prompt leaves the screen, so each new
        approval is asked again (they are separate decisions)."""
        tail = (self._tails.get(session_id, "") + canonicalize(text))[-TAIL_CHARS:]
        self._tails[session_id] = tail
        raw = (self._raw_tails.get(session_id, "") + strip_ansi(text))[-TAIL_CHARS * 3:]
        self._raw_tails[session_id] = raw
        asked = self._asked.setdefault(session_id, set())
        asked.intersection_update({p.id for p in PATTERNS if p.needle in tail})

        for p in PATTERNS:
            if worker not in p.workers or p.needle not in tail or p.id in asked:
                continue
            if p.dynamic:
                labels = tuple(parse_choices(raw))
                if not labels:
                    continue          # options not on screen yet — ask next chunk
            else:
                labels = tuple(label for label, _ in p.choices)
            asked.add(p.id)
            self._seq += 1
            qid = f"{session_id}:{p.id}:{self._seq}"
            pending = PendingQuestion(qid, session_id, p, labels)
            self.pending[qid] = pending
            self.sink.emit(protocol.question(session_id, {
                "id": qid, "q": p.question, "kind": "choice",
                "choices": list(labels),
            }))
            return pending
        return None

    def answer_bytes(self, question_id: str, value) -> str:
        """Translate an operator answer (1-based index or label) into the bytes the
        worker's own dialog expects. Unknown question → generic line answer."""
        pending = self.pending.pop(question_id, None)
        if pending is None:
            return str(value) + "\r"
        v = str(value).strip()
        if pending.pattern.dynamic:
            # the worker's own menu: the number is the answer
            if v.isdigit() and 1 <= int(v) <= len(pending.choices):
                return v + "\r"
            for i, label in enumerate(pending.choices, start=1):
                if v.lower() == label.lower():
                    return f"{i}\r"
            return v + "\r"
        choices = pending.pattern.choices
        if v.isdigit() and 1 <= int(v) <= len(choices):
            return choices[int(v) - 1][1]
        for label, write in choices:
            if v.lower() == label.lower():
                return write
        return v + "\r"

    def forget_session(self, session_id: str) -> None:
        self._tails.pop(session_id, None)
        self._raw_tails.pop(session_id, None)
        self._asked.pop(session_id, None)
        for qid in [q for q, p in self.pending.items()
                    if p.session_id == session_id]:
            self.pending.pop(qid, None)
