# Tooling Inspiration — Orchestrators & Spec-Driven Systems (July 2026)

Survey of tools to take inspiration from before building Podium. Official sources cited inline;
items marked UNVERIFIED lack a primary source. Compiled 2026-07-27.

## 1. GSD (Get Shit Done)

**Links:** original (archived): <https://github.com/gsd-build/get-shit-done> · active community
fork: <https://github.com/open-gsd/gsd-core> · docs:
<https://gsd-build-get-shit-done.mintlify.app/introduction> · user guide:
<https://github.com/gsd-build/get-shit-done/blob/main/docs/USER-GUIDE.md>

**What it is:** meta-prompting / context-engineering / spec-driven system driving Claude Code
(also Codex, Gemini CLI, OpenCode, Cursor, Cline) through a disciplined phase loop. Core thesis:
solve **context rot** by externalizing all state into markdown files, running heavy work in
fresh-context subagents, keeping the main session at 30–40% context. ~64.8k stars, MIT.

**Status:** original repo archived 2026-06-26; founder disappeared ~April 2026 with the associated
$GSD token publicly linked to a rug-pull. Active development continues at **open-gsd/gsd-core**
(community fork, v1.8.0, 2026-07-22).

**Workflow phases:** `/gsd-new-project` (interview → research agents → REQUIREMENTS → ROADMAP) →
`/gsd-discuss-phase` (locks implementation decisions) → `/gsd-plan-phase` (4 parallel research
agents → planner → plan-checker loop, max 3 iterations) → `/gsd-execute-phase`
(dependency-aware **waves** of fresh-200k-context executors, one atomic commit per task) →
`/gsd-verify-work` (guided UAT with auto-diagnosis → fix plans) → `/gsd-ship` (PR with required
body sections). Plus `/gsd-progress --next`, `/gsd-resume-work`/`/gsd-pause-work` (HANDOFF.json),
`/gsd-quick`, `/gsd-map-codebase` (brownfield), `/gsd-spike`, `/gsd-debug`, milestones, and
`/gsd-workspace`/`/gsd-workstreams` (parallel workstreams with isolated `.planning/` state).

**File conventions — `.planning/`:** root: `PROJECT.md` (always loaded), `REQUIREMENTS.md`
(REQ-001 IDs), `ROADMAP.md` (phase statuses pending/planned/executed/verified/shipped),
`STATE.md`, `config.json`, `MILESTONES.md`, `HANDOFF.json`. Per phase (`phases/01-name/`):
`CONTEXT.md` (locked decisions D-01…), `RESEARCH.md` (incl. Package Legitimacy Audit
[OK]/[SUS]/[SLOP]), `01-01-PLAN.md` (atomic plans as `<task>` XML: name/files/action/verify/done),
`01-01-SUMMARY.md`, `VERIFICATION.md`, `VALIDATION.md`, `UAT.md`. Also `research/`, `codebase/`
(STACK/ARCHITECTURE/CONVENTIONS/CONCERNS.md), `spikes/`, `todos/`, backlog as pseudo-phases 999.x.

**Context management:** every researcher/planner/executor/verifier is a fresh subagent (150–200k
budget); orchestrator `/clear`s between commands; waves parallelize independent plans; per-plan
atomic commits.

**Config (`.planning/config.json`):** `model_profile` (budget/balanced/quality/inherit/yolo),
per-role models + overrides, `dynamic_routing` (tier escalation on failure),
`parallelization.max_parallel_plans`, `workflow.*` toggles (discuss_mode, nyquist_validation,
context_coverage_gate, drift_action), `git.branch_strategy` (feature-branches/trunk/worktree),
`hooks.enabled`, `security.*`.

**Worth stealing:** fresh-context executor per atomic plan + wave scheduling; everything-is-
markdown resumable state; **decision-coverage gates** (locked D-nn decisions must appear in plans —
blocking at plan time); **Nyquist validation** (every task carries an automated verify command
mapped to a requirement before code is written); typed human checkpoints inside plans
(`checkpoint:human-verify`); package-legitimacy gate against hallucinated deps; HANDOFF.json
continuity; tier routing that escalates on failure, never silently downgrades.

**Lacks:** single-user prompt-ware inside one CLI session — no daemon/scheduler, no quota/rate
management, no cross-project portfolio, no live monitoring of parallel executors; governance risk
proved real (rug-pull → fork).

## 2. Conductor (Melty Labs)

**Links:** <https://conductor.build> · docs: <https://www.conductor.build/docs/> · worktrees:
<https://www.conductor.build/docs/concepts/git-worktrees> · config:
<https://www.conductor.build/docs/reference/conductor-json> · scripts:
<https://www.conductor.build/docs/reference/scripts>

**What it is:** free Mac app (v0.77.2, July 2026) running **Claude Code, Codex, Cursor, OpenCode
agents in parallel**, each in its own git worktree; BYO subscription/keys.

**UX model:** workspace = task (Cmd+N): own branch, files, terminal, diff, review path; worktrees
share `.git` with separate working trees; dashboard shows every agent at a glance; built-in diff
viewer; flow ends review → PR → merge → archive workspace.

**Config:** `.conductor/settings.toml` (repo-committed, schema URL), with lifecycle `[scripts]`:
`setup` (on workspace creation, `$CONDUCTOR_WORKSPACE_PATH`), per-id `run` commands
(`$CONDUCTOR_PORT` injected), `archive` (cleanup of resources outside the workspace dir).

