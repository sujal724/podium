"""Decision-50 Stage-A slice: direct harness→harness calls are denied by worker
permission policy from day one, and native-subagent/tool lifecycle events are ingested
from hooks into the event feed/ledger.

For Claude sessions this is real policy: a generated settings file with permission deny
rules plus hooks that append lifecycle JSON to `.podium/hooks.jsonl` in the worktree
(the daemon tails it). For Gemini/Codex the deny is stated in the task preamble until
their policy surfaces are wired — a registered increment (surfaces.py `peer.deny` note),
not a silent skip. The brokered `peer` MCP tool is a Stage C surface.
"""

import json
from pathlib import Path

PEER_BINARIES = ("claude", "gemini", "codex", "podium", "podiumd")

HOOK_EVENTS = ("PreToolUse", "PostToolUse", "SubagentStop", "Stop")

PREAMBLE_DENY = (
    "Policy: do not invoke other AI coding harnesses or the orchestrator "
    f"({', '.join(PEER_BINARIES)}) from inside this session; such calls are denied "
    "and will be brokered by the orchestrator when that surface ships."
)


def hooks_path(worktree: str) -> Path:
    return Path(worktree) / ".podium" / "hooks.jsonl"


def write_claude_settings(worktree: str) -> Path:
    """Generate `.podium/settings.json` for a Claude worker session in `worktree`.
    Returned path is passed to the CLI via `--settings`."""
    pdir = Path(worktree) / ".podium"
    pdir.mkdir(parents=True, exist_ok=True)
    log = hooks_path(worktree)
    append_cmd = f"cat >> {json.dumps(str(log))}"
    settings = {
        "permissions": {
            "deny": [f"Bash({b} *)" for b in PEER_BINARIES]
                    + [f"Bash({b})" for b in PEER_BINARIES],
        },
        "hooks": {
            event: [{"hooks": [{"type": "command", "command": append_cmd}]}]
            for event in HOOK_EVENTS
        },
    }
    path = pdir / "settings.json"
    path.write_text(json.dumps(settings, indent=2))
    return path


def read_hook_events(worktree: str, offset: int = 0) -> tuple[list[dict], int]:
    """Read hook lifecycle events appended since `offset` bytes. Returns (events,
    new_offset). Malformed lines are skipped (hooks are best-effort telemetry)."""
    path = hooks_path(worktree)
    if not path.exists():
        return [], offset
    events = []
    with path.open("rb") as f:
        f.seek(offset)
        data = f.read()
        new_offset = f.tell()
    for line in data.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events, new_offset
