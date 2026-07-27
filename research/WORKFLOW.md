# Workflow — Work Management, Operating Modes, TMS, Autonomy & Coordination

**Status:** Draft for approval · **Date:** 2026-07-24 · Companion to `DESIGN.md`, `HLD.md`,
`LLD.md`, `INTELLIGENCE.md`, `RESEARCH.md`.

This document specifies the **work-management layer** of the orchestrator: the native
workspaces/projects/tasks model, the operating-mode spectrum, the Autonomy Controller (autopilot
loop + throw-away minimization + **bidirectional live session handoff**), the **quota-rationing
scheduler**, durability/auto-resume, the optional TMS bridge, resource ingestion, the coordination &
learning layer, **agent teams**, the **overall management layer (mission-control)**, the **mobile
companion**, **bookmarks**, and the **Library & Knowledge layer ("reading room")** with
**read-or-listen (TTS)**.

> **Provisional by construction.** Everything named here — TMS adapters, scheduler policies, gate
> policies, model choices, worker mix, team rosters, UI surfaces, **TTS providers, library
> curation** — is a **candidate / configurable provider**, not a locked decision. A first-class **Comparison / Eval Harness** (`DESIGN §12.19`,
> `INTELLIGENCE.md`) tunes and selects winners empirically, later. Do not read any specific value
> below as final.
>
> **Utilise existing tools; invent only on demonstrated need** (`DESIGN` decision 29). Everywhere
> below — team roles, mission-control, mobile, bookmarks — we **delegate to what already exists**
> (native CLI features, installed tools, the platform's own APIs) and build a new capability only
> when a real gap or the Eval Harness shows nothing adequate exists.
>
> **ToS line (held everywhere):** official CLIs, subscription/login auth (**no API keys**), one
> operator, own accounts/machine, published free tiers only. The system **never spends overflow /
> API credits** and never evades a cap. (`DESIGN §11`, `RESEARCH §2`, decisions 2, 20, 21.)

---

## 1. Overview

Work is **first-class**. The orchestrator is not a chat box that happens to edit files — it is a
work-management system whose job is to keep the fleet of workers (Claude Code, plus Codex and Gemini
when installed and opted-in) **always busy on the *right* thing**, within quota, and to keep the
**human and the fleet coordinated** as peers in one loop.

Two flows meet here:

- **Human → fleet.** You author intent (workspaces, projects, tasks, resources, workflows), choose
  *how hands-on to be* per scope, and set the gates. You top up the backlog; you answer the
  decisions the system hands you.
- **Fleet → human.** The system pulls ready work, assigns the best worker, runs to completion,
  **verifies**, and reports back — narrating what each agent is doing and why, surfacing decisions
  as **human tasks**, teaching you the tool, and **attaching sources** to every output so you can
  verify it.

This layer is useful **with zero external workers** (authoring, ingestion, human tasks, board,
narration all work standalone — `DESIGN §2`, decision 8) and scales up as workers are added.

---

## 2. Native work model

A **Linear-like** hierarchy you author into. The native store (SQLite — `DESIGN §9`) is the system
of record when no external TMS is connected; a connected TMS becomes source-of-truth (§8).

```
Workspace  ──<  Project  ──<  Task  ──<  Subtask
                                │
                                ├─ dependencies (task ↔ task, blocks / blocked-by —
                                │   within OR across projects/workspaces; decision 43)
                                ├─ resources (reading materials + links → ingested, §9)
                                └─ workflow / template (how to execute, §11)
```

### 2.1 Data model

| Entity | Key fields | Notes |
|---|---|---|
| **Workspace** | id, name, default operating mode, default gates, deny-lists, quota policy | Top scope; a repo-family or an area of work. |
| **Project** | id, workspace, name, goal, repo/worktree root, default workflow, mode override | Maps to a codebase or initiative. |
| **Task** | id, project, title, description (intent), **status**, **priority**, **assignee** (agent *or* human), labels, workflow/template, estimate, blast-radius class, confidence | The unit of work; see lifecycle (§3). |
| **Subtask** | id, parent task, … (same shape as Task) | Decomposition; may be graph-partitioned (`INTELLIGENCE §4.1`) to avoid semantic conflicts. |
| **Dependency** | from-task, to-task (**may live in different projects/workspaces**), kind (`blocks`/`blocked-by`/`relates`), origin (`human`/`proposed`) | Gates readiness; a task is `ready` only when deps are `done`. One DAG spans the whole portfolio (decision 43). **Human edges are authoritative** (never overridden; agents can't mutate the DAG); `proposed` edges have no scheduling effect until approved in the inbox; the system flags cycles/contradictions as questions. |
| **Project relation** | from-project, to-project, kind (`consumes`/`shares-infra`/`independent`) | Portfolio-level structure (decision 43); informs cross-project dependency proposals and critical-path pull order (§5, §6). |
| **Resource** | id, task, kind (file/link/ref), URI, provenance, ingest-status | Fetched/parsed/indexed into the Context Engine (§9). |
| **Assignee** | agent-id \| human-id | An agent (Claude/Codex/Gemini worker) **or** the human operator. |
| **Label** | id, name | Free-form tags; drive filtering, routing hints, gate selection. |
| **Workflow/Template** | id, name, stages, gates | SDLC recipe bound to a task (§11). |

- **Priority:** an ordered field (e.g. `P0…P3`) — one input to the scheduler's pull order (§5, §6),
  not the only one.
- **Assignee = agent OR human** is load-bearing: the same queue holds agent work *and* **human
  tasks** (decisions, reviews, learning items — §10). The fleet and the human share one board.
- **Resources** turn a task into *intent + workflow + cited sources* (`DESIGN §6`, decision 10).

---

## 3. Task lifecycle & states

States are a **candidate** set (tunable by the Eval Harness); the shape is stable:

```
proposed ─(human: approve / edit)─▶ ready ─▶ assigned ─▶ running ─▶ verifying ─▶ review/gate ─┬▶ done
   │ (reject)              backlog ─▶ ▲   │                                  │                 └▶ (repair) ─▶ running
   ▼                                  │   └────────────── blocked ◀──────────┘
discarded / parked                    └──────────────────── parked ◀────────────────────────────────
```

| State | Meaning | Who transitions in |
|---|---|---|
| **proposed** | **Auto-detected/proposed, awaiting human approval** in the **Approval Inbox** (§5.4) — *does not run*. | **Detectors** (verifier, gap/TODO scan, failures, follow-ups, ingestion) create it (`DESIGN §2`, decision 35). |
| **backlog** | Authored, not yet schedulable (deps unmet / not triaged). | Human authoring; system on dep-change. |
| **ready** | Dependencies satisfied, well-enough specified to schedule. | System (dep resolver), human triage, **or human approval of a `proposed` task** (§5.4). |
| **assigned** | Claimed by the scheduler for a specific worker. | **Scheduler** (atomic claim, §5). |
| **running** | Worker executing in an isolated worktree. | Autonomy Controller. |
| **verifying** | Verifier/critic checking the result (verify→repair). | Verifier (`INTELLIGENCE §7`). |
| **review/gate** | Awaiting a declarative gate — often a **human task**. | System raises; human/gate resolves. |
| **done** | Verifier passed **and** all gates cleared. | System, only via verifier + gates. |
| **blocked** | Cannot proceed (missing dep, failed verify past retry, needs a decision). | System or worker. |
| **parked** | Deliberately paused (quota back-off, deprioritized, human hold). | Scheduler (quota, §6) or human. |

Rules:
- **Nothing in `proposed` runs.** Detection is automatic, but the **gate is the human's**: a
  `proposed` task must be **approved (→ `ready`)**, **edited (→ `ready`)**, or **rejected
  (→ discarded / parked)** before it can be scheduled (`DESIGN §2`, decision 35, gates decision 7).
- **Only the verifier can move a task to `done`** (a gate, not a vibe — `RESEARCH §4`, decision 14).
- **Atomic claim** on `ready → assigned` so idle workers never double-pull (pattern reused from
  amux — `RESEARCH §4`).
- **`review/gate` never throttles the fleet** (decision 45): finished work queues **unbounded**
  awaiting the operator, who catches up on their own rhythm. The system maintains a **risk profile**
  per finished diff (size, blast-radius class, verifier signal, task class), **triages** the review
  queue (highest-leverage first, low-risk batched), and may raise a one-tap **auto-merge proposal**
  for low-risk items via the Approval Inbox (§5.4) — never a silent merge.
- Every transition is an **event** in the persistent log (durability, §7; narration, §10).

---

## 4. Operating modes (a gated spectrum)

Autonomy is a **spectrum, not a switch** (`DESIGN §5`, decision 7), set **per
workspace/project/task** (most specific wins). Three reference points along one axis:

| Mode | Who decides the next action | Who acts | Who reviews | Typical use |
|---|---|---|---|---|
| **Interactive (co-pilot)** | Human | System (asks before acting) | Human, live | Exploration, high-stakes, unfamiliar code. |
| **Assisted** | System proposes → human approves | System | Human, per task | Steady work you want to sign off on. |
| **Autonomous (autopilot)** | System | System (to completion) | **Verifier** + gates; human only on escalation | Well-specified, low-blast-radius backlogs. |

A single run can **mix modes per stage** (e.g. autopilot the implementation, human-gate the
migration). Modes are enforced by **declarative gates**:

```yaml
# illustrative — gate policies are pluggable/configurable, not locked
gates:
  - when: task.blast_radius == "schema-migration"
    require: human_approval           # never auto
  - when: verifier.passed and diff.lines < 40 and no_deny_zone_touched
    action: auto_commit               # to worktree branch, NOT main (§5)
  - when: task.confidence < 0.6
    action: escalate_as_human_task    # don't guess
  - when: quota.weekly_remaining_ratio < projected_need
    action: prefer_cheap_model        # or park (§6)
```

Gates are the contract between the human and the Autonomy Controller: they decide *when the loop may
proceed unattended* and *when it must stop and ask*.

**Live handoff along the spectrum.** The spectrum is not only chosen up front — a **running** session
can be slid along it **mid-flight**, in either direction (`DESIGN §5`, decision 27): **promote** a
watched interactive session to autonomous (hand it to the Autonomy Controller with a goal + gates), or
**take over** a running autonomous session and drive it by hand, then hand it back. Mechanically this
swaps the session's *driver* (human PTY input ↔ controller) and its *permission/gate policy*, without
tearing down the session. Full mechanics in **§5.3**.

---

## 5. Autonomy Controller (the autopilot loop)

The controller keeps the fleet at **max utilization on the right work** while **minimizing
throw-away**. Core loop:

```
pull ready task ─▶ assign best worker ─▶ run ─▶ verify ─▶ handoff/commit ─▶ replenish ─┐
      ▲   (MoE gate, INTELLIGENCE)        (worktree)  (verify→repair)   (no auto-merge)  │
      └──────────────────────────────────────────────────────────────────────────────────┘
```

1. **Pull** the highest-value **ready** task, subject to the quota scheduler (§6) and deny-lists.
2. **Assign best worker** via the **MoE-over-agents gate** (contextual bandit — routes by task-type,
   cost, quota, track record; `INTELLIGENCE §6`). Sparse route or ensemble+judge.
3. **Run** in an isolated **git worktree** (`DESIGN §9`, decision — isolation via worktrees).
4. **Verify** with the independent verifier/critic; failures trigger a bounded **verify→repair**
   loop (`INTELLIGENCE §7`). The verifier is also the RLAIF reward signal.
5. **Handoff / commit** the result as a **readable artifact** on the **worktree branch** — **never
   auto-merged to `main`** (see below).
6. **Replenish.** Pull the next ready task, or **propose follow-ups** from verifier findings / gaps —
   proposed work lands in the **Approval Inbox** as `proposed` (§5.4), **not** straight to `ready` — so
   *if tasks finish, more get created* for the human to approve and nothing idles silently
   (`DESIGN §2`, decisions 8 & 35).

**Classify before assigning; never hard-block.** Step 2 first asks *is this even a coding task?* — the
router can route to a **non-code outcome** (a human/decision, research, or external action — §5.5)
instead of forcing a code agent (`DESIGN §12`, decision 37). And the loop **never hard-blocks on the
human**: anything needing the operator **queues** (§5.4, §10) while every runnable task keeps flowing —
the system **absorbs human variability** rather than stalling on it (§10.1, `DESIGN §12`, decision 36).

### 5.1 Throw-away minimization (a user priority)

Unattended work must not generate garbage. The controller only auto-runs work that is **well-specified,
low-blast-radius, and verifiable**; everything else degrades to a safer mode:

| Mechanism | Behavior |
|---|---|
| **Auto-run filter** | Only well-specified + low-blast-radius + **verifiable** tasks run unattended. |
| **Uncertain / risky → downgrade** | High blast-radius or low confidence → convert to a **human task** or drop to **plan / read-only** mode. Don't guess. |
| **Worktree isolation, no auto-merge** | Every run in its own worktree; results land on a branch. **Merge to `main` is never automatic** — it is a gate (often human). |
| **Verifier gates "done"** | A task is `done` only when the verifier passes (§3). Merge-on-green-CI / eyeballing is explicitly *not* enough (`RESEARCH §4`). |
| **Cheap models for speculation** | Speculative / exploratory / draft steps use cheap models (e.g. **Haiku**) before spending premium capacity or committing. |
| **Escalate low-confidence** | Below a confidence threshold, **escalate** (human task / clarifier) instead of proceeding. |
| **User no-go zones (deny-lists)** | User-set deny-lists (paths, actions, task-types) that autopilot must never touch; a hit forces escalation. |

### 5.2 Continuous supply

The backlog is a reservoir the controller drains and the human/system refill. Sources of new work:
human authoring ("I'll create more tasks"), **verifier-found follow-ups**, gap analysis from the
Context Engine/graph, and periodic self-maintenance agents (Updater/Watcher, Self-Diagnostic —
`RESEARCH §6`, decision 22). Goal: the fleet is **never idle for lack of the right work**, but never
runs *wrong* work just to stay busy — quota (§6) and the auto-run filter (§5.1) bound it.

**Auto-authored, human-approved.** Most of that supply is **not manually authored**: the system
detects and *proposes* it, and the proposals queue in the **Approval Inbox** (§5.4) as `proposed`
tasks rather than entering the active queue — the human's one-tap approve/edit/reject is what turns
supply into schedulable `ready` work (`DESIGN §2`, decisions 8 & 35).

### 5.4 Auto-detection & the Approval Inbox

*(provisional — `DESIGN §2`, decision 35)* **New tasks don't need manual authoring.** The system
**automatically detects and proposes** work, but proposals **never auto-run** — they land in a
dedicated **Approval Inbox**, held as `proposed` (§3) and kept **out of the active queue** until a
human acts. Detection is automatic; the **gate is the human's** (decision 7).

**Detection sources (auto → `proposed`):**

| Source | Proposes |
|---|---|
| **Verifier findings** | Gaps/defects the verifier/critic surfaces (`INTELLIGENCE §7`). |
| **Code gaps / TODOs** | `TODO`/`FIXME`, missing tests/docs, and gap analysis from the Context Engine/graph. |
| **Failures** | Failing runs, broken builds/CI, verify→repair dead-ends. |
| **Follow-ups from finished work** | Next steps a completed task implies (the §5 replenish step). |
| **Ingested reading materials** | Action items extracted from a task's resources/links (§9) and the Library (§16). |

**The gate (one tap, human-only):**

| Action | Effect |
|---|---|
| **Approve** | `proposed → ready` — enters the active queue as-is. |
| **Edit** | Adjust spec/scope/priority, then `→ ready`. |
| **Reject** | `→ discarded` (or **parked** to reconsider later); never runs. |

This ties the **continuous-supply** idea (§5.2, decision 8) to a human checkpoint: **replenishment
proposes into the inbox, not straight to `ready`**, so the reservoir stays full *and* the operator
stays in control. The inbox is a first-class surface — reachable from mission-control (§13), the phone
(§14), and the UI (§17).

### 5.3 Bidirectional live session handoff

A session's **driver** and its **permission/gate policy** are separable from the session itself, so
either can be swapped on a **live, mid-flight** session without restarting it (`DESIGN §5`, decision
27). Two directions:

| Direction | Trigger | What happens | Ends with |
|---|---|---|---|
| **Promote — interactive → autonomous** | You've been co-piloting and it's on rails. | You hand the live session to the **Autonomy Controller** with a **goal + gates**; the controller takes the driver seat, the loop (§5) continues (verify → replenish), you walk away. | Autopilot, escalating only on gates. |
| **Take over — autonomous → interactive** | An autonomous session is drifting, on a high-stakes step, or you just want the wheel. | You **seize** the live PTY (the controller yields the driver), drive by hand — type into the terminal, co-edit (`DESIGN §8`) — then **hand back** to autopilot when done. | Back to autonomous, or parked/closed by you. |

Mechanics (provisional, delegate-first — decision 29):
- **Session continuity.** The underlying worker keeps running; we swap *who supplies input* and
  *which gate policy applies*, reusing native session primitives where they exist (Agent SDK
  `session_id` resume/fork, `permission_mode`, `can_use_tool` / `PreToolUse` hooks — `RESEARCH §1.1`;
  Gemini `--acp`, Codex app-server for the other workers — `RESEARCH §1.2–1.3`).
- **Driver arbitration.** The PTY already has two input sources (you and the Brain, `DESIGN §8`);
  handoff is choosing which one is *authoritative* and re-labelling the session's mode. Human vs
  agent actions stay attributed (co-editing attribution, `DESIGN §8`).
- **Gate/permission swap.** Promotion attaches the target scope's gates (§4); takeover drops to
  interactive "ask before acting". Every swap is an **event** in the log (§7).
- **From anywhere.** Handoff is driven from the desktop UI, the **management layer** (§13), or the
  **phone** when the laptop is online (§14).

**Tie to throw-away minimization (§5.1).** Takeover is the human's *emergency brake* on a drifting
autonomous session: rather than let an unattended run generate garbage, a human can **grab it live**,
correct course, and either finish by hand or re-promote. This complements the auto-run filter,
worktree-isolation/no-auto-merge, and verifier gate — a **human-in-the-loop escape hatch** that keeps
unattended work from going off the rails.

### 5.5 Not every task is a coding task (classification & rerouting)

*(provisional — `DESIGN §12`, decision 37)* **The code harness is not the solution for everything.**
Before assigning a worker (loop step 2), the router/classifier asks *what kind of task is this?* — and
can conclude a code agent is the **wrong tool**. When it does, it **reroutes or escalates** rather than
forcing the harness. **Abstention / escalation is a first-class routing outcome, not a failure** — the
opposite of the "everything looks like a nail" trap.

**Non-coding task outcomes (the router may route here instead of a code agent):**

| Outcome | Routed to | When |
|---|---|---|
| **Human task** | The **human queue** (assignee = human, §10) | Needs human **judgment / decision / approval** only the operator should make. |
| **Reasoning / research** | An internal **reasoning/research** agent (RAG + ingestion, §9) | Reading, analysis, comparison, or **design** — thinking, not editing files. |
| **External / manual action** | An **external tool** or a **manual step** the operator performs | The action lives **outside** the harness (a console click, a purchase, a conversation, a non-code tool). |
| **Decide / clarify** | The **clarifier** (§10) | Ambiguity that must be **resolved before** any work can be well-specified. |

- **Classification, not just routing.** A task carries a **kind** (code vs human vs research vs
  external vs decide/clarify); the classifier can set or revise it, and a task may **change kind**
  mid-flight (e.g. an implementation blocked on a decision spawns a **decide/clarify** step).
- **Invent, don't fake.** When **nothing adequate exists** for a non-code need, the system proposes
  **inventing a capability** (`DESIGN §12`, decision 29) rather than forcing the code harness to fake
  it. Escalation to the human is always a valid landing.
- **Ties in.** This reuses the MoE-over-agents gate (`INTELLIGENCE §6`) as the router, the human-task
  queue and clarifier (§10), and the Resource Ingestion / research path (§9) — no new engine
  (decision 29).

---

## 6. Quota-rationing scheduler

**The centerpiece.** The objective (decision 20, `RESEARCH §3`) has two halves that pull against
each other, reconciled by a **reservoir / token-bucket controller**:

- **Burn the perishable 5-hour window.** The rolling **5h** allocation is **use-it-or-lose-it** —
  unused capacity evaporates. So **fully utilise every 5h cycle**: keep the best worker busy up to
  the window's ceiling.
- **Pace the weekly cap.** The **weekly** cap is a scarcer, slower reservoir. Smooth consumption
  across 7 days so it **lasts the whole week and drains *almost completely just before* reset** —
  never front-loaded and starved mid-week, never left large amounts unused at reset.

Formally: **maximize 5h utilization subject to a weekly budget smoothed across the week**, never
overflowing either, **never spending overflow / API credits** (hard rule).

### 6.1 Controller sketch

| Control | Role |
|---|---|
| **5h bucket** | Short-horizon token bucket; refill each rolling window; target ≈ full drain per cycle. |
| **Weekly reservoir** | Long-horizon budget; a **pace line** = ideal cumulative burn to hit ≈100% just before reset. |
| **Pace error** | `actual_cumulative − pace_line`. Ahead of pace → throttle / prefer cheap models / park low-value work. Behind pace → open the throttle, pull more, use premium models. |
| **Per-worker rationing** | Each worker (Claude/Codex/Gemini) rationed to **its own** message/rate limits; Claude the safest unattended default, Codex/Gemini per-worker opt-in (decision 21, `RESEARCH §2`). |
| **Overflow guard** | Hard stop before any overflow/API-credit spend; back off as caps approach. |
| **Regime detector** | **One bucket today** (headless + interactive share the 5h+weekly pool). **Two buckets** if the paused **2026-06-15 SDK-credit split** un-pauses (separate monthly headless credit) — scheduler detects the regime and adapts (`RESEARCH §3`, decision 20). |

Reading balance: no CLI subcommand exposes remaining quota; the metrics layer estimates a live
utilization gauge from session telemetry (interactive `/usage`, `ccusage`-style log parsing as a
*consumption estimate*, not the bill — `RESEARCH §3`).

### 6.2 Interaction with autonomy

The scheduler modulates the Autonomy Controller (§5):
- **Near caps / ahead of pace →** back off: park low-value tasks, prefer cheap models (Haiku),
  shrink parallelism.
- **Behind pace / early in window →** saturate: pull more, allow premium models and ensembles.
- Gates (§4) can reference quota directly (e.g. `prefer_cheap_model` when weekly is tight).

### 6.3 Worked example (weekly nearly exhausted just before reset)

> Weekly cap resets **Sunday 00:00**. Suppose the week's budget = **B** message-units.
>
> - **Pace line:** ideal cumulative burn ≈ `B × (elapsed_fraction_of_week)`, targeting ~98–100% at
>   Saturday 23:59.
> - **Mon–Tue (early):** plenty of headroom → run at full 5h saturation each window, premium models
>   and small ensembles allowed. Actual tracks slightly *behind* pace (banking slack).
> - **Wed:** a burst of high-value tasks pushes actual **above** the pace line. Controller throttles:
>   parks `P3` speculation, switches drafts to Haiku, drops best-of-N to single-route — while still
>   **fully draining each 5h window** with cheaper work so the perishable short-window capacity is
>   never wasted.
> - **Thu–Fri:** actual re-converges to the pace line; premium models re-enabled as headroom returns.
> - **Sat:** deliberately **spend down the remaining reservoir** — open the throttle, pull the
>   remaining backlog, allow premium models — so weekly lands at **≈99% just before Sunday reset**.
>   The overflow guard ensures it never crosses 100% into paid credits.
>
> Result: **every 5h window fully used all week** *and* **the weekly cap nearly exhausted right at
> reset** — the two-timescale objective satisfied, zero overflow spend.

---

## 7. Durability & auto-resume

The system is **durable, auto-resuming, power-loss-tolerant** (decision 23):

- **Boot/login service.** The daemon runs as a boot/login service with **persistent state** (SQLite:
  runs, sessions, events, tasks, handoffs — `DESIGN §9`).
- **Auto-resume autopilot on restart.** After a crash/reboot/power loss, the daemon **re-dispatches
  interrupted tasks** and **resumes or forks sessions** where possible (Agent SDK
  `session_id` resume/fork — `RESEARCH §1.1, §4`) — **unless a setting disables it**. Interrupted
  `running`/`verifying` tasks return to `ready` (or resume in place) rather than being lost.
- **Idempotent claim + event replay.** Atomic task-claiming plus the append-only event log let the
  controller rebuild exactly where it was.

### 7.1 Optional split topology

Deployment is flexible; the split is a **later option decided by comparison, not locked**:

- **Always-on coordinator** on the user's **free Oracle Cloud "Always Free" micro instance** —
  lightweight: queue, state, scheduler, watchdog, notifier. No subscription/compute lives here.
- **Heavy coding agents** on the **local, electricity-dependent machine** where the subscription +
  compute live.
- When local is **off**, the coordinator **persists intent** and **resumes dispatch** when local
  returns. This keeps the quota pacer (§6) and human-task inbox (§10) alive even while workers sleep.

---

## 8. TMS Bridge

Bidirectional adapters to external task-management systems. **Optional and works-without-any**
(decision 9, `DESIGN §6`). Candidate adapters (not locked): **Linear, Jira, GitHub Issues**, others.

| Concern | Rule |
|---|---|
| **Source of truth** | **External TMS wins when connected** (mirror/augment it); the **native store** is source-of-truth when none is connected. |
| **Direction** | Bidirectional: **import** issues → tasks; **push** status/results/links back. |
| **Mapping** | External issue ↔ native Task; external states ↔ lifecycle states (§3) via a per-adapter map. |
| **Auth** | Official APIs for TMSes the user owns/is authorized to use (`DESIGN §11`). |
| **Standalone** | With no adapter, everything in this doc still works on the native store. |

Prior-art patterns reused: **GitHub-Issues-as-truth + deterministic ops** (ccpm), **spec/plan/code
checkpoints** (Backlog.md) — `RESEARCH §4`. The adapter interface is a pluggable provider; specific
field mappings and conflict-resolution policies are tuned by the Eval Harness.

**Third-party-agent protocol targets (provisional — `RESEARCH §7.3`).** The bidirectional bridge
should target the **three real mid-2026 third-party-agent protocols the industry converged on**,
mapping our native **task + agent-session** model onto whichever tracker the user has (or none):
**Linear** — OAuth `actor=app` + `AgentSessionEvent` webhooks + `agentActivityCreate`
([docs](https://linear.app/developers/agent-interaction)); **GitHub** — Agent Tasks API
([docs](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/use-cloud-agent-via-the-api));
**Atlassian** — Forge `agentConnector`, JSON-RPC/SSE
([docs](https://developer.atlassian.com/platform/forge/remote-agents-in-jira/)). The convergence is
consistent across all three: **issue-as-truth + a typed agent-session lifecycle posted back to the
ticket + a human-stays-accountable guardrail**. Our differentiator is owning a **native** board that
**also bridges to any of these — or none** (tracker-agnostic).

---

## 9. Resource ingestion

A task carries **resources** — reading materials, attached links, references — so the agent works
from **real, cited sources, not guesses** (decision 10, `DESIGN §6`).

```
attach resource ─▶ fetch ─▶ parse/extract ─▶ index into Context Engine ─▶ agent retrieves (cited)
    (file/link/ref)  (httpx)  (readability/md)   (INTELLIGENCE §5, RAG)     provenance shown to user
```

- **Fetch/parse:** `httpx` + readability/markdown extraction for links (`DESIGN §9`), respecting
  robots/access (`DESIGN §11`).
- **Index:** into the **Context Engine** (deep RAG + rerankers + graph-expand — `INTELLIGENCE §5`)
  so the assigned agent retrieves from the ingested material.
- **Provenance shown to the user:** every ingested source is tracked and surfaced; outputs **attach
  citations** back to these sources so the user can **verify** (no hallucination — see §10).
- **Ingest status** is a resource field (§2.1); a task can gate on "resources ingested" before run.
- **Bookmarks feed ingestion.** A **bookmark** (§15) — a doc/link the operator saved, often from the
  phone — can be **attached to a task as a resource**, at which point it enters this same
  fetch → parse → index pipeline. Bookmarks are the low-friction on-ramp to Resource Ingestion.

---

## 10. Coordination & Learning layer

A **first-class product feature**: the system manages work **for** the user and keeps the human side
of the loop healthy (decision 11, `DESIGN §2, §7`).

| Capability | What it does |
|---|---|
| **Human tasks** | Decisions, reviews, approvals, and **learning items** enter the *same* queue as agent tasks (assignee = human, §2). The system coordinates the human side, not just the agents. |
| **Activity narration feed** | A live feed of *what the fleet is doing and why* — routing choices, verifier results, gate stops — so you are never lost (`DESIGN §8`). |
| **Teaching (unbiased)** | Tracks **what the user knows / doesn't**, expands their horizon, and teaches the tool as they go — **unbiased**, not steering toward one vendor/answer. |
| **Cite sources** | Outputs **attach sources/citations** (from ingested resources, §9, and retrieval) so the user can **verify** — a hard anti-hallucination stance. |
| **Help express intent** | The clarifier helps turn vague asks into well-specified tasks (which also feeds throw-away minimization, §5.1) — surfacing ambiguity as a question rather than guessing. |

The learning signals (what the user reviews, edits, approves) also feed the metrics/adaptation layer
(`INTELLIGENCE §11`) — but the coordination layer's job is the **human's understanding and control**,
first-class alongside the agents' output.

### 10.1 Absorbing human variability (asymmetric agents & humans)

*(provisional — `DESIGN §12`, decision 36)* Agents and the human are **asymmetric**: agent capability
is **fixed-or-rising**, but the human's **capacity, attention, availability, and skill vary** (energy,
focus, time, mood, learning curve, life). The coordination layer is built to **absorb that variance,
not depend on it** — it never treats the human as a constant oracle:

| Principle | Behavior |
|---|---|
| **Never hard-block on the human** | Questions/approvals **queue** in the inbox (§5.4) and the human-task queue (§10) — they **never stall runnable work**. The fleet keeps flowing on everything that doesn't need the operator; durable auto-resume (§7) means an absent human never freezes the system. |
| **Right-size & right-time the ask** | What the system asks of the human is **sized and timed to their current state** — batch low-urgency items, surface only what matters now, don't flood a busy or low-energy operator. Escalate *only when needed* (`DESIGN §12`, decision 32). |
| **Re-prioritize & re-calibrate — do *not* widen autonomy** | Human state/energy/presence **re-prioritizes** *which* already-autonomous work runs and *what* is surfaced, and **calibrates communication** (how much to explain, when to ask, more co-piloting when engaged / fewer interruptions when away) — sliding *how it collaborates* along the modes spectrum (§4). It does **not** widen autonomy scope: low or varying energy **never** makes the system do *more* on its own. Autonomy stays governed by the gates (§4) and design; the system only ever runs work **already designated autonomous**, and merely re-orders + re-surfaces it (`DESIGN §12`, decision 36 *revised*). |
| **Degrade gracefully** | When the human is unavailable, the system **degrades gracefully** rather than breaking: safe work proceeds, unsafe/uncertain work parks or queues for later, nothing is lost. |
| **Track engagement to serve, not surveil** | The **learner model** (`INTELLIGENCE §11`) tracks **fluctuating capacity/engagement** (not just knowledge) — strictly to *serve* the human and human-controlled, **not surveillance**. |

**Human-efficiency metrics model variance, not a fixed baseline.** The efficiency dashboards
(§17, `INTELLIGENCE §11`) treat the human's throughput as a **distribution with variance** — a
**low-energy week is not "getting worse"**, just a normal fluctuation. Trends are read against expected
variance so the system never mistakes a quiet week for regression.

### 10.2 User-State model (reads *how* you communicate)

*(provisional — `DESIGN §12`, decision 38; `INTELLIGENCE §11`)* A model infers the operator's
**current state** not from what they say but from **how they say it** — message style, clarity, focus,
verbosity — placing them on a spectrum from **"very off"** (scattered, unfocused) to **"very to the
point"** (sharp, decisive). That inferred state drives exactly two levers:

| Lever | Effect |
|---|---|
| **Prioritization** | *What* to tee up vs. defer — surface decisive, high-signal work when the user is sharp; batch, simplify, and hold back low-urgency load when they're scattered. Re-orders the *already-autonomous* queue and the human-task inbox (§5.4, §10). |
| **Communication calibration** | *How* to communicate — how much to explain, when to ask vs. wait, how to phrase, how densely to narrate (§10). |

Crucially, this **does not change autonomy scope** (decision 36 *revised*, §10.1) — it never makes the
system act more autonomously because the user seems off; it only re-prioritizes and re-phrases. It is
also **distinct from the CS/SWE knowledge learner-model** (`DESIGN §12`, decision 33): that models what
the user *knows*; this models their transient **state/engagement**. Human-controlled, transparent, and
**not surveillance**.

### 10.3 Authorship provenance (human vs which agent)

*(provisional — `DESIGN §12`, decision 39; `HLD §3`, `INTELLIGENCE §11`)* The system always tracks
**who wrote what** — the human vs. *which* agent — across **messages, edits, artifacts, and shared
context**. This **generalizes the co-editing / diff-sync attribution** (`DESIGN §12`, decision 16, §4
operating modes) from edits to *all* content. When the orchestrator launches an agent, that agent and
every layer can see **what came from another agent vs. from the human**. It matters because:

- The **User-State model (§10.2)** must read **only the human's own words** — never agent output — or
  it would model the agents' style instead of the operator's state.
- **Agents must not mistake** another agent's (or the human's) text for ground truth / the human's
  intent; provenance keeps human intent distinguishable from agent-generated content everywhere.

Provenance is **first-class, tagged at write time**, not inferred after the fact.

---

## 11. Templates / SDLC workflows

Each task selects a **workflow / template** describing *how* it is executed (`DESIGN §6`, `HLD §3`).

| Template | Shape (illustrative — composable & pluggable) |
|---|---|
| **feature** | clarify → plan → implement → test → verify → review-gate |
| **bugfix** | reproduce → locate (graph) → fix → regression-test → verify |
| **refactor** | map blast-radius (graph) → transform → verify no-behavior-change |
| **spike** | read/ingest (§9) → prototype (cheap model) → write-up → human-review |
| **review** | load diff → critique → produce human task |
| **docs** | ingest sources → draft (cited) → verify claims → review-gate |

- **Composable:** stages are building blocks; users can **build their own features/templates**;
  meta-orchestration lets a template invoke Router/Parallel/Pipeline modes (`HLD §3`) per stage.
- **Gates per stage** (§4) let one template mix operating modes (autopilot implement, human-gate
  migrate).
- Templates are **candidates**, tuned/selected by the Eval Harness; nothing above is a fixed recipe.

---

## 12. Agent Teams

Individual agents compose into **teams** — named groups of collaborating agents that share context
and memory and are assigned, as a unit, to a project or task (`DESIGN §4`, decision 24, `HLD §3–4`).
A team blends all three kinds of agent:

- **External coding agents** (Claude/Codex/Gemini CLIs) — the implementers/heavy lifters.
- **Internal capability agents** (graph-builder, deep-RAG, clarifier, verifier, distiller) — the
  smart layer (`INTELLIGENCE §10`).
- **Workflow-peer agents** — peer agents that *collaborate on the same step* (fan-out, cross-check,
  synthesize), leaned on **heavily** (decision 24). A "peer" is a role in the team, not a separate
  agent class.

### 12.1 Roles

Each member holds a **role**; one member is the **lead / coordinator**. Roles are a candidate set
(tunable by the Eval Harness):

| Role | Responsibility | Typically |
|---|---|---|
| **Lead / Coordinator** | Owns the team's task outcome: divides labor, dispatches sub-work to members, resolves disagreements, **synthesizes** peer outputs, and is the team's interface to the Brain / Autonomy Controller. | The Brain, or a strong CLI acting as conductor. |
| **Planner** | Decomposes intent into a plan / subtasks; graph-aware partitioning to avoid semantic conflicts (`INTELLIGENCE §4.1`). | Reasoning-Provider-backed. |
| **Implementer(s)** | Do the actual edits in isolated worktrees; often **several in parallel** (fan-out). | External coding CLIs. |
| **Reviewer** | Critiques diffs — design, style, correctness — and files review/human tasks. | CLI or internal critic. |
| **Verifier** | Independent verify → repair; **the only role that can move a task to `done`** (§3). | Internal verifier (`INTELLIGENCE §7`). |
| **Tester** | Writes/runs tests and regressions; feeds the verifier. | External CLI + tools. |
| **Researcher / Ingestor** | Pulls a task's resources (§9) and retrieves cited context (RAG). | Internal capability agents. |

Small teams collapse roles (one member wears several hats); large teams fan out (many implementers,
multiple reviewers). Rosters are **provisional**, tuned per project by the Eval Harness.

### 12.2 Team templates (per SDLC)

Teams are **templated per SDLC**, mirroring the workflow templates (§11): a `feature-team` (planner +
N implementers + reviewer + verifier + tester), a `bugfix-team` (reproducer + locator + fixer +
verifier), a `docs-team` (ingestor + drafter + fact-checker), a `spike-team` (researcher + cheap-model
prototyper + writer). A template pairs a **roster** with a **collaboration pattern** and default gates;
users can build their own (composable, like §11).

### 12.3 Collaboration = a layer over the orchestration modes

Team collaboration is **not a new engine** — it composes over the existing Router / Parallel /
Pipeline orchestration modes (`HLD §3`, decision 24). The team is the *who*; those modes are the
*how*:

- **Fan-out** (several implementers on partitioned sub-work, or best-of-N on the same) → **Parallel**.
- **Cross-check** (peers review/vote on each other's output) → **Parallel + judge** (the MoE judge /
  verifier, `INTELLIGENCE §6–7`).
- **Synthesize** (lead merges peer outputs into one result) → the lead's **Pipeline** stage.
- **Route** (send each sub-task to the best-suited member) → **Router** (MoE-over-agents gate).

### 12.4 Teams on the operating-modes spectrum

A team runs at **any point on the autonomy spectrum** (§4), set per its assigned scope:

- **Interactive** — you steer the **lead** (or drop into any member's PTY, §5.3); the team assists.
- **Autonomous** — the **Autonomy Controller** (§5) drives the **lead**, which coordinates members
  within the *same* gates (§4) and the *same* quota rationing (§6). The team is just what the
  controller assigns instead of a lone worker.
- A **live handoff** (§5.3) can promote/take-over the whole team's session mid-flight.

### 12.5 Example team

> **`feature-team` on `project: billing-api`, task "add usage-metering endpoint" (autonomous, gated):**
>
> - **Lead** (Brain-as-coordinator) reads the task + ingested resources (§9), asks the **Planner** to
>   decompose. Planner uses the graph to partition into *schema*, *handler*, *tests* — non-colliding.
> - **Implementer-A** (Claude) takes the handler; **Implementer-B** (Codex, if opted-in) takes the
>   schema migration — **fan-out / Parallel**, each in its own worktree. The migration trips a gate
>   (`blast_radius == schema-migration → human_approval`, §4) → filed as a **human task** (§10).
> - **Tester** writes regressions; **Reviewer** cross-checks both diffs; the **Verifier** runs
>   verify → repair and is the only one that can mark `done`.
> - **Lead synthesizes**, lands results on a worktree branch (**no auto-merge**, §5.1), and
>   **replenishes** (§5.2). Quota rationing (§6) throttles the fan-out width when the weekly pace is
>   tight. The operator watches the whole team from mission-control (§13) or the phone (§14), and can
>   **take over** any member live (§5.3).

---

## 13. Overall Management Layer (mission-control)

The **top-level surface** that aggregates monitor **and** control across **everything** — the single
place the operator steers the whole system from (`DESIGN §8`, decision 25, `HLD §3`). Where the work
board (§17) is per-workspace, mission-control is the **cross-cutting rollup** over all of it.

**What the operator sees (aggregated monitor):**

| Pane | Rollup across |
|---|---|
| **Fleet status** | Every **live session** — which worker, which task, mode (interactive/autonomous), health — across all projects. |
| **Teams** | Every **agent team** (§12): roster, roles, lead, what each member is doing now. |
| **Work rollup** | All **workspaces → projects → tasks** state counts (backlog/ready/running/blocked/done) at a glance. |
| **Global quota gauge** | The §6 objective **system-wide**: 5h burn + weekly pace-vs-actual across **all** workers, overflow guard status. |
| **Human-task inbox** | Decisions/reviews/learning items (§10) aggregated across all scopes. |
| **Approval Inbox** | Auto-detected **`proposed`** tasks awaiting one-tap approve/edit/reject (§5.4, `DESIGN §2` decision 35), aggregated across all scopes — distinct from the active queue. |
| **TMS sync** | Connected-TMS status and drift (§8) for every bridged project. |
| **Notifications & narration** | The activity feed (§10) unified across the fleet; escalations surfaced. |

**What the operator does (aggregated control):**

- Start/stop/pause **autopilot** per any scope (task → project → workspace → whole fleet).
- **Promote / take over** any session live (§5.3); reprioritize the queue; adjust **gates** (§4) and
  **quota policy** (§6) globally or per scope.
- Assign/compose **teams** (§12) onto projects/tasks; answer **human tasks** (§10).
- Clear the **Approval Inbox** — one-tap **approve / edit / reject** of `proposed` tasks (§5.4) to
  gate what enters the active queue.

Mission-control is the **primary surface** and is reachable from laptop **or** phone (§14). It is a
thin client over the same daemon/coordinator API — no new backend (decision 29, delegate-first).

---

## 14. Mobile companion (phone)

A first-class **phone client** over the daemon/coordinator, connectivity-aware (`DESIGN §8`,
decision 26). It is a **thin client** — it exposes the *same* daemon/coordinator API surfaces
(mission-control §13, boards, dashboards, docs), never a second backend (decision 29).

**Always available (both regimes):** monitor **autonomous sessions** + dashboards, **browse the
Library / reading room — read *or* listen** (§16), **read docs & links**, **bookmark** things (§15),
and reach the **TMS** (§8).

**Connectivity determines how much *control* the phone has:**

| Laptop state | Served by | What the phone can do |
|---|---|---|
| **Online** | the local **daemon** (via the coordinator) | **Full orchestration control** — everything mission-control does (§13): start/stop, steer, adjust gates/quota, **promote / take over** sessions (§5.3), **clear the Approval Inbox — approve/edit/reject `proposed` tasks (§5.4)**, compose teams. Plus everything below. |
| **Offline** | the always-on **coordinator** (`§7.1`, decision 23) | **Read / monitor only** — **library browse + read + listen** (cached docs + **pre-generated audio**, §16), docs, links, **bookmarks**, TMS, **last-known state**, **the Approval Inbox (§5.4) read-only**, and **notifications**. No live steering (the workers are asleep); intent — new bookmarks, TMS edits, **and inbox approve/edit/reject decisions** — is **queued** and applied when the laptop returns. |

**Security boundary.** Remote access is **always through the authenticated coordinator** (the
"Always Free" micro instance of §7.1) — **never by exposing the localhost daemon** to the network
(`DESIGN §8, §11`). The coordinator is the auth surface and the offline read/monitor server; when the
laptop comes back online it becomes the relay to the daemon for full control. This reuses the split
topology already in §7.1 rather than inventing new transport (decision 29).

---

## 15. Bookmarks

A lightweight way for the operator to **save things for later** and turn them into work (decision 28).

- **What can be bookmarked.** Docs and links (the primary case), plus references the operator wants
  to revisit — encountered while reading on the phone (§14), browsing narration (§10), or reviewing a
  task's resources (§9).
- **Where they live / sync.** Bookmarks persist in the native store (`DESIGN §9`) and **sync to the
  phone**, so something saved on mobile is there on the desktop and vice-versa. Under the split
  topology (§7.1) the always-on **coordinator** serves bookmarks even while the laptop is **offline**
  (§14).
- **Bookmark → task resource (feeds ingestion).** A bookmark can be **attached to a task as a
  resource** (§2.1), at which point it enters **Resource Ingestion** (§9): fetch → parse → index into
  the Context Engine, with provenance and citations. This is the bridge from "I saw something useful"
  to "an agent works from it, cited." Bookmarking on the phone while offline queues the item; attaching
  it to a task triggers ingestion once connectivity returns.

Bookmarks deliberately **reuse existing machinery** — the native store, the sync/coordinator path, and
the Resource Ingestion pipeline — rather than a bespoke system (decision 29).

---

## 16. Library & Knowledge layer ("Reading Room")

A **read-everywhere knowledge surface** that aggregates every document the system touches — the docs
of **all managed projects**, curated **reading materials**, and the system's **own docs** — into one
browsable, searchable **reading room**, reachable from the desktop and (especially) the **phone**
(`DESIGN §8`, decisions 30–31). Its two jobs: **maximum viewability** (read anything, anywhere,
online or offline) and **learning** (expand the operator's understanding, unbiased and cited).

It is **not a new store or a new brain** — it is a **view + index** over material the system already
holds (project repos, the codebase graph, the artifact/handoff log, ingested resources, bookmarks),
reusing the native store (`DESIGN §9`), the Context Engine's RAG (`INTELLIGENCE §5`), and the
coordinator sync path (§7.1) — delegate-first, invent only on demonstrated need (decision 29).

### 16.1 What the library aggregates

| Source | Includes | Where it comes from |
|---|---|---|
| **Managed-project docs** | Repo docs (README, `docs/`, **ADRs**), **auto-generated docs** (rendered from the **codebase graph** — `INTELLIGENCE §4.1`), **API docs**. | Every project's worktree root (§2) + the graph. |
| **Live system artifacts** | **Plans**, **handoffs** (`HLD §8`), **task write-ups**, verifier reports — the system's own output as it works. | The append-only artifact/event log (§7). |
| **Curated reading materials** | Links, **theoretical books / PDFs**, **introductory guides**, **expert explainers** — the operator's own materials. | Attached by the operator / promoted from bookmarks (§15). |
| **The system's own docs** | `DESIGN`, `HLD`, `LLD`, `INTELLIGENCE`, `WORKFLOW`, `RESEARCH`, plus generated guides. | This doc-set; self-describing. |

**Scoping.** Every library item is scoped **global / workspace / project / task** (mirrors the work
hierarchy §2), so the reading room can show "everything" or narrow to "just this project's docs +
this task's materials." Curated materials attach at any scope (a book global, an RFC to one task).

### 16.2 Search — full-text + RAG, with citations

- **Full-text** over all items (SQLite FTS5/BM25 — `DESIGN §9`) for exact lookups.
- **RAG search with citations** — reuse the **code-aware deep RAG + rerankers** from the Context
  Engine (`INTELLIGENCE §5`): ask a question, get an answer **grounded in cited library items** (the
  same anti-hallucination stance as §9–§10). Because it reuses the code-aware retrieval, a query can
  span prose docs *and* the codebase graph in one search.
- Results **link back to the source item** (provenance) and open in the reader (§16.4).

### 16.3 Overlap with Resource Ingestion & Bookmarks (one pipeline, three doors)

The library is the **read side** of machinery already described — it deliberately shares it
(decision 29):

- **Library items ↔ task resources (§9).** A curated library item can be **attached to a task as a
  resource** (enters the fetch → parse → index ingestion pipeline); conversely, a task's **ingested
  resources are readable in the library**. Same items, two views — a *reading* view here, a
  *working-context* view there.
- **Bookmarks land in the library (§15).** A **bookmark** (a doc/link saved, often from the phone)
  **appears in the reading room** and can be promoted to a curated material or attached to a task.
  *Bookmark → library → task-resource* is one continuous path, reusing the same store + coordinator
  sync.
- **Coordination & Learning (§10).** The library is where the **teach-and-expand** rule (§10,
  decision 11) becomes a browsable feature — see the learning path (§16.5).

### 16.4 Read or listen (TTS / audio)

Every library item renders **two ways** (decision 31) — read it or **listen** to it:

| Mode | What it is |
|---|---|
| **Reading view** | A clean, distraction-free **reader** (readability/markdown extraction — `DESIGN §9`) — consistent typography across repo docs, PDFs, books, and artifacts. |
| **Audio narration (TTS)** | An **optional spoken version** via a **pluggable TTS provider** — so you can **listen on the go** (commute, away from the screen). |

- **Pluggable, free-first, ToS-clean TTS.** The TTS provider is a **candidate / provider**, not a
  locked choice — a **local / free-first** engine is the leading candidate, selected by the
  **Comparison / Eval Harness** (`DESIGN §12.19`) on quality/latency/cost. It **never spends
  overflow / API credits** (the hard rule — decisions 20–21, `DESIGN §11`); narration falls back to
  a local engine rather than a paid one.
- **Cached on the coordinator for offline.** Both the **extracted text** and the **pre-generated
  audio** are **cached on the always-on coordinator** (§7.1) so the phone can **read and listen
  offline** — the workers (and the laptop) can be asleep. Pre-generation is a low-priority
  background job that **never competes with coding quota** (§6).
- **Listen queue.** Queue items (a project's docs, a book, this week's learning path) into a **play
  queue** — narrated back-to-back, resumable, like a podcast.

### 16.5 Curated learning path (the teach-and-expand rule, as a feature)

The Coordination & Learning layer's **teaching** capability (§10, decision 11) surfaces here as a
**curated learning path** — an ordered **introductory → expert** reading/listen sequence over library
items:

- **Ordered & progressive.** Intro guides first, expert explainers and theory later; the system
  tracks what the operator has read / already knows (§10) and **expands the horizon** from there.
- **Unbiased & cited.** Paths are **unbiased** (not steering toward one vendor/answer — §10) and
  every step **cites its sources** (§16.2), so the operator can **verify**, not just trust.
- **Read or listen.** A path is a natural **listen queue** (§16.4) — narrated for offline learning
  on the phone.
- **Provisional.** Curation policy (ordering, what counts as "intro" vs "expert") is a **candidate**,
  tuned by the Eval Harness; nothing here is a fixed syllabus.

---

## 17. UI surfaces

**TUI first (Textual) → desktop later (Tauri + React/TS)**, both thin clients over the same daemon
API (`DESIGN §8, §9`, decision 18):

| Surface | Shows |
|---|---|
| **Mission-control** | The top-level **management layer** (§13): fleet/team/work/quota/TMS rollup + global steering — the primary surface. |
| **Work board** | Workspaces → projects → tasks, states, assignees, priority (Linear-like). |
| **Team view** | An **agent team** (§12): roster, roles, the **lead**, live per-member activity, collaboration pattern. |
| **Activity feed** | Live narration of fleet actions + reasons (§10). |
| **Human-task inbox** | Decisions/reviews/learning items awaiting the user (§10). |
| **Approval Inbox** | Auto-detected **`proposed`** tasks (§5.4, `DESIGN §2` decision 35) with one-tap **approve / edit / reject** — the gate before work enters the active queue; kept distinct from the human-task inbox. |
| **Context / resource inspector** | A task's ingested resources, provenance, citations (§9), and **bookmarks** (§15). |
| **Reading room (Library)** | The Library (§16): all managed-project docs + curated materials + system docs, scoped global/workspace/project/task, with **full-text + RAG search (cited)**. |
| **Reader** | Clean **reading view** of any library item (§16.4); the read-side twin of the resource inspector. |
| **Listen / player** | The **audio-narration player** + **listen queue** (§16.4) — read *or* listen, offline-cached on the coordinator. |
| **Learning-path view** | The curated **introductory→expert** path (§16.5) — unbiased, cited, read or listen. |
| **Efficiency + utilization dashboards** | Human/agent efficiency, **rework-rate** trend, and the **quota gauge** — 5h burn + weekly pace line vs actual (§6, `INTELLIGENCE §11`). |
| **Mobile companion** | The phone client (§14): monitor sessions/dashboards, **browse/read/listen the Library** (§16), read docs/links, bookmark, **approve/edit/reject the Approval Inbox (§5.4)**, TMS — full control when the laptop is online, read/monitor (inbox decisions queued) via the coordinator when offline. |

The utilization dashboard makes the §6 objective legible: you can *see* each 5h window filling and
the weekly reservoir tracking its pace line toward a near-empty reset.

**Client rollout.** TUI first (Textual) → desktop (Tauri + React/TS) → **mobile companion** (§14) and
the **mission-control** rollup (§13) → the **reading room / Library** (§16, M9); every surface is a
**thin client over the same daemon/coordinator API** (decision 18, 29), so no surface adds its own
backend. Live-session **handoff/takeover** controls (§5.3) appear on every surface that has control
rights (desktop, mission-control, phone-when-online).

---

## 18. Milestone alignment

Maps to the roadmap in `DESIGN §10`:

| This doc | Milestone | Delivers |
|---|---|---|
| §2, §9 (native model + ingestion v0) | **M3** — Work Management v1 | Native workspaces/projects/tasks store + board; task authoring; Resource Ingestion v0. |
| §11 (templates) | **M5** — Templates/Feature registry | SDLC templates + Router/Parallel modes. |
| §3, §4, §5, §6 (lifecycle, modes, Autonomy Controller, quota scheduler) | **M6** — Autonomy Controller | Autopilot loop, gates, replenishment, quota rationing. |
| §8 (TMS bridge) | **M6** — TMS Bridge | Linear/Jira/GitHub adapters (candidates). |
| §17 (TUI surfaces) | **M4** — Textual TUI | Work board, activity feed, human-task inbox. |
| §17 (desktop) | **M7** — Native desktop UI | Same surfaces on Tauri + xterm.js. |
| §12, §13, §14, §15, §5.3 (teams, mission-control, mobile, bookmarks, live handoff) | **M8** — Teams + Management + Mobile | **Agent Teams** + **Overall Management Layer**; **mobile companion** (full control laptop-online, read/monitor via the coordinator offline); **bidirectional live session handoff/takeover**; **bookmarks**. |
| §16 (Library & Knowledge layer, read-or-listen) | **M9** — Library & Knowledge layer | All managed-project docs + curated reading materials (links/books/intro/expert explainers) + system docs, **full-text/RAG search with citations**; **read-or-listen (TTS audio)** offline-cached on the coordinator; curated **learning paths**. |
| §7 (durability/auto-resume, split topology) | cross-cutting (M6+) | Boot service, persistent state, auto-resume; optional Oracle-coordinator/local-worker split (the same coordinator the mobile companion §14 and Library §16 lean on for offline read/listen). |
| §5.1 (throw-away min. via verifier + live takeover) | leans on **I3** | Verifier/critic + verify→repair, MoE gate (`INTELLIGENCE.md`); §5.3 takeover is the human escape hatch. |

Autonomy (M6) leans on the intelligence track's verifier (I3) and metrics flywheel (I4); the tracks
interleave (`DESIGN §10`). **M8** builds the teams/management/mobile layer on the M3–M7 spine (the
coordinator from §7.1 is what serves the phone when the laptop is offline). **M9** adds the **Library
& Knowledge layer** (§16) on top — reusing that same coordinator to cache docs + pre-generated audio
for offline **read-or-listen**, and the Context Engine's RAG (I1) for cited search.
