"""Metering ledger + quota gauge + backoff (Stage A slice of LLD §8.3/§16).

ADR-0005: quota is READ, not estimated. No read surface is wired in this iteration, so
the gauge reports `unavailable` for every window — never an estimate, never a fake
number (decision 44). The `QuotaReader` ABC is the socket the read surfaces plug into
(/usage PTY-parse, per-CLI counters — next increment on this surface).

What IS live: the metering ledger (every session attributed: worker, task, wall time,
outcome) and rate-limit backoff — adapter regexes over session output flip a worker to
`limited` and `may_dispatch()` to False until the observed reset (or a default hold).
"""

import re
import time
from abc import ABC, abstractmethod

from podium import protocol
from podium.sink import Sink
from podium.state import StateStore

DEFAULT_HOLD_S = 15 * 60  # backoff hold when a limit gives no reset time

RATE_LIMIT_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"rate limit(ed|\s+reached|\s+exceeded)?",
        r"usage limit (reached|exceeded)",
        r"quota exceeded",
        r"429",
        r"too many requests",
    )
]


class QuotaReader(ABC):
    """A per-worker authenticated read surface (ADR-0005). None wired in Stage A."""

    @abstractmethod
    def read(self) -> dict:
        """→ {"window_5h": {...}, "weekly": {...}} with real numbers, or raise."""


class QuotaGauge:
    """Truthful per-worker gauge state: read numbers when a reader exists, otherwise
    `unavailable`; `limited` overlays when backoff is active."""

    def __init__(self, sink: Sink) -> None:
        self.sink = sink
        self.readers: dict[str, QuotaReader] = {}
        self._limited_until: dict[str, float] = {}

    def state(self, worker: str) -> dict:
        limited_until = self._limited_until.get(worker)
        limited = bool(limited_until and limited_until > time.time())
        reader = self.readers.get(worker)
        if reader is None:
            windows = {"window_5h": "unavailable", "weekly": "unavailable",
                       "reason": "no read surface wired (ADR-0005: read, not estimated)"}
        else:
            try:
                windows = reader.read()
            except Exception as e:
                windows = {"window_5h": "unavailable", "weekly": "unavailable",
                           "reason": f"read surface failed: {e}"}
        return {"limited": limited,
                "limited_until": int(limited_until) if limited else None, **windows}

    def mark_limited(self, worker: str, until: float | None = None) -> None:
        self._limited_until[worker] = until or (time.time() + DEFAULT_HOLD_S)
        self.sink.emit(protocol.quota_update(worker, self.state(worker)))

    def may_dispatch(self, worker: str) -> bool:
        until = self._limited_until.get(worker)
        return not (until and until > time.time())


class Meter:
    """The ledger: every session/request attributed for history + reconciliation."""

    def __init__(self, state: StateStore, sink: Sink) -> None:
        self.state = state
        self.gauge = QuotaGauge(sink)
        self._scan_tail: dict[str, str] = {}

    def record_session(self, session_id: str, task_id: str | None, worker: str,
                       kind: str, started: int, ended: int, outcome: str) -> None:
        self.state.execute(
            "INSERT INTO metrics(session_id,task_id,actor,kind,value,ts)"
            " VALUES(?,?,?,?,?,?)",
            (session_id, task_id, "agent", "time", float(ended - started), ended),
        )
        self.state.execute(
            "INSERT INTO quota_ledger(worker,window,spent,budget,window_start,"
            "window_reset,ts) VALUES(?,?,?,?,?,?,?)",
            (worker, "session", 1.0, -1.0, started, 0, ended),
        )
        self.state.log_event(session_id, "meter.session",
                             {"worker": worker, "kind": kind, "task_id": task_id,
                              "outcome": outcome, "seconds": ended - started})

    def scan_output(self, worker: str, session_id: str, text: str) -> bool:
        """Rate-limit detection over the live stream. Keeps a small tail per session so
        markers split across reads still match."""
        buf = (self._scan_tail.get(session_id, "") + text)[-512:]
        self._scan_tail[session_id] = buf
        if any(p.search(buf) for p in RATE_LIMIT_PATTERNS):
            self._scan_tail[session_id] = ""
            self.gauge.mark_limited(worker)
            self.state.log_event(session_id, "meter.rate_limited", {"worker": worker})
            return True
        return False
