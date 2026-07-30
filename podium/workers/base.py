"""Worker ABC + registry (LLD §3). `available()` reports the full auth + ToS posture —
not just on-PATH — so dispatch can decide what may run unattended (RESEARCH §2).
Coding workers use subscription/login auth only; no API keys, ever."""

import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass

from podium.sessions.base import Session
from podium.sink import Sink


@dataclass
class Availability:
    ok: bool
    on_path: bool
    auth: str            # "subscription" | "oauth" | "none"
    tos: str             # "clean" | "gray" | "n/a"
    headless_ok: bool    # may this worker run unattended?
    hint: str = ""       # install/login guidance when not ok
    warning: str = ""    # e.g. an API key shadowing subscription auth

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class WorkerUnavailable(RuntimeError):
    pass


class Worker(ABC):
    name: str
    kinds: list[str]

    @abstractmethod
    def available(self) -> Availability: ...

    @abstractmethod
    def make_session(
        self, sid: str, sink: Sink, cwd: str, prompt: str, kind: str | None = None,
        resume_key: str | None = None, autonomy: str = "supervised",
        interaction=None,
    ) -> Session:
        """`resume_key` continues a previous session of this worker (its own
        session id) instead of starting fresh — adapters that can't resume ignore
        it and the caller falls back to a fresh run with feedback. `interaction`
        is the Approvals registry native-callback kinds (sdk/acp) park on."""

    def check_kind(self, kind: str | None) -> str:
        """Resolve the requested session kind against this worker's registered
        kinds — an unknown kind is an honest error, never a silent PTY fallback."""
        if kind is None:
            return self.kinds[0]
        if kind not in self.kinds:
            raise WorkerUnavailable(
                f"worker {self.name!r} has no session kind {kind!r} "
                f"(kinds: {self.kinds})")
        return kind

    @staticmethod
    def _on_path(binary: str) -> bool:
        return shutil.which(binary) is not None


WORKERS: dict[str, type[Worker]] = {}


def register(cls: type[Worker]) -> type[Worker]:
    WORKERS[cls.name] = cls
    return cls
