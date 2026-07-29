# Spec 004 — Claude session visibility + update-interrupt re-queue

**Status:** Approved for build · **Date:** 2026-07-29 · Operator requests from dogfooding
(closes backlog task `t_27bb05`; session-visibility ask from this run).

## Part 1 — update-interrupted tasks re-queue (fix to spec 002)

Observed: the v0.3.1→v0.3.2 self-update stopped the live session while the old event
loop still ran; the dispatcher processed the SIGTERM exit as a normal finish →
"no commits" → `blocked`. Intended semantics: a task interrupted by an update/shutdown
is *interrupted*, exactly like a crash — it stays `running` and the boot auto-resume
re-queues it (decision 23).

Fix: `Dispatcher.interrupt_all(reason)` cancels the per-task runner coroutines (so no
finish-path runs) and logs an `interrupted` task event; the update restart calls it
*before* stopping sessions. Criteria **D1**: a task whose session is killed mid-run by
the update path stays `running`, and `resume_interrupted()` re-queues it to `ready`.

## Part 2 — Podium sessions show up in `claude`

Claude Code scopes its session list per directory and identifies sessions by UUID;
`claude --resume <uuid>` works from any directory (verified empirically). Podium
sessions live in per-task worktrees, so they never appear in the operator's usual
`claude` picker. The operator wants them visible in `claude`, not just in Podium.

Mechanism:
1. **Capture** — Claude hook events (already ingested from `.podium/hooks.jsonl`)
   carry the CLI's `session_id`; the first one seen for a dispatched session is stored
   as `sessions.resume_key` (the schema column reserved for exactly this) and logged.
2. **Mirror** — the session's transcript (`~/.claude/projects/<munged-worktree>/
   <uuid>.jsonl`) is symlinked into the *parent repo's* project dir
   (`~/.claude/projects/<munged-repo>/`), so `claude --resume` **in the repo** lists
   Podium's sessions alongside the operator's own. Toggle: `PODIUM_SESSION_MIRROR`
   (default on). Path munging: any non-alphanumeric → `-` (observed layout).
3. **Surface** — `sessions.list` wire frame + `podium sessions` print each session
   with its task, status, and exact `claude --resume <uuid>` command; `podium open
   <task>` resumes the precise session (falls back to `--continue`).

Caveat (documented, deliberate): resuming from the repo runs with the repo as cwd —
right for post-hoc conversation/review; for *mid-task* takeover use `podium open`,
which resumes inside the isolated worktree.

Criteria **D2**: hook ingestion stores the resume key and creates the mirror symlink
(temp dirs in tests); **D3**: `sessions.list`/`podium sessions` expose id, task,
resume key.
