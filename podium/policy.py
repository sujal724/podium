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


def munge_project_path(path: str) -> str:
    """Claude Code keys ~/.claude/projects/ dirs by the cwd with every
    non-alphanumeric replaced by '-' (observed layout, e.g.
    /home/x/.podium/work → -home-x--podium-work)."""
    import re
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def mirror_session(worktree: str, repo_root: str, claude_session_id: str,
                   projects_base: Path | None = None) -> Path | None:
    """Spec 004: make a Podium worktree session visible in the parent repo's
    `claude --resume` picker by symlinking its transcript into the repo's project
    dir. Returns the link path, or None if the source transcript doesn't exist
    (yet). Resuming from the repo runs with repo cwd — mid-task takeover should go
    through `podium open`, which resumes inside the worktree."""
    base = projects_base or (Path("~/.claude/projects").expanduser())
    src = base / munge_project_path(str(Path(worktree).resolve())) \
        / f"{claude_session_id}.jsonl"
    if not src.exists():
        return None
    dest_dir = base / munge_project_path(str(Path(repo_root).resolve()))
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if not dest.exists():
        dest.symlink_to(src)
    return dest


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
