"""Task detail (spec 012) — everything about one task, in one answer.

The rule: every state states its **cause** and its **next action**. Nothing here is
new information; it is the derivation the operator was otherwise doing by hand —
what stage, since when and why, what base this branch was cut from and why that base,
where approving would merge it, which sessions ran, and whether the next run continues
or restarts.
"""

import json
import subprocess
from pathlib import Path

from podium.state import now


def _git(repo: str, *args: str) -> str | None:
    proc = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else None


BLOCKED_HINTS = {
    "no commits": "the worker exited without committing — resume it, or reject with "
                  "guidance",
    "126": "a peer call was refused or capped — approve it next time, or tell the "
           "worker to avoid it",
    "merge failed": "resolve the conflict on the task branch, then approve again",
    "rate-limited": "the worker is in quota backoff — it will dispatch when the "
                    "window reopens",
}


def blocked_hint(reason: str) -> str:
    low = (reason or "").lower()
    for needle, hint in BLOCKED_HINTS.items():
        if needle in low:
            return hint
    return "inspect the session transcript, then resume or reject with feedback"


def build(task_id: str, work, dispatcher, state) -> dict:
    task = work.get_task(task_id)
    out: dict = {"type": "task.detail", "task": task.to_dict()}

    # --- where it sits ---------------------------------------------------
    crumbs, node = [], task
    while node.parent_id:
        node = work.get_task(node.parent_id)
        crumbs.insert(0, {"id": node.id, "title": node.title, "status": node.status})
    proj_rows = state.query("SELECT * FROM projects WHERE id=?", (task.project_id,))
    proj = dict(proj_rows[0]) if proj_rows else {}
    ws_rows = state.query("SELECT * FROM workspaces WHERE id=?",
                          (proj.get("workspace_id"),)) if proj else []
    out["breadcrumb"] = {
        "workspace": dict(ws_rows[0]) if ws_rows else None,
        "project": {k: proj.get(k) for k in ("id", "name", "base_branch", "repo_root")},
        "parents": crumbs,
    }
    out["subtasks"] = [dict(r) for r in state.query(
        "SELECT id,title,status FROM tasks WHERE parent_id=? ORDER BY priority",
        (task_id,))]
    out["blocked_by"] = [dict(r) for r in state.query(
        "SELECT t.id,t.title,t.status FROM task_deps d JOIN tasks t ON t.id=d.to_task"
        " WHERE d.from_task=? AND d.kind='blocked-by'", (task_id,))]
    out["blocks"] = [dict(r) for r in state.query(
        "SELECT t.id,t.title,t.status FROM task_deps d JOIN tasks t ON t.id=d.from_task"
        " WHERE d.to_task=? AND d.kind='blocked-by'", (task_id,))]

    # --- what stage, since when, and why ---------------------------------
    events = [dict(r) for r in state.query(
        "SELECT ts,type,actor,data FROM task_events WHERE task_id=? ORDER BY id",
        (task_id,))]
    out["timeline"] = [
        {"ts": e["ts"], "type": e["type"], "actor": e["actor"],
         "data": json.loads(e["data"] or "{}")} for e in events]
    last_status = next((e for e in reversed(out["timeline"])
                        if e["type"].startswith("status:")
                        or e["type"] in ("created", "approved")), None)
    out["since"] = now() - (last_status["ts"] if last_status else now())
    reason = ""
    if task.status == "blocked":
        blocked = next((e for e in reversed(out["timeline"])
                        if e["type"] == "status:blocked"), None)
        reason = (blocked or {}).get("data", {}).get("error", "")
    out["reason"] = reason
    out["hint"] = blocked_hint(reason) if reason else ""
    out["feedback"] = work.feedback(task_id)

    # --- the base, spelled out -------------------------------------------
    repo = proj.get("repo_root")
    branch = dispatcher.workspaces.branch(task_id)
    base = dispatcher.base_ref_for(task, proj) if repo else None
    if task.base_ref:
        why = "explicit base_ref on this task"
    elif task.parent_id:
        why = f"parent task {task.parent_id}'s branch (subtasks stack on their parent)"
    else:
        why = f"the project's base branch ({proj.get('base_branch')})"
    git_info = {"branch": branch, "base_ref": base, "base_reason": why,
                "merge_target": base,
                "worktree": task.worktree,
                "worktree_exists": bool(task.worktree
                                        and Path(task.worktree).is_dir())}
    if repo and base:
        ahead = _git(repo, "rev-list", "--count", f"{base}..{branch}")
        git_info["commits_ahead"] = int(ahead) if ahead and ahead.isdigit() else 0
        git_info["diffstat"] = (_git(repo, "diff", "--shortstat", f"{base}...{branch}")
                                or "")
    out["git"] = git_info

    # --- who ran it, and what the next run will do -----------------------
    worker = task.assignee or dispatcher.default_worker
    sessions = [dict(r) for r in state.query(
        "SELECT id,worker,kind,status,resume_key,created_at,ended_at FROM sessions"
        " WHERE task_id=? ORDER BY created_at DESC", (task_id,))]
    out["sessions"] = sessions
    key = dispatcher.resume_key_for(task_id, worker) if worker else None
    out["resume"] = {
        "will_resume": bool(key), "resume_key": key,
        "explanation": (f"the next run CONTINUES session {key[:8]}… (it was "
                        "interrupted)") if key else
                       ("the next run STARTS FRESH — a rejection means your feedback "
                        "reshapes the work"
                        if any(e["type"] == "review.rejected" for e in out["timeline"])
                        else "the next run starts fresh (no interrupted session to "
                             "continue)"),
    }
    out["autonomy"] = {
        "mode": dispatcher.autonomy_for(task, proj) if proj else None,
        "source": ("task override" if task.autonomy else
                   "project default" if proj.get("autonomy") else "daemon default"),
    }
    out["agents"] = dispatcher.agents.tree(task_id)

    # --- what you can do now, and why not --------------------------------
    unfinished = [r["id"] for r in state.query(
        "WITH RECURSIVE kids(id) AS (SELECT id FROM tasks WHERE parent_id=:root"
        " UNION ALL SELECT t.id FROM tasks t JOIN kids k ON t.parent_id=k.id)"
        " SELECT k.id FROM kids k JOIN tasks t ON t.id=k.id"
        " WHERE t.status NOT IN ('done','discarded')", {"root": task_id})]
    unmet = [d for d in out["blocked_by"] if d["status"] not in ("done", "discarded")]
    actions = {}
    actions["run"] = (
        (True, "") if task.status == "ready" and not unmet else
        (False, f"waiting on {', '.join(d['id'] for d in unmet)}") if unmet else
        (False, f"status is {task.status}, not ready"))
    actions["approve"] = (
        (True, f"merges {branch} → {base}") if task.status == "review" and not unfinished
        else (False, f"{len(unfinished)} unfinished subtask(s): "
                     f"{', '.join(unfinished)}") if unfinished
        else (False, f"status is {task.status}, not review"))
    actions["reject"] = ((True, "") if task.status == "review"
                         else (False, f"status is {task.status}, not review"))
    actions["requeue"] = ((True, "back to ready") if task.status == "blocked"
                          else (False, "only blocked tasks need re-queueing"))
    out["actions"] = {k: {"enabled": v[0], "reason": v[1]} for k, v in actions.items()}
    return out
