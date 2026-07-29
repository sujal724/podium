# Plan 001 — Stage A: the loop lives

**Status:** Approved for build · **Date:** 2026-07-29 · Companion to `spec.md`.

## Shape

Package `podium/` (ADR-0002 renames the LLD's `orchestrator/`). Console scripts:
`podiumd` (daemon) and `podium` (CLI + TUI). Python 3.12, asyncio. Runtime deps:
`websockets`, `textual`. Dev: `pytest`, `pytest-asyncio`.

```
podium/
├── config.py       # env-driven settings (PODIUM_*), defaults keep single-machine working
├── ids.py          # short ids: new_id("s") → "s_7f3a"
├── protocol.py     # wire frame constructors/validation (LLD §18 subset + blocked frames)
├── sink.py         # async event bus: emit() → subscribers (gateway clients, store, feed)
├── state.py        # StateStore: SQLite WAL, full LLD §19 schema + normalized DAG tables
├── surfaces.py     # decision-44 registry: every v1 surface → stage + live|blocked
├── policy.py       # decision-50 slice: peer-call deny settings + hook config per worktree
├── metering.py     # ledger + QuotaGauge (read-not-estimated; `unavailable` until wired) + backoff
├── workspace.py    # git worktree per task, task branch, diff, merge/reject
├── manager.py      # SessionManager: spawn/write/stop/list/snapshot
├── dispatcher.py   # the spine loop: claim → worktree → spawn → run → review; approve/reject
├── gateway.py      # podiumd: WS server on 127.0.0.1:8765, frame routing, blocked answers
├── work/store.py   # WorkStore: CRUD, deps+cycles, inbox, ready_tasks, atomic claim
├── sessions/       # base.py (Session ABC) · pty.py (PtySession, concrete)
├── workers/        # base.py (Worker ABC, Availability, registry) · claude.py · gemini.py
│                   #   · codex.py (blocked posture) · mock.py (tests/dogfood)
├── tui/app.py      # Textual cockpit: board·session·review·inbox·gauge·feed·blocked panes
└── cli.py          # `podium` entry: daemon|tui|task …|workspace …|project … (thin WS client)
tests/              # unit + integration (mock CLI under a real PTY; temp git repos; temp DBs)
```

## Contracts (deviations from LLD noted)

- **Schema**: LLD §19 verbatim for `runs/sessions/events/metrics/workspaces/projects/tasks/
  task_events/tms_links/resources/quota_ledger`, **plus** `task_deps(from_task,to_task,kind,
  origin)` and `project_relations(from_project,to_project,kind)` — the DAG normalized into
  tables instead of a `tasks.deps` JSON column, because readiness/unblock-count/cycle checks
  are relational queries (WORKFLOW §2.1 defines edges with kind+origin; V1 says "WORKFLOW §2
  schema verbatim"). `tasks.deps` JSON is dropped; everything else keeps LLD names.
- **Statuses**: the full WORKFLOW §3 set now (`proposed|backlog|ready|assigned|running|
  verifying|review|done|blocked|parked|discarded`); `verifying` is passed through instantly
  (no verifier yet) and recorded as an event, so semantics never change later — the state
  exists, the verifier just isn't gating it (decision 44).
- **Wire protocol**: LLD §18 subset live: `brain.send`(stub ack), `agent.send`, `spawn`,
  `answer`(PTY write), `stop`, `resize`, `list`, `work.create/update/list`, `task.assign/
  claim/approve/reject/edit/inbox`, plus **added frames** `review.approve {task_id}` /
  `review.reject {task_id, feedback}` and `task.run {task_id}` (manual dispatch — the
  autopilot is Stage C; §18 predates the review pane, addition recorded here). All other
  §18 frames answer `{"type":"blocked","surface":…,"stage":…}` from the surface registry.
- **Quota gauge** (ADR-0005): `QuotaReader` ABC with per-worker readers; **no reader is
  wired in this iteration**, so every gauge renders `unavailable`. The ledger still records
  every session for attribution/history. Backoff: adapter-specific rate-limit regexes over
  session output flip the worker to `limited(until?)`; `may_dispatch` honors it.
- **Peer-call deny** (decision 50): Claude sessions get a generated `.podium/settings.json`
  (permission deny rules for `claude`/`gemini`/`codex` invocations + hooks appending
  PreToolUse/SubagentStop/Stop JSON to `.podium/hooks.jsonl`) passed via `--settings`;
  Gemini/Codex get the deny expressed in the task preamble until their policy surfaces are
  wired (recorded as a registered increment, not silently skipped). The daemon tails
  `hooks.jsonl` into `events` + the feed.
- **Sessions**: only `kind="pty"` is concrete (LLD §2.2); `sdk|headless|api|codex_rpc|
  gemini_acp` are registered blocked surfaces.
- **Approve/merge**: approve = `git merge --no-ff task/<id>` into the project base branch
  in the *primary* checkout path of the project repo (never `main` of a repo unless that
  *is* the configured base), then worktree removed, branch deleted. Reject = feedback →
  `task_events` + task description addendum, task → `ready`, worktree removed, branch kept.
- **Auto-resume seed**: on boot, `assigned|running` tasks → `ready` (+event), open sessions
  → `interrupted` events. Resume/fork of live sessions is a later increment (LLD §21).

## Risks

- **PTY rendering fidelity in Textual** — raw ANSI is stored/streamed verbatim (invariant
  holds at the data layer); the pane renders best-effort. Full emulation is a follow-up.
- **Claude/Gemini CLI flag drift** — adapters isolate argv construction; capability probing
  (LLD §5) lands Stage C, so adapters pin to current stable flags and fail with hints.
- **Merge conflicts on approve** — surfaced as a `blocked` task + event with the git error;
  never auto-resolved.
- **SQLite concurrency** — WAL + one writer connection per process; atomic claim via
  `UPDATE … WHERE id IN (SELECT … LIMIT 1) RETURNING` in one transaction.

## Task breakdown

Tracked per SDLC in the tracker. This iteration bootstraps Podium's own work store and
seeds it with the remaining Stage A+ backlog (`podium seed-backlog`) — the dogfooding path
SDLC.md anticipates ("it becomes Podium's own work management as soon as that exists").
Verify command for the whole feature: `pytest` (acceptance criteria A1–A7 are test-encoded).
