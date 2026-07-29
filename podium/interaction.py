"""Interaction layer — Stage-A slice (LLD §7, pulled forward by spec 002).

The operator has power over the workers, not the other way around: when a worker CLI
stops to ask something on its own screen (Claude's "do you trust this folder?" dialog,
and any other known first-run/permission prompt), Podium detects it in the PTY stream,
raises it as a uniform `question` frame — a real dialog in the cockpit — and writes the
operator's answer back into the same PTY. Podium never answers on its own and never
edits a worker's config behind the operator's back.

Native approval callbacks (SDK `can_use_tool`, ACP) remain the Stage B surface; this is
the PTY-pattern fallback that ships first because dogfooding hit it first.
"""

import re
from dataclasses import dataclass

from podium import protocol
from podium.sink import Sink

_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[=>]")
_WS = re.compile(r"\s+")
TAIL_CHARS = 2000


def canonicalize(text: str) -> str:
    """PTY streams interleave escape sequences mid-word; matching happens on the
    ANSI-stripped, whitespace-free, lowercased form."""
    return _WS.sub("", _ANSI.sub("", text)).lower()


@dataclass(frozen=True)
class PromptPattern:
    id: str                      # stable kind, e.g. "claude.trust"
    workers: tuple[str, ...]     # adapters this applies to
    needle: str                  # sought in the canonicalized tail
    question: str                # Podium's phrasing of the worker's question
    choices: tuple[tuple[str, str], ...]   # (label shown, bytes written on pick)


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
]


@dataclass
class PendingQuestion:
    id: str
    session_id: str
    pattern: PromptPattern


class InteractionLayer:
    def __init__(self, sink: Sink) -> None:
        self.sink = sink
        self._tails: dict[str, str] = {}
        self._asked: dict[str, set[str]] = {}
        self.pending: dict[str, PendingQuestion] = {}

    def scan(self, session_id: str, worker: str, text: str) -> PendingQuestion | None:
        """Feed a chunk of session output; returns (and emits) a question if a known
        prompt just appeared. One question per pattern per session."""
        tail = (self._tails.get(session_id, "") + canonicalize(text))[-TAIL_CHARS:]
        self._tails[session_id] = tail
        asked = self._asked.setdefault(session_id, set())
        for p in PATTERNS:
            if worker in p.workers and p.needle in tail and p.id not in asked:
                asked.add(p.id)
                qid = f"{session_id}:{p.id}"
                pending = PendingQuestion(qid, session_id, p)
                self.pending[qid] = pending
                self.sink.emit(protocol.question(session_id, {
                    "id": qid, "q": p.question, "kind": "choice",
                    "choices": [label for label, _ in p.choices],
                }))
                return pending
        return None

    def answer_bytes(self, question_id: str, value) -> str:
        """Translate an operator answer (1-based index or label) into the bytes the
        worker's own dialog expects. Unknown question → generic line answer."""
        pending = self.pending.pop(question_id, None)
        if pending is None:
            return str(value) + "\r"
        choices = pending.pattern.choices
        v = str(value).strip()
        if v.isdigit() and 1 <= int(v) <= len(choices):
            return choices[int(v) - 1][1]
        for label, write in choices:
            if v.lower() == label.lower():
                return write
        return v + "\r"

    def forget_session(self, session_id: str) -> None:
        self._tails.pop(session_id, None)
        self._asked.pop(session_id, None)
        for qid in [q for q, p in self.pending.items()
                    if p.session_id == session_id]:
            self.pending.pop(qid, None)
