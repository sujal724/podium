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
import sys
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


def write_peer_shims(worktree: str) -> Path:
    """Decision-50 enforcement that no permission setting can switch off.

    Verified empirically: under `--dangerously-skip-permissions` the CLI ignores
    hook denials, so a policy-layer deny is not enough. These shims sit first on the
    worker's PATH, so invoking another harness fails at the OS level in *every*
    autonomy mode. The hook (guard.py) stays as defense in depth + logging.
    """
    bin_dir = Path(worktree) / ".podium" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in PEER_BINARIES:
        shim = bin_dir / name
        shim.write_text(
            "#!/bin/sh\n"
            f'echo "Blocked by Podium policy: this session may not invoke \'{name}\'."'
            " >&2\n"
            'echo "Direct harness-to-harness calls are denied in every autonomy mode'
            ' (decision 50); the orchestrator brokers peer calls when that surface'
            ' ships." >&2\n'
            "exit 126\n"
        )
        shim.chmod(0o755)
    return bin_dir


def worker_env(worktree: str, base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for a worker session: peer shims first on PATH, API keys stripped
    (subscription auth only), and inherited child-session markers removed — those
    silently disable the CLI's transcript saving, which breaks session visibility
    and resume (observed in a real run)."""
    import os
    env = dict(os.environ if base is None else base)
    env["PATH"] = f"{write_peer_shims(worktree)}:{env.get('PATH', '')}"
    for key in list(env):
        if key.startswith("CLAUDE_CODE_") or key in (
                "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY",
                "GOOGLE_API_KEY", "CLAUDECODE"):
            env.pop(key, None)
    return env


# Autonomy modes for a worker session (the Stage-A slice of the autonomy spectrum,
# decision 7). The peer-call deny (decision 50) applies in EVERY mode — deny rules
# take precedence over allow rules, so autonomy never widens into other harnesses.
AUTONOMY_MODES = {
    "supervised": "edits auto-accepted; commands ask (Podium surfaces the dialog)",
    "autonomous": "edits and tools auto-accepted; only denied actions stop it",
    "bypass": "asks nothing at all (--dangerously-skip-permissions); the peer-call "
              "guard still blocks other harnesses",
}
DEFAULT_AUTONOMY = "supervised"

# Tools an autonomous session may use without asking (deny still wins).
AUTONOMOUS_ALLOW = ["Bash", "Read", "Edit", "Write", "Glob", "Grep", "WebFetch",
                    "WebSearch", "Task", "TodoWrite", "NotebookEdit"]


def write_claude_settings(worktree: str, autonomy: str = DEFAULT_AUTONOMY) -> Path:
    """Generate `.podium/settings.json` for a Claude worker session in `worktree`.
    Returned path is passed to the CLI via `--settings`."""
    pdir = Path(worktree) / ".podium"
    pdir.mkdir(parents=True, exist_ok=True)
    log = hooks_path(worktree)
    append_cmd = f"cat >> {json.dumps(str(log))}"
    # PreToolUse runs the guard: it logs AND enforces the peer-call deny. Hooks run
    # in every autonomy mode, so the decision-50 policy survives even bypass mode,
    # where permission rules are skipped entirely.
    guard_cmd = f"{json.dumps(sys.executable)} -m podium.guard {json.dumps(str(log))}"
    settings = {
        "permissions": {
            "deny": [f"Bash({b} *)" for b in PEER_BINARIES]
                    + [f"Bash({b})" for b in PEER_BINARIES],
            "allow": AUTONOMOUS_ALLOW if autonomy in ("autonomous", "bypass") else [],
        },
        "hooks": {
            event: [{"hooks": [{"type": "command",
                                "command": guard_cmd if event == "PreToolUse"
                                else append_cmd}]}]
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
