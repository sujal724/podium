"""Dispatcher — the spine loop (V1 §1).

claim → worktree → spawn worker → stream live (metered, rate-limit-scanned, hook-file
tailed) → session exits → diff collected → task enters `review` → the operator approves
(merge) or rejects (feedback + re-queue). `verifying` is passed through with an event —
the state exists now, the verifier gates it in Stage C (decision 44: semantics never
change, the gate just arrives).

Stage A dispatch is operator-triggered (`task.run`) or `run_next()`; the autopilot tick
over the DAG is a Stage C surface.
"""

import asyncio
import json
from pathlib import Path

from podium import policy, protocol
from podium.agents import AgentTree
from podium.config import CONFIG
from podium.manager import SessionManager
from podium.metering import Meter
from podium.sink import Sink
from podium.state import StateStore, now
from podium.work.store import WorkStore
from podium.workspace import GitError, WorkspaceManager

HOOK_POLL_S = 0.5


class DispatchError(RuntimeError):
    pass


class Dispatcher:
    def __init__(self, work: WorkStore, manager: SessionManager,
                 workspaces: WorkspaceManager, meter: Meter, sink: Sink,
                 state: StateStore, default_worker: str = "claude") -> None:
        self.work = work
        self.manager = manager
        self.workspaces = workspaces
        self.meter = meter
        self.sink = sink
        self.state = state
        self.default_worker = default_worker
        self.running: dict[str, asyncio.Task] = {}   # task_id → runner
        self._linked: set[str] = set()               # sessions with resume_key stored
        self.winsize = None                          # () -> (rows, cols) | None
        self.agents = AgentTree(state, sink)         # spec 007: who is working
        self.pending_peers: dict[str, tuple] = {}    # spec 009: awaiting you

    # --- dispatch -----------------------------------------------------------

    async def run_task(self, task_id: str, worker: str | None = None) -> None:
        """Claim a specific ready task and run it end-to-end (until `review`)."""
        worker = worker or self._worker_for(task_id)
        if not self.meter.gauge.may_dispatch(worker):
            raise DispatchError(f"worker {worker} is rate-limited (backoff active)")
        task = self.work.claim(worker, task_id)
        if task is None:
            raise DispatchError(f"task {task_id} is not claimable (not ready, or deps unmet)")
        runner = asyncio.create_task(self._run(task.id, worker))
        self.running[task.id] = runner
        runner.add_done_callback(lambda _: self.running.pop(task.id, None))

    async def run_next(self, worker: str | None = None) -> str | None:
        """Claim the best ready task, if any. Returns its id."""
        worker = worker or self.default_worker
        if not self.meter.gauge.may_dispatch(worker):
            return None
        task = self.work.claim(worker)
        if task is None:
            return None
        runner = asyncio.create_task(self._run(task.id, worker))
        self.running[task.id] = runner
        runner.add_done_callback(lambda _: self.running.pop(task.id, None))
        return task.id

    def _worker_for(self, task_id: str) -> str:
        t = self.work.get_task(task_id)
        if t.assignee and not t.assignee.startswith("human"):
            return t.assignee
        return self.default_worker

    def _project(self, task) -> dict:
        rows = self.state.query("SELECT * FROM projects WHERE id=?", (task.project_id,))
        if not rows or not rows[0]["repo_root"]:
            raise DispatchError(
                f"task {task.id} has no project repo to run in "
                "(set project.repo_root)")
        return dict(rows[0])

    # --- the loop body ------------------------------------------------------

    async def _run(self, task_id: str, worker: str) -> None:
        task = self.work.get_task(task_id)
        started = now()
        try:
            proj = self._project(task)
            base_ref = self.base_ref_for(task, proj)
            wt = self.workspaces.create(proj["repo_root"], task_id, base_ref)
            if base_ref != proj["base_branch"]:
                self.sink.emit(protocol.narration(
                    task_id, f"branching off {base_ref} (parent task's branch)"))
            self.work.update_task(task_id, actor="system", worktree=wt)
            prompt = self._prompt(task)
            self.work.set_status(task_id, "running", data={"worker": worker})
            ws = self.winsize() if self.winsize else None
            resume_key = self.resume_key_for(task_id, worker)
            if resume_key:
                self.sink.emit(protocol.narration(
                    task_id, f"resuming {worker} session {resume_key[:8]}… "
                             "(continues where it stopped)"))
            autonomy = self.autonomy_for(task, proj)
            sess = await self.manager.spawn(worker, prompt, cwd=wt, task_id=task_id,
                                            rows=ws[0] if ws else None,
                                            cols=ws[1] if ws else None,
                                            resume_key=resume_key, autonomy=autonomy)
            self.sink.emit(protocol.narration(
                task_id, f"[{sess.id}] {worker} · mode: {autonomy} "
                         f"({policy.AUTONOMY_MODES.get(autonomy, '')})"))
            self.agents.open_session(sess.id, task_id, worker,
                                     detail=task.title,
                                     data={"autonomy": autonomy, "cwd": wt})
            scan_q = self.sink.subscribe()
            hook_offset = peer_offset = 0
            try:
                waiter = asyncio.create_task(sess.wait())
                while not waiter.done():
                    hook_offset = self._ingest_hooks(wt, sess.id, hook_offset,
                                                     repo_root=proj["repo_root"])
                    peer_offset = self._ingest_peers(wt, sess.id, peer_offset,
                                                     autonomy)
                    try:
                        frame = await asyncio.wait_for(scan_q.get(), timeout=HOOK_POLL_S)
                    except TimeoutError:
                        continue
                    if (frame.get("type") == "output"
                            and frame.get("session_id") == sess.id):
                        self.meter.scan_output(worker, sess.id, frame["text"])
                exit_code = waiter.result()
            finally:
                self.sink.unsubscribe(scan_q)
                self._ingest_hooks(wt, sess.id, hook_offset,
                                   repo_root=proj["repo_root"])
                self._ingest_peers(wt, sess.id, peer_offset, autonomy)
                self.agents.close_session(
                    sess.id, "done" if sess.exit_code == 0 else "error")
            self.manager.mark_ended(sess.id)
            self.meter.record_session(sess.id, task_id, worker, sess.kind,
                                      started, now(),
                                      "ok" if exit_code == 0 else f"exit:{exit_code}")
            await self._finish(task_id, proj, exit_code)
        except Exception as e:
            self.work.set_status(task_id, "blocked", data={"error": str(e)})
            self.sink.emit(protocol.error(f"task {task_id} failed to run", str(e)))

    def base_ref_for(self, task, proj: dict) -> str:
        """Branches mirror the task tree: an explicit `base_ref` wins, else a
        subtask builds on its PARENT's branch (created if the parent hasn't run
        yet), else the project's base branch. So base → b → subtask-of-b stacks."""
        if task.base_ref:
            return task.base_ref
        if task.parent_id:
            parent = self.work.get_task(task.parent_id)
            parent_base = self.base_ref_for(parent, proj)
            return self.workspaces.ensure_branch(proj["repo_root"], parent.id,
                                                 parent_base)
        return proj["base_branch"]

    def autonomy_for(self, task, proj: dict) -> str:
        """Task overrides project overrides the daemon default (decision 7's
        spectrum, per-scope). Unknown values fall back to supervised — the safe end."""
        for candidate in (getattr(task, "autonomy", None), proj.get("autonomy"),
                          CONFIG.autonomy):
            if candidate in policy.AUTONOMY_MODES:
                return candidate
        return policy.DEFAULT_AUTONOMY

    def resume_key_for(self, task_id: str, worker: str) -> str | None:
        """The worker session id to continue for this task, if its last session was
        interrupted (update/restart/crash) rather than finished. A rejected task
        starts fresh — the operator's feedback should reshape the work, not append
        to a conversation that already went the wrong way (spec 005)."""
        rows = self.state.query(
            "SELECT resume_key FROM sessions WHERE task_id=? AND worker=?"
            " AND resume_key IS NOT NULL ORDER BY created_at DESC LIMIT 1",
            (task_id, worker))
        if not rows:
            return None
        events = self.state.query(
            "SELECT type FROM task_events WHERE task_id=? AND type IN"
            " ('interrupted','review.rejected') ORDER BY id DESC LIMIT 1", (task_id,))
        # ordered by rowid, not ts: two events in the same second must still
        # resolve to the later one
        if events and events[0]["type"] == "interrupted":
            return rows[0]["resume_key"]
        return None

    def _prompt(self, task) -> str:
        parts = [policy.PREAMBLE_DENY, "", f"Task: {task.title}"]
        if task.description:
            parts += ["", task.description]
        feedback = self.work.feedback(task.id)
        if feedback:
            parts += ["", "Previous review feedback (address it):"]
            parts += [f"- {f.get('feedback', '')}" for f in feedback]
        parts += ["", "Work only in this directory. Commit your changes when done, "
                      "then exit."]
        return "\n".join(parts)

    def _ingest_hooks(self, worktree: str, session_id: str, offset: int,
                      repo_root: str | None = None) -> int:
        events, new_offset = policy.read_hook_events(worktree, offset)
        for ev in events:
            self.state.log_event(session_id, "hook", ev)
            name = ev.get("hook_event_name", "hook")
            tool = ev.get("tool_name", "")
            self.sink.emit(protocol.narration(
                None, f"[{session_id}] {name}{f' {tool}' if tool else ''}"))
            # subagent lifecycle → the agent tree (spec 007)
            if name == "PreToolUse" and tool == "Task":
                ti = ev.get("tool_input") or {}
                self.agents.open_subagent(
                    session_id,
                    ti.get("subagent_type") or ti.get("description") or "subagent",
                    (ti.get("description") or ti.get("prompt") or "")[:300],
                    data={"tool_input": ti}, key=ev.get("tool_use_id"))
            elif name == "PostToolUse" and tool == "Task":
                # correlated close: subagents run in parallel, so pair by id
                self.agents.close_subagent(session_id, ev.get("tool_use_id"))
            elif name == "SubagentStop":
                self.agents.close_subagent(session_id, ev.get("tool_use_id"))
            claude_sid = ev.get("session_id")
            if claude_sid and session_id not in self._linked:
                self._link_claude_session(session_id, claude_sid, worktree, repo_root)
        return new_offset

    def _ingest_peers(self, worktree: str, session_id: str, offset: int,
                      autonomy: str = "supervised") -> int:
        """Peer-harness calls reported by the shims. Recorded whatever the outcome
        (bypass can never make one invisible, spec 007) — and `requested` ones are
        DECIDED here by the session's autonomy mode (spec 009): autonomous/bypass
        approve; supervised asks the operator through the same dialog as any other
        approval. Podium can launch anything; the mode decides whether it asks."""
        events, new_offset = policy.read_peer_events(worktree, offset)
        for ev in events:
            sid = ev.get("session") or session_id
            outcome = ev.get("outcome", "blocked")
            if outcome != "requested":
                self.agents.record_peer(sid, ev.get("binary", "?"),
                                        ev.get("command", ""), outcome)
                continue
            if autonomy in ("autonomous", "bypass"):
                self._decide_peer(worktree, ev, sid, True,
                                  f"auto-approved in {autonomy} mode")
            else:
                self.pending_peers[ev["id"]] = (worktree, ev, sid)
                self.sink.emit(protocol.question(sid, {
                    "id": f"peer:{ev['id']}", "kind": "choice",
                    "q": f"This session wants to run another harness:\n\n"
                         f"  {ev.get('command', '')}\n\n"
                         "Approve? It will run as a tracked child session.",
                    "choices": ["Approve — run it", "Deny"],
                }))
        return new_offset

    def _decide_peer(self, worktree: str, ev: dict, session_id: str, allow: bool,
                     reason: str) -> None:
        """Write the decision the waiting shim is polling for, and record the node."""
        ddir = Path(worktree) / ".podium" / "peer-decisions"
        ddir.mkdir(parents=True, exist_ok=True)
        (ddir / f"{ev['id']}.json").write_text(
            json.dumps({"allow": allow, "reason": reason}))
        self.agents.record_peer(session_id, ev.get("binary", "?"),
                                ev.get("command", ""),
                                "allowed" if allow else "blocked")
        self.sink.emit(protocol.narration(
            ev.get("task") or None,
            f"peer call {'approved' if allow else 'denied'} ({reason}): "
            f"{ev.get('command', '')[:80]}"))

    def answer_peer(self, request_id: str, allow: bool,
                    actor: str = "human") -> bool:
        """Operator's answer to a supervised peer request."""
        pending = self.pending_peers.pop(request_id, None)
        if pending is None:
            return False
        worktree, ev, sid = pending
        self._decide_peer(worktree, ev, sid, allow, f"{actor} decision")
        return True

    def _link_claude_session(self, session_id: str, claude_sid: str,
                             worktree: str, repo_root: str | None) -> None:
        """Spec 004: record the worker CLI's own session id (resume from anywhere
        with `claude --resume <id>`) and mirror the transcript into the parent
        repo's claude project dir so it lists in the operator's usual picker."""
        self._linked.add(session_id)
        self.state.execute("UPDATE sessions SET resume_key=? WHERE id=?",
                           (claude_sid, session_id))
        self.sink.emit(protocol.narration(
            None, f"[{session_id}] claude session {claude_sid[:8]}… "
                  f"(claude --resume {claude_sid})"))
        if repo_root and CONFIG.session_mirror:
            try:
                policy.mirror_session(worktree, repo_root, claude_sid)
            except OSError as e:
                self.state.log_event(session_id, "mirror.failed", {"error": str(e)})

    async def interrupt_all(self, reason: str = "shutdown") -> list[str]:
        """Cancel every in-flight runner WITHOUT running its finish path, so the
        tasks stay `running` and boot auto-resume re-queues them (spec 004 Part 1 —
        an update/shutdown interrupt is a crash, not a completion)."""
        interrupted = []
        for task_id, runner in list(self.running.items()):
            runner.cancel()
            self.work._log(task_id, "interrupted", "system", {"reason": reason})
            interrupted.append(task_id)
        if self.running:
            await asyncio.gather(*self.running.values(), return_exceptions=True)
        return interrupted

    async def _finish(self, task_id: str, proj: dict, exit_code: int) -> None:
        # `verifying` exists now; the verifier arrives in Stage C and will gate here.
        self.work.set_status(task_id, "verifying",
                             data={"verifier": "not wired (Stage C surface)"})
        base = self.base_ref_for(self.work.get_task(task_id), proj)
        if not self.workspaces.has_commits(proj["repo_root"], task_id, base):
            self.work.set_status(
                task_id, "blocked",
                data={"error": f"session exited ({exit_code}) with no commits on "
                               f"{self.workspaces.branch(task_id)}"})
            return
        diff = self.workspaces.diff(proj["repo_root"], task_id, base)
        self.work.set_status(task_id, "review", data={"exit_code": exit_code})
        self.sink.emit(protocol.review_ready(
            task_id, diff, self.workspaces.branch(task_id)))
        self.sink.emit(protocol.narration(
            task_id, f"✅ ready for review — approving merges "
                     f"{self.workspaces.branch(task_id)} → {base}"))

    # --- the review gate (decision 45) --------------------------------------

    async def approve(self, task_id: str, actor: str = "human") -> None:
        task = self.work.get_task(task_id)
        if task.status != "review":
            raise DispatchError(f"task {task_id} is not in review (status={task.status})")
        proj = self._project(task)
        # Children are stacked ON this task's branch and approve deletes it — so a
        # parent cannot land while any child is unfinished (spec 008). This is also
        # the work model: a parent is its decomposition; it is done when its parts are.
        unfinished = [r["id"] for r in self.state.query(
            "WITH RECURSIVE kids(id) AS ("
            "  SELECT id FROM tasks WHERE parent_id=:root"
            "  UNION ALL SELECT t.id FROM tasks t JOIN kids k ON t.parent_id=k.id)"
            " SELECT k.id FROM kids k JOIN tasks t ON t.id=k.id"
            " WHERE t.status NOT IN ('done','discarded')", {"root": task_id})]
        if unfinished:
            raise DispatchError(
                f"task {task_id} has unfinished subtasks ({', '.join(unfinished)}); "
                "they branch off it, so they must land (or be discarded) first")
        base = self.base_ref_for(task, proj)
        try:
            self.workspaces.approve(proj["repo_root"], task_id, base)
        except GitError as e:
            self.work.set_status(task_id, "blocked", actor=actor,
                                 data={"error": f"merge failed: {e}",
                                       "next": "resolve the conflict on "
                                               f"{self.workspaces.branch(task_id)}, "
                                               "then approve again"})
            raise
        self.work.update_task(task_id, actor=actor, worktree=None, status="done")
        self.sink.emit(protocol.narration(
            task_id, f"approved by {actor} — merged into {base}"))
        # Siblings stacked on the same base now sit on an older commit: their diffs
        # stay correct (three-dot), but they should pick the change up.
        if task.parent_id:
            for r in self.state.query(
                    "SELECT id FROM tasks WHERE parent_id=? AND id!=? AND status IN"
                    " ('ready','running','review','blocked')",
                    (task.parent_id, task_id)):
                self.sink.emit(protocol.narration(
                    r["id"], f"base advanced: sibling {task_id} landed on {base} — "
                             "re-run or merge the base in to pick it up"))

    async def approve_via_pr(self, task_id: str, actor: str = "human",
                             title: str | None = None) -> dict:
        """Approve WITHOUT merging locally: push the task branch and open a PR
        against its base, then mark the task done.

        For a protected base branch a local merge is a trap — it succeeds locally,
        marks the task done, deletes the branch, and only fails later at push time
        with nothing left to open a PR from. This route never writes to the base.
        """
        task = self.work.get_task(task_id)
        if task.status != "review":
            raise DispatchError(f"task {task_id} is not in review "
                                f"(status={task.status})")
        unfinished = [r["id"] for r in self.state.query(
            "WITH RECURSIVE kids(id) AS (SELECT id FROM tasks WHERE parent_id=:root"
            " UNION ALL SELECT t.id FROM tasks t JOIN kids k ON t.parent_id=k.id)"
            " SELECT k.id FROM kids k JOIN tasks t ON t.id=k.id"
            " WHERE t.status NOT IN ('done','discarded')", {"root": task_id})]
        if unfinished:
            raise DispatchError(
                f"task {task_id} has unfinished subtasks ({', '.join(unfinished)})")
        proj = self._project(task)
        repo, branch = proj["repo_root"], self.workspaces.branch(task_id)
        base = self.base_ref_for(task, proj)
        import subprocess
        push = subprocess.run(["git", "-C", repo, "push", "-u", "origin", branch],
                              capture_output=True, text=True)
        if push.returncode != 0:
            raise DispatchError(f"push failed: {push.stderr.strip()[-300:]}")
        pr = subprocess.run(
            ["gh", "pr", "create", "--base", base, "--head", branch,
             "--title", title or task.title,
             "--body", f"{task.description}\n\n---\nPodium task `{task_id}` — "
                       f"approved by {actor}; merging is the PR's job, so the base "
                       f"branch is never written to locally."],
            cwd=repo, capture_output=True, text=True)
        url = pr.stdout.strip().splitlines()[-1] if pr.returncode == 0 else ""
        if pr.returncode != 0 and "already exists" not in pr.stderr:
            raise DispatchError(f"gh pr create failed: {pr.stderr.strip()[-300:]}")
        self.work._log(task_id, "approved.pr", actor, {"branch": branch,
                                                       "base": base, "url": url})
        self.work.update_task(task_id, actor=actor, worktree=None, status="done")
        self.workspaces.remove(repo, task_id)      # worktree goes; BRANCH STAYS
        self.sink.emit(protocol.narration(
            task_id, f"approved by {actor} — PR opened against {base}: {url or branch}"
                     " (branch kept; the PR does the merging)"))
        return {"type": "review.pr", "task_id": task_id, "branch": branch,
                "base": base, "url": url}

    async def reject(self, task_id: str, feedback: str, actor: str = "human") -> None:
        task = self.work.get_task(task_id)
        if task.status != "review":
            raise DispatchError(f"task {task_id} is not in review (status={task.status})")
        proj = self._project(task)
        self.work._log(task_id, "review.rejected", actor, {"feedback": feedback})
        self.workspaces.reject(proj["repo_root"], task_id)
        self.work.update_task(task_id, actor=actor, worktree=None, status="ready")
        self.sink.emit(protocol.narration(
            task_id, f"rejected by {actor}; re-queued with feedback"))

    # --- durability seed (decision 23) --------------------------------------

    async def adopt_live_sessions(self) -> list[str]:
        """Boot step 1 (spec 011): workers run in tmux and OUTLIVE the daemon, so
        adopt the ones still running instead of re-queuing their tasks — re-queuing
        would start a second worker on the same task while the first kept going."""
        from podium.sessions.tmux import TmuxSession, live_sessions
        alive = live_sessions()
        adopted = []
        for r in self.state.query(
                "SELECT * FROM sessions WHERE ended_at IS NULL AND status NOT IN"
                " ('exited','error','interrupted') ORDER BY created_at"):
            name = f"podium-{r['id']}"
            if name not in alive or not r["task_id"]:
                continue
            try:
                sess = TmuxSession(r["id"], r["worker"], r["cwd"], self.sink, [])
                sess.task_id = r["task_id"]
                await sess.adopt()
            except Exception as e:
                self.state.log_event(r["id"], "adopt.failed", {"error": str(e)})
                continue
            self.manager.sessions[sess.id] = sess
            self.work.set_status(r["task_id"], "running", actor="system",
                                 data={"reason": "adopted a worker that survived "
                                                 "the daemon"})
            runner = asyncio.create_task(self._monitor_adopted(r["task_id"], sess))
            self.running[r["task_id"]] = runner
            runner.add_done_callback(
                lambda _, tid=r["task_id"]: self.running.pop(tid, None))
            self.sink.emit(protocol.narration(
                r["task_id"], f"adopted live session {sess.id} — it kept running "
                              "while the daemon was down"))
            adopted.append(r["task_id"])
        return adopted

    async def _monitor_adopted(self, task_id: str, sess) -> None:
        """Watch an adopted worker to completion and run the normal finish path, so
        an adopted task still reaches review exactly like a freshly dispatched one."""
        try:
            proj = self._project(self.work.get_task(task_id))
            exit_code = await sess.wait()
            self.manager.mark_ended(sess.id)
            self.agents.close_session(sess.id,
                                      "done" if exit_code == 0 else "error")
            await self._finish(task_id, proj, exit_code)
        except Exception as e:
            self.work.set_status(task_id, "blocked", data={"error": str(e)})

    def resume_interrupted(self) -> list[str]:
        """Boot step 2: whatever did NOT survive is re-queued. `adopt_live_sessions`
        runs first, so a task with a living worker is already `running` here."""
        requeued = []
        for r in self.state.query(
                "SELECT id FROM tasks WHERE status IN ('assigned','running')"):
            if r["id"] in self.running:          # adopted — leave it alone
                continue
            # Record the interrupt BEFORE the re-queue: resume_key_for reads the
            # latest (interrupted | review.rejected) to choose continue-vs-restart,
            # and without it every retry started the worker from scratch.
            self.work._log(r["id"], "interrupted", "system",
                           {"reason": "daemon restart"})
            self.work.set_status(r["id"], "ready", actor="system",
                                 data={"reason": "daemon restart — re-queued"})
            requeued.append(r["id"])
        # ...but never mark an ADOPTED session interrupted: it is still running.
        live = list(self.manager.sessions)
        placeholders = ",".join("?" * len(live)) or "''"
        self.state.execute(
            "UPDATE sessions SET status='interrupted', ended_at=?"
            " WHERE ended_at IS NULL AND status NOT IN ('exited','error')"
            f" AND id NOT IN ({placeholders})",
            (now(), *live))
        if requeued:
            self.sink.emit(protocol.narration(
                None, f"auto-resume: re-queued {len(requeued)} interrupted task(s)"))
        return requeued
