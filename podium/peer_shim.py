"""Peer-call broker, worker side (spec 009).

A worker session invoking another harness lands here (the shims on its PATH point at
this module). It does NOT decide anything: it files a request with full provenance and
waits for the daemon's decision, which follows the session's **autonomy mode** —
supervised asks the operator, autonomous/bypass auto-approve. On approval the real
binary is exec'd (so its output flows through the worker's own PTY and its work is
attributed to the requesting session); on refusal the reason is printed.

Podium can launch anything — permission is a *mode* question, not a hardcoded wall.
The one unconditional rule is the depth cap: a spawned harness may not keep spawning,
because unbounded recursion is a runaway-cost hazard rather than a permission choice.
"""

import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

WAIT_S = float(os.environ.get("PODIUM_PEER_WAIT_S", "300"))
POLL_S = 0.25
MAX_DEPTH = int(os.environ.get("PODIUM_PEER_MAX_DEPTH", "1"))


def _podium_dir() -> Path:
    return Path(os.environ.get("PODIUM_DIR", "")) if os.environ.get("PODIUM_DIR") \
        else Path.cwd() / ".podium"


def _real_binary(name: str) -> str | None:
    """The genuine executable — same PATH minus the shim directory."""
    shim_dir = str(_podium_dir() / "bin")
    path = os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep)
                           if p and os.path.realpath(p) != os.path.realpath(shim_dir))
    return shutil.which(name, path=path)


def main(argv: list[str]) -> int:
    name = argv[1]
    args = argv[2:]
    pdir = _podium_dir()
    pdir.mkdir(parents=True, exist_ok=True)
    depth = int(os.environ.get("PODIUM_PEER_DEPTH", "0"))
    req_id = uuid.uuid4().hex[:12]
    request = {
        "id": req_id, "binary": name, "argv": args,
        "command": " ".join([name, *args])[:500],
        "session": os.environ.get("PODIUM_SESSION_ID", ""),
        "task": os.environ.get("PODIUM_TASK_ID", ""),
        "depth": depth, "outcome": "requested", "ts": int(time.time()),
    }

    if depth >= MAX_DEPTH:
        request["outcome"] = "blocked"
        request["reason"] = f"depth cap ({MAX_DEPTH}) reached"
        _append(pdir, request)
        print(f"Podium: '{name}' not started — peer depth cap ({MAX_DEPTH}) reached. "
              "A brokered harness may not spawn further harnesses.", file=sys.stderr)
        return 126

    _append(pdir, request)
    decision = _await_decision(pdir, req_id)

    if decision.get("allow"):
        target = _real_binary(name)
        if target is None:
            print(f"Podium: approved, but '{name}' is not installed.", file=sys.stderr)
            return 127
        env = dict(os.environ)
        env["PODIUM_PEER_DEPTH"] = str(depth + 1)
        env["PODIUM_PEER_PARENT"] = os.environ.get("PODIUM_SESSION_ID", "")
        os.execve(target, [target, *args], env)

    reason = decision.get("reason") or "not approved by the operator"
    print(f"Podium: '{name}' not started — {reason}.", file=sys.stderr)
    print("The request is recorded in Podium's agent tree.", file=sys.stderr)
    return 126


def _append(pdir: Path, record: dict) -> None:
    with (pdir / "peer.jsonl").open("a") as f:
        f.write(json.dumps(record) + "\n")


def _await_decision(pdir: Path, req_id: str) -> dict:
    path = pdir / "peer-decisions" / f"{req_id}.json"
    deadline = time.time() + WAIT_S
    while time.time() < deadline:
        if path.exists():
            try:
                return json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                pass
        time.sleep(POLL_S)
    return {"allow": False, "reason": "timed out waiting for a decision"}


if __name__ == "__main__":
    sys.exit(main(sys.argv))
