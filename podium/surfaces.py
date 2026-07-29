"""Decision-44 surface registry: every v1 surface exists from day one; anything not yet
functional is an explicit `blocked` entry with the stage it comes alive in — never a fake,
never a stand-in that changes later. The gateway answers later-stage wire frames from this
table; the TUI renders one pane per blocked surface.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Surface:
    key: str
    title: str
    stage: str          # "A" | "B" | "C" | "D"
    live: bool
    note: str = ""
    frames: tuple[str, ...] = ()   # wire frame types served by this surface


SURFACES: dict[str, Surface] = {
    s.key: s
    for s in [
        # --- live in Stage A ---
        Surface("loop", "Task loop (dispatch → worktree → review)", "A", True),
        Surface("work", "Work management (hierarchy + DAG + inbox)", "A", True),
        Surface("sessions", "Live sessions + takeover (PTY)", "A", True),
        Surface("review", "Review gate (diff + approve/reject)", "A", True),
        Surface("metering", "Metering ledger + backoff", "A", True),
        Surface(
            "quota.read", "Quota gauge (read, not estimated — ADR-0005)", "A", False,
            "Read surfaces (/usage PTY-parse, per-CLI counters) not wired yet; "
            "gauge shows `unavailable`, never an estimate.",
        ),
        Surface(
            "peer.deny", "Peer-call policy-deny + hook ingestion (decision 50)", "A", True,
        ),
        # --- Stage B ---
        Surface(
            "interaction", "Interaction layer (native approvals, uniform prompts)", "B",
            False, "", ("answer.native",),
        ),
        Surface("ingestion", "Resource ingestion (fetch→parse→chunk→index)", "B", False,
                "", ("resource.add",)),
        Surface("tms", "TMS bridge (GitHub Issues / Linear / Jira)", "B", False,
                "", ("tms.sync",)),
        Surface("library", "Library & reading room (+ TTS, video queue)", "B", False, "",
                ("library.browse", "library.search", "library.get", "tts.request",
                 "listen.queue", "path.get")),
        Surface("mobile", "Mobile PWA via authenticated coordinator", "B", False),
        Surface("matrix", "Model matrix (seeded routing)", "B", False),
        Surface("pacing", "Quota pacing controller (admit/defer)", "B", False),
        Surface("bookmarks", "Bookmarks", "B", False,
                "", ("bookmark.add", "bookmark.list", "bookmark.to_task")),
        # --- Stage C ---
        Surface("context", "Context engine (graph + RAG via MCP)", "C", False),
        Surface("verifier", "Independent verifier gating `done`", "C", False),
        Surface("autopilot", "Autonomy spectrum + autopilot over the DAG", "C", False,
                "", ("autonomy.set",)),
        Surface("detectors", "Approval-inbox detectors (auto-proposed tasks)", "C", False),
        Surface("peer.tool", "Brokered `peer` MCP tool + subagent tree pane", "C", False,
                "Direct harness→harness calls are policy-denied from day one; the brokered "
                "tool ships with the MCP intelligence server."),
        Surface("handoff", "Promote/takeover drive-mode handoff", "C", False,
                "", ("session.promote", "session.takeover")),
        Surface("teams", "Agent teams (roles, lead, peer bus)", "C", False,
                "", ("team.create", "team.update", "team.assign", "team.message")),
        Surface("mgmt", "Mission-control aggregation (desktop+phone)", "C", False,
                "", ("mgmt.query", "mgmt.control")),
        Surface("selfmaint", "Self-maintenance (updater/watcher, self-diagnostic)", "C",
                False),
        # --- Stage D ---
        Surface("routing", "Learned routing (contextual bandit)", "D", False),
        Surface(
            "operator-model",
            "What the system believes about me (decision 51)", "D", False,
            "Blocked until the first estimate exists; every estimate will show its "
            "evidence with edit/pin/reset. No surface ever conditions availability on "
            "these estimates (no-ceiling rule).",
        ),
        Surface("learning", "Learning paths + quizzes/SRS + learner ML", "D", False),
        Surface("runtime", "Runtime maintainer (envs, infra, alerts)", "D", False),
    ]
}


def frame_surface(frame_type: str) -> Surface | None:
    """The blocked surface that owns a wire frame type, if any."""
    for s in SURFACES.values():
        if not s.live and frame_type in s.frames:
            return s
    return None


def blocked_surfaces() -> list[Surface]:
    return [s for s in SURFACES.values() if not s.live]
