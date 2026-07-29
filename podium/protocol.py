"""Wire frames (LLD §18 subset for Stages A–B). One JSON object per WS message.

Server→client constructors live here so every emitter produces the same shape.
Additions over §18 (recorded in specs/001 + specs/002 plans): `task.run`,
`review.approve`, `review.reject`, the `blocked` answer frame for later-stage
surfaces, and the interaction-layer set (`approval.request`/`approval.resolved`
out; `answer.native`/`interaction.pending`/`session.mode` in).
"""

import json
import time


def _f(type_: str, **fields) -> dict:
    fields["type"] = type_
    fields.setdefault("ts", int(time.time()))
    return fields


# --- server → client -------------------------------------------------------

def hello(version: str, workers: list[dict]) -> dict:
    return _f("hello", version=version, workers=workers)


def snapshot(sessions: list[dict], backlogs: dict[str, str]) -> dict:
    return _f("snapshot", sessions=sessions, backlogs=backlogs)


def session_created(info: dict) -> dict:
    return _f("session.created", session=info)


def output(session_id: str, text: str) -> dict:
    return _f("output", session_id=session_id, text=text)


def session_status(session_id: str, status: str) -> dict:
    return _f("session.status", session_id=session_id, status=status)


def question(session_id: str, q: dict) -> dict:
    return _f("question", session_id=session_id, question=q)


def approval_request(session_id: str, request: dict) -> dict:
    """A native approval callback surfaced as the one uniform prompt (LLD §18.2).
    `request` = {id, kind, title, detail, options:[{id,label,kind}], meta?}."""
    return _f("approval.request", session_id=session_id, request=request)


def approval_resolved(session_id: str, request_id: str, value: str,
                      actor: str = "human") -> dict:
    return _f("approval.resolved", session_id=session_id, request_id=request_id,
              value=value, actor=actor)


def work_snapshot(data: dict) -> dict:
    return _f("work.snapshot", **data)


def task_updated(task: dict) -> dict:
    return _f("task.updated", task=task)


def task_claimed(task_id: str, worker: str) -> dict:
    return _f("task.claimed", task_id=task_id, worker=worker)


def task_proposed(task: dict) -> dict:
    return _f("task.proposed", task=task)


def task_inbox(tasks: list[dict]) -> dict:
    return _f("task.inbox", tasks=tasks)


def review_ready(task_id: str, diff: str, branch: str) -> dict:
    return _f("review.ready", task_id=task_id, diff=diff, branch=branch)


def quota_update(worker: str, gauge: dict) -> dict:
    return _f("quota.update", worker=worker, **gauge)


def narration(task_id: str | None, text: str) -> dict:
    return _f("narration", task_id=task_id, text=text)


def blocked(surface: str, stage: str, note: str = "") -> dict:
    """Decision 44: a surface that exists but is not yet functional answers
    `blocked` with its stage — never a fake, never a silent absence."""
    return _f("blocked", surface=surface, stage=stage, note=note)


def error(message: str, detail: str = "") -> dict:
    return _f("error", message=message, detail=detail)


# --- (de)serialization -----------------------------------------------------

def dumps(frame: dict) -> str:
    return json.dumps(frame, separators=(",", ":"))


def loads(raw: str | bytes) -> dict:
    frame = json.loads(raw)
    if not isinstance(frame, dict) or "type" not in frame:
        raise ValueError("frame must be a JSON object with a 'type' field")
    return frame