**Worth stealing:** workspace lifecycle create → setup → run (port injection) → review diff → PR →
merge → **archive script**; repo-committed TOML config with published JSON schema; one-glance
parallel status + integrated diff review as the primary human surface.

**Lacks:** Mac-only, closed source; no task/backlog management, no spec discipline, no dependency
graph, no quota awareness — sequencing entirely manual.

## 3. Adjacent tools

- **spec-kit (GitHub)** — <https://github.com/github/spec-kit> · docs
  <https://github.github.com/spec-kit/> — ~124k stars, agent-agnostic SDD toolkit (30+ agents):
  `/speckit.constitution → specify → plan → tasks → implement`, plus `clarify`, `analyze`
  (cross-artifact consistency gate), `checklist`, `taskstoissues` (tasks → GitHub issues),
  `converge` (codebase-vs-spec alignment). Artifacts: `.specify/memory/constitution.md` +
  `specs/<NNN>/spec|plan|tasks.md`. Steal: constitution as persistent principles gate,
  analyze/converge checks, tasks→issues export. Lacks: any execution orchestration.
- **Taskmaster** — <https://github.com/eyaltoledano/claude-task-master> — CLI + MCP server;
  `parse-prd` → `tasks/tasks.json` (tasks, subtasks, dependencies, statuses); `next_task`
  dependency-aware selection; tiered MCP tool modes (Core ~7 tools ≈ 70% token reduction).
  Steal: canonical machine-readable work graph + `next_task`; token-tiered tool exposure.
  Lacks: single-agent, no parallelism; state is a JSON file, not a service.
- **OpenSpec (Fission-AI)** — <https://github.com/Fission-AI/OpenSpec> — change-proposal-centric
  SDD: every change is a proposal (spec-diff + task list agreed before code); deliberately
  low-ceremony, brownfield-first. Steal: change-as-proposal framing. Lacks: no orchestration.
- **BMAD-METHOD** — <https://github.com/bmad-code-org/BMAD-METHOD> — simulated agile org of role
  agents (analyst, PM, architect, SM, dev, QA) producing PRD → architecture → sharded stories.
  Steal: role-scoped context, story-sharding. Lacks: heavy ceremony; 2026 comparisons warn it
  "reproduces your chaos across seven agents"
  ([DEV](https://dev.to/willtorber/spec-kit-vs-bmad-vs-openspec-choosing-an-sdd-framework-in-2026-d3j)).
- **Sculptor (Imbue)** — <https://imbue.com/sculptor/> — parallel Claude Code agents in **Docker
  containers** (full repo copy each; devcontainers, port forwarding); **Pairing Mode** =
  bidirectional sync of an agent's container into your local IDE. Steal: container isolation
  alternative; pairing/takeover sync. Lacks: Mac-only; no work management.
- **Crystal (stravu)** — <https://github.com/stravu/crystal> — early open-source Conductor-alike
  (parallel worktree sessions, diff review); effectively dormant (last commit Feb 2026).
- **Vibe Kanban (BloopAI)** — <https://github.com/BloopAI/vibe-kanban> — kanban card = agent task
  binding across 10+ agents, inline diff comments feeding back to the agent, app preview. Bloop
  shut down 2026-04-10 ("couldn't find a business model"); community-maintained now. Steal: the
  card↔agent binding + inline-comment feedback. Cautionary tale on sustainability.
- **amux** — <https://github.com/andyrewlee/amux> — Go TUI over tmux (≥3.2): workspace-first,
  worktree per workspace, agent per persistent tmux session; attach/detach = takeover. Steal:
  tmux-backed PTY persistence; worktree import. Lacks: no work management or scheduling.
- **Newly prominent 2026** (from
  <https://github.com/andyrewlee/awesome-agent-orchestrators>; individually UNVERIFIED):
  agent-deck, thurbox, herdr, clave, **garcon** (browser + mobile steering of parallel agents),
  kandev (self-hostable kanban workbench, multi-repo), **octomux** (local dashboard with a
  **unified permission inbox** + diff review), agentsmesh, Orkas, loki-mode (41 agents / 8
  swarms), Switchboard.

## Cross-cutting takeaways for Podium

1. **Two disjoint camps; no one does both.** SDD/work-management systems (GSD, spec-kit,
   Taskmaster, OpenSpec) have no execution layer; session orchestrators (Conductor, Sculptor,
   amux, vibe-kanban) have no work model. Podium's combination targets exactly the gap —
   vibe-kanban came closest and died for business reasons, not product ones.
2. **Convergent file conventions:** markdown-artifact state dirs; machine-readable task graph
   with dependencies; repo-committed config with a published schema.
3. **Convergent execution patterns:** fresh-context subagent per atomic unit + dependency-wave
   scheduling; worktree-or-container per task with setup/run/archive lifecycle hooks; tmux/PTY
   persistence for attach-detach-takeover.
4. **Gates worth copying:** decision-coverage gate; verify-command-before-code; plan-checker
   loops; package-legitimacy audit; spec↔code convergence check; typed human checkpoints;
   unified permission inbox.
5. **Nobody manages quota/rate limits across subscriptions** — open ground, confirming
   `RESEARCH.md §7`.
