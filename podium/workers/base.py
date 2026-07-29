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
        self, sid: str, sink: Sink, cwd: str, prompt: str, kind: str | None = None
    ) -> Session: ...

    @staticmethod
    def _on_path(binary: str) -> bool:
        return shutil.which(binary) is not None


WORKERS: dict[str, type[Worker]] = {}


def register(cls: type[Worker]) -> type[Worker]:
    WORKERS[cls.name] = cls
    return cls
