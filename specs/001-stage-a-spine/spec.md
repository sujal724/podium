# Spec 001 — Stage A: the loop lives

**Status:** Approved for build (first iteration) · **Date:** 2026-07-29
**Sources:** `research/V1.md` (Stage A), `research/LLD.md` §1–§4, §8.2, §9, §18–§21, `research/WORKFLOW.md` §2–§3, ADR-0002, ADR-0004, ADR-0005.

## What

The spine of Podium: *you add a task → the daemon runs it on a real coding CLI in an isolated
git worktree → you watch live and can take over → it commits on a task branch → you approve or
reject with feedback → next task.* Every later stage of v1 lands **through** this loop as
reviewed tasks (dogfooding).

## Why

V1's operator ruling: full scope, staged honestly. Stage A is the dependency root — nothing
else can be built *through* Podium until this loop exists. The gate for this stage is the
**bootstrap test**: Podium executes a task from its own backlog.

## Scope (in)

1. **Daemon** (`podiumd`): Python 3.12 asyncio, WebSocket gateway on `127.0.0.1:8765`
   (localhost-only, no auth — single operator), SQLite (WAL) state store.
2. **Full work schema from day one** (no flat queue): workspaces → projects → tasks →
   subtasks; normalized cross-project dependency DAG + project relations (decision 43);
   resources; origin `human`/`auto` + detector + approval provenance (decision 35); the full
   lifecycle `proposed|backlog|ready|assigned|running|verifying|review|done|blocked|parked|
   discarded` (WORKFLOW §3). Readiness = dependency resolve; pull order = priority +
   unblock-count heuristic. Atomic claim.
3. **Workers**: Claude and Gemini adapters over a concrete **PtySession** (real PTY, live
   bytes, human takeover via the same `write()` path). Codex adapter present but
   `blocked` until installed + logged in. Availability = auth + ToS posture, not just
   on-PATH (subscription/login auth only; API keys never used for coding workers).
4. **Worktree isolation**: one git worktree + `task/<id>` branch per dispatched task;
   approve = merge to the project base branch + clean up; reject = feedback recorded, task
   re-queued with feedback, branch kept.
5. **Review gate** (decision 45): finished work enters `review` with its diff; the operator
   approves/rejects from the TUI (or CLI). Only approval moves work onward; nothing
   auto-merges.
6. **Metering ledger + quota gauge + backoff**: every session logged (worker, task,
   timestamps, outcome) to the ledger. Per ADR-0005 the gauge **reads** quota; until a read
   surface is wired the gauge shows **`unavailable`** — never an estimate, never a fake
   number (decision 44). Rate-limit detection in session output → worker marked `limited`,
   dispatch deferred (backoff).
7. **TUI cockpit** (Textual): board pane (full hierarchy), live session pane (PTY stream +
   takeover input), review pane (diff + approve/reject), approval inbox pane (`proposed`
   tasks), quota gauge, event feed — plus every not-yet-live v1 surface rendered as an
   explicit **`blocked`** pane with its stage (decision 44): operator-model pane (decision
   51), subagent tree (decision 50), library, TMS, autonomy/autopilot, mobile.
8. **Peer-call policy-deny + hook ingestion** (decision 50, Stage A slice): worker sessions
   launch with a permission policy denying direct harness→harness calls (`claude`/`gemini`/
   `codex` invocations from inside a worker); Claude sessions get hook config that appends
   subagent/tool lifecycle events to a per-worktree `hooks.jsonl`, which the daemon ingests
   into the event feed/ledger. The subagent **tree pane** stays `blocked` until Stage C.
9. **Auto-resume seed** (decision 23): on daemon restart, interrupted `assigned`/`running`
   tasks return to `ready` (re-dispatch); interrupted sessions are marked and surfaced.
10. **No-ceiling invariant** (decision 51): the operator-model pane exists (`blocked`) and
    no surface conditions availability on operator estimates — structurally true in this
    iteration because no estimates exist and no renderer reads any.

## Scope (out — later stages, surfaces present as `blocked`)

Interaction layer (native approval callbacks), SDK/headless/ACP session kinds, resource
ingestion, TMS adapters, PWA/mobile, context engine/RAG, verifier, MoE routing, autopilot
loop over the DAG, detectors, library/TTS, teams, coordinator. Per decision 44 each has a
registered surface with an explicit stage label; none is faked.

## Acceptance criteria

- **A1 (bootstrap loop):** with a mock worker, `create task → dispatch → worktree created →
  worker runs and commits on `task/<id>` → task reaches `review` with a non-empty diff →
  approve → merged into the project base branch → task `done`; reject → feedback stored,
  task back to `ready`, branch preserved. Fully automated in tests.
- **A2 (visibility + takeover):** a PTY session streams real output live over the wire;
  text written via `agent.send` reaches the child's stdin (round-trip proven in tests with
  a mock CLI).
- **A3 (work model):** hierarchy CRUD, cross-project dependency with cycle rejection,
  readiness only when deps are `done`, atomic claim under concurrent claimers (no double
  pull), pull order = priority then unblock-count. Proposed→inbox→approve/reject with
  approver provenance recorded.
- **A4 (honest surfaces):** every §18 wire frame belonging to a later stage answers
  `blocked` with its stage; the TUI renders those surfaces as `blocked`, never hides or
  fakes them; the quota gauge shows `unavailable` (not a number) while no read surface is
  wired.
- **A5 (metering + backoff):** sessions land in the ledger; a rate-limit marker in session
  output flips the worker gauge to `limited` and `may_dispatch` to false.
- **A6 (durability seed):** daemon restart re-queues interrupted tasks and preserves all
  work state (proven against a temp DB).
- **A7 (posture):** Codex reports blocked-with-hint; Claude posture warns when
  `ANTHROPIC_API_KEY` is set; workers never receive API-key env for coding.

## Non-goals of this iteration

Terminal-emulator-grade rendering inside the TUI pane (raw ANSI is streamed and rendered
best-effort; full-fidelity emulation is a registered increment), real quota reading
(`/usage` PTY-parse — next increment on the metering surface), autopilot ticks.
