# Agentic Coding Orchestrator — Design Doc

**Status:** Draft for approval · **Date:** 2026-07-24 · **Author:** Claude (with sujal)

A free, ToS-clean **framework over Claude Code, Codex, and Gemini** that drives those official
CLIs to their full potential — in **two modes**: an **interactive co-pilot** you steer live, and a
**full autopilot** that saturates every agent, runs tasks to completion, verifies them, and asks
for (or generates) more work when idle. It is organized around a **native, Linear-like
work-management layer** (workspaces → projects → tasks) that **integrates with your existing TMS
or runs entirely standalone**. Each task carries **reading materials and source links** that the
system ingests as context. On top sits an **intelligence layer** (codebase graph, deep RAG +
rerankers, cross-run memory, self-maintaining guidelines, a verifier, MoE-over-agents routing, and
small trained ML/RL models) that is the real value-add. And a **coordination layer** manages tasks
*for you* — teaching you the tool, narrating what the agents are doing, and turning decisions into
human tasks — so you and the fleet coordinate fully. Wrapped in a UI that shows every agent's
**live terminal** and lets you read, edit, and interrupt anything. It works *with* a fleet of
workers, or entirely on its own.

> **Companion docs:** `HLD.md` (architecture) · `LLD.md` (detailed design) ·
> `INTELLIGENCE.md` (the smart layer) · `WORKFLOW.md` (work-management, TMS, modes) ·
> `RESEARCH.md` (sourced grounding: CLI drivers, auth/ToS, quota model, prior art).

---

## 1. Vision

You should be able to open a workspace, drop in a project, describe what you want (with links and
reading material), and choose *how hands-on to be* — from "watch me approve every step" to "run the
whole backlog and ping me when you need a decision." Behind that, Claude/Codex/Gemini are driven at
full tilt through their official CLIs, made smarter than they are alone by a shared brain (graph,
RAG, memory, guidelines, verifier), and kept honest by metrics that prove the system is getting
better at *your* work over time. The system is also your **teacher and coordinator**: it explains
what it's doing, learns your intent, and manages the human side of the loop so you're never lost.

Three ideas hold it together:

1. **Framework, not glue.** We orchestrate *agents* (CLIs that plan, edit files, run tools), driven
   to full potential, and we add the intelligence and coordination they lack — never reinventing
   what they already do well (§ *evolution principle*).
2. **Work is first-class.** A native work-management model (workspaces/projects/tasks) with a
   continuous supply of work, TMS bridges, and per-task resources — so the fleet is always busy on
   *the right thing*.
3. **Two-way coordination.** The human and the fleet are peers in one loop: you assign and steer;
   the system executes, verifies, narrates, and hands *you* the decisions only you should make.

---

## 2. Goals & Non-Goals

### Goals

- **Drive official CLIs to full potential.** `claude` (Claude Max), `gemini` (Google AI Pro / free
  tier), `codex` (OpenAI), plus optional OpenRouter free models — via their **CLIs only**, on your
  own accounts and machine.
- **Two operating modes (a spectrum, not a switch).**
  - **Interactive co-pilot** — you drive; the system assists, retrieves context, asks before acting.
  - **Autonomous autopilot** — the system pulls ready tasks, assigns the best agent, runs to
    completion, verifies, and **requests/creates more work when idle** to keep the fleet at max
    utilization. Autonomy level is set per workspace/project/task, with gates you configure.
- **Native, Linear-like work management.** **Workspaces → Projects → Tasks** (with subtasks,
  dependencies, priority, status, assignee = agent *or* human). You author tasks and tell the system
  what to do; it schedules and executes them.
- **TMS-agnostic, TMS-optional.** Integrate bidirectionally with **any known/pre-existing TMS**
  (Linear, Jira, GitHub Issues, …) via adapters — *and* run fully standalone with the native store
  when you have none. Your TMS stays the source of truth when present; ours mirrors and augments.
- **Per-task resources & real sources.** Each task can carry **reading materials, attached links,
  and references**; the system **ingests them** (fetch, parse, index) into the context engine so the
  assigned agent works from real, cited sources — not guesses.
- **Continuous supply, max utilization.** When tasks finish, more are pulled/created; idle agents
  are re-tasked; nothing sits blocked silently. "Utilise everything to the max."
- **Human coordination & learning.** The system **manages tasks for you**: it narrates what each
  agent is doing and why, teaches you the tool as you go, surfaces decisions/reviews as **human
  tasks**, and helps you express intent — so you always understand and can course-correct.
- **Configurable orchestration.** Router (split → best/cheapest worker), Parallel (N workers →
  compare/vote/synthesize), Pipeline (fixed stages with gates) — equal peers, chosen per run/template.
- **Readable + interactive handoff.** Every agent runs in a real PTY; watch its live output and
  *type into it yourself*. Handoffs are readable artifacts you can inspect and edit before use.
- **Inline human editability (co-editing).** Edit any artifact (doc, code, handoff) directly; the
  agent **sees your diff** as new context and continues — no re-prompt.
- **Direct messaging — to agents AND the Brain.** Message any running worker, *and* the orchestrator
  **Brain** ("skip review", "use Gemini here", "why this route?"). Two addressable layers.
- **The intelligence layer (the core value).** Codebase graph, deep RAG + rerankers, cross-run
  **memory**, self-maintaining **additive guidelines**, a **verifier/critic**, **MoE-over-agents**
  routing, fresh-context resets, and **small trained ML/RL** (bandit router, outcome predictors,
  offline RL, RLAIF). Detailed in `INTELLIGENCE.md`.
- **Delegate-but-authoritative & evolution-aware.** Probe each CLI's *native* capabilities and use
  them; keep our layer the source of truth; adapt as the underlying CLIs evolve. Don't reinvent the
  wheel. (§7.)
- **Metrics & observability.** Track **human and agent efficiency**; the headline "getting smarter"
  signal is a falling **rework-rate** trend. Metrics are also training data for the ML models.
- **Incremental.** Durable core + thin UI clients: ship a TUI first, a rich desktop UI later, same
  daemon backend.
- **Works without any worker.** Brain + context + memory + guidelines + editor + work-management are
  useful with zero external agents.

### Non-Goals (and why)

- **No headless Antigravity / GUI agents.** Antigravity is a GUI IDE (Codeium-lineage Electron app,
  internal `language_server`) with no supported headless CLI — **dropped**. The Google worker is the
  official `gemini` CLI.
- **No ToS gray areas.** No scraping subscription web sessions, no auth-cookie reuse, no
  multi-account rotation, no limit evasion. One operator (you), your own accounts, official tools,
  published free tiers only. (§9.)
- **Not a model-API gateway.** We orchestrate *agents*, not raw chat completions. Raw-API workers
  (OpenRouter) are an optional adapter and the *fuel* for internal reasoning — not the core.
- **No LLM fine-tuning in v1.** The trained ML/RL is small and real (routers, predictors, offline
  RL, reward models) but we do **not** fine-tune the coding LLMs themselves. (`INTELLIGENCE.md`.)

---

## 3. Core concepts (map)

| Concept | One-liner | Where |
|---|---|---|
| **External coding agents** | Claude/Codex/Gemini CLIs, PTY-wrapped, driven to full potential | HLD §4 |
| **Internal capability agents** | Graph-builder, deep-RAG, clarifier, verifier, distiller — the smart layer | INTELLIGENCE §9 |
| **Orchestrator Brain** | The conductor you chat with; plans, routes, explains | HLD §3 |
| **Work Management** | Native workspaces → projects → tasks (Linear-like) | WORKFLOW §2 |
| **Task Approval Inbox** | Auto-detected/proposed tasks land here for your one-tap approve/edit/reject before they run | WORKFLOW §3, §5 |
| **TMS Bridge** | Bidirectional adapters to Linear/Jira/GitHub Issues…, optional | WORKFLOW §8 |
| **Autonomy Controller** | The autopilot loop: pull → assign → run → verify → replenish | WORKFLOW §5 |
| **Operating modes** | Interactive co-pilot ↔ autonomous autopilot (a spectrum, gated) | WORKFLOW §4 |
| **Resource Ingestion** | Fetch/parse/index a task's reading materials & links | WORKFLOW §9 |
| **Coordination & Learning** | Manages tasks *for you*; narrates agents; **teaches you CS/SWE + the project's tools** (intent→hunt→curate) | WORKFLOW §10 |
| **Orchestration modes** | Router / Parallel / Pipeline run strategies (equal peers) | HLD §3 |
| **Templates / Features** | Composable SDLC workflows; meta-orchestration | HLD §3 |
| **Context Engine** | Deep RAG + rerankers + graph-expand + fresh-context | INTELLIGENCE §4 |
| **Codebase Graph** | tree-sitter nodes/edges; repo-map; blast-radius | INTELLIGENCE §3.1 |
| **Memory Store** | Cross-run decisions/conventions/preferences/failures/facts | INTELLIGENCE §3.2 |
| **Guidelines Engine** | Additive, self-proposed, human-editable rules → AGENTS.md | INTELLIGENCE §3.3 |
| **Verifier / Critic** | Independent verification; also the RLAIF reward model | INTELLIGENCE §7 |
| **MoE-over-agents** | Contextual-bandit gate: sparse route vs ensemble+judge | INTELLIGENCE §6 |
| **Reasoning Provider** | Use Claude/Gemini/Codex CLIs (or free API / local) as the LLM for smart tasks | INTELLIGENCE §8 |
| **Capability Registry** | Probe & version each CLI's native features; delegate-first | HLD §6 |
| **Interaction Layer** | Uniform question/approval interface for question-less CLIs | HLD §7 |
| **Handoff Bus / Co-editing** | Readable, editable inter-stage artifacts; diff-back | HLD §3 |
| **Adaptation Engine + Metrics** | Human/agent efficiency; rework-rate; ML training data | INTELLIGENCE §10–11 |
| **Agent Teams** | Named groups of collaborating agents/workflow-peers with roles + a lead; templated | HLD §4.1, WORKFLOW §12 |
| **Overall Management Layer** | Top-level mission-control across all workspaces/projects/teams/sessions/quota | HLD §3, WORKFLOW §13 |
| **Mobile Companion** | Phone client: monitor, docs/links/bookmarks/TMS; full control online, read/monitor offline | HLD §3, WORKFLOW §14 |
| **Live Session Handoff** | Promote interactive→autonomous; take over autonomous→interactive, mid-flight | WORKFLOW §5.3 |
| **Bookmarks** | Save docs/links/things; sync to phone; attach to tasks | WORKFLOW §15 |
| **Library & Knowledge layer** | All managed-project docs + curated reading materials/books/explainers; searchable | WORKFLOW §16, HLD |
| **Read-or-Listen (TTS)** | Every doc/material has a reading view + optional audio narration; offline-cached | WORKFLOW §16, HLD |
| **CS/SWE Learning Companion** | Turns plain-language project intent into curated learning: theory + practice + the tools/libraries in play | WORKFLOW §10, INTELLIGENCE §10 |
| **Learner Model (personalization ML)** | Small models trained on your/project/agent data to learn what you understand & what works | INTELLIGENCE §11 |
| **User-State model** | Infers your current state from *how you communicate* (off ↔ to-the-point) → prioritization + comms calibration, not autonomy scope | INTELLIGENCE §11 |
| **Authorship provenance** | Tracks who wrote what (human vs which agent) across messages/edits/context | HLD §3, INTELLIGENCE §11 |

---

## 4. Two classes of agent

- **External coding agents — Claude Code, Codex, Gemini.** CLI-only, each wrapped in a PTY. They do
  the heavy coding. You watch their live terminal and can take over. Driven to full potential via
  their native features (structured I/O, resume, subagents, MCP) probed by the Capability Registry.
- **Internal capability agents — graph-builder, deep-RAG retriever/reranker, clarifier, verifier,
  distiller.** In-process, fuelled by the **Reasoning Provider** (a CLI, a free API, or local
  model). They make the system smart and fill CLI gaps. They run even with **zero external agents**.

The worker contract therefore supports **both PTY sessions and API sessions** from day one.

**Agent teams.** Agents (external + internal + workflow peers) compose into **teams** — named groups
with roles (e.g. planner, implementer(s), reviewer, verifier, tester) and a **lead/coordinator**,
sharing context and memory, templated per SDLC. Teams are the unit the Orchestration Engine assigns
to a project/task; they lean on workflow **agent-peers** heavily (fan-out, cross-check, synthesize).
An **overall management layer** (mission-control) sits above all teams/workspaces/sessions — the
single surface you monitor and steer from (laptop or phone). See HLD §3–4 and `WORKFLOW.md`.

---

## 5. Operating modes (interactive ↔ autonomous)

Autonomy is a **spectrum**, set per workspace/project/task, with gates you choose:

- **Interactive (co-pilot).** You drive. The system retrieves context, drafts, and *asks before
  acting*. Every step is watchable and interruptible. Good for exploration and high-stakes work.
- **Assisted.** The system proposes the next task and plan; you approve; it executes; you review.
- **Autonomous (autopilot).** The **Autonomy Controller** pulls ready tasks from the queue, assigns
  the best agent (MoE gate), runs to completion, **verifies** (verify→repair loop), commits/handoffs,
  and **replenishes work** — pulling the next task or generating follow-ups so the fleet stays at max
  utilization. It escalates to you only on configured gates (ambiguity, risk, verification failure,
  cost thresholds). "If tasks finish, more get created; nothing idles silently."
- **Live handoff (both directions).** Any running session can be **promoted interactive→autonomous**
  (hand it to the Autonomy Controller with a goal + gates and walk away) and **taken over
  autonomous→interactive** (seize the live terminal, drive by hand, hand back) — mid-flight, by
  swapping the session's driver and permission/gate policy. This is the live-session twin of the
  modes spectrum.

Gates are declarative (e.g. "human-approve any migration", "auto-merge if verifier passes and diff
< N lines"). The same run can mix modes per stage. Details in `WORKFLOW.md §3, §5`.

---

## 6. Work management & TMS (Linear-like, TMS-optional)

- **Native hierarchy.** **Workspace → Project → Task** (+ subtasks, dependencies, priority, status,
  labels, assignee = agent or human). A Linear-like model you author into. This is where you "tell
  what to do."
- **Task = intent + workflow + resources.** Each task has a description, an assigned **workflow /
  template** (how it should be executed), and **resources**: reading materials, attached links, and
  references to real sources. On execution, resources are **ingested** into the context engine so the
  agent works from cited material.
- **Continuous supply.** A backlog/queue feeds the Autonomy Controller; you top it up ("I will
  create more tasks"), or the system proposes follow-ups from verifier findings and gaps.
- **TMS Bridge (optional, bidirectional).** Adapters sync the native store with **any known TMS**
  (Linear, Jira, GitHub Issues, …): import issues as tasks, push status/results back. When you have
  a TMS it stays source-of-truth and ours mirrors/augments; when you don't, the native store *is* the
  system of record. **Works with any, works without any.**
- **Human tasks.** The queue holds tasks for *you* too — decisions, reviews, learning items — so the
  system coordinates the human side, not just the agents.

Full model, states, adapter interface, and scheduler in `WORKFLOW.md`.

---

## 7. Reasoning Provider & the evolution principle

- **Reasoning Provider.** Intelligence tasks (planning, reranking, clarifying, verifying,
  distilling) need an LLM. Rather than a hard dependency, a **smart-pick** provider chooses per call
  among: a **coding CLI itself** (Claude/Gemini/Codex used as an LLM), a **free API** (OpenRouter/
  Gemini), or a **local model** — by cost, latency, quota, and quality. So Claude/Codex/Gemini are
  both the *workers* and, when apt, the *engine of our intelligence*.
- **Evolution principle (don't reinvent the wheel).** The CLIs evolve fast and already ship real
  capabilities (memory files, MCP, subagents, web search, structured output). A **Capability
  Registry** *probes* each CLI (version-aware) and we **delegate to native features first**, keeping
  our layer **authoritative** (source of truth) and shimming only what's missing. When a CLI gains a
  feature we shim, we switch to delegating. (HLD §6.)

---

## 8. UI & interaction model

- **Live terminals.** Every agent runs in a PTY; clicking an agent shows its **real terminal UI**
  (e.g. Claude's actual TUI), streamed live. You can type straight into it — you and the Brain are
  two input sources into the same PTY.
- **Two addressable layers.** Message the **Brain** (orchestration intent) or a **worker** directly.
- **Integrated editor & co-editing.** A first-class in-app **editor** lets you open and edit **any
  file** (code, docs, artifacts, handoffs) **then and there** — Monaco in the desktop app, an editor
  pane in the TUI. A watcher captures your change as a **diff** and feeds it to the relevant agent as
  context, so the agent **sees your edit and continues — no re-prompt**. Human vs agent edits are
  attributed separately; you can edit while a session is paused *or* mid-run.
- **Uniform questions/approvals.** The Interaction Layer surfaces every agent's questions/approvals
  through one consistent prompt — even for CLIs that don't natively ask.
- **Coordination surfaces.** A work board (workspaces/projects/tasks), an activity narration feed
  ("what the fleet is doing and why"), human-task inbox, context inspector, and efficiency
  dashboards. TUI first; rich desktop later.
- **Overall management layer (mission-control).** A top-level surface aggregating monitor + control
  across **all** workspaces/projects/teams/sessions/quota/TMS — the single place you steer from.
- **Mobile companion (phone).** A thin client for **monitoring everything**: live autonomous
  sessions, dashboards, **docs & links (read + bookmark)**, and your **TMS**. **Laptop online →
  full orchestration control** (start/stop, steer, promote/take-over sessions); **laptop offline →**
  the always-on **coordinator** serves read/monitor (docs, links, bookmarks, TMS, last-known state,
  notifications). Remote access is through the authenticated coordinator — never by exposing the
  local daemon.
- **Library & reading room (read *or* listen).** Browse **all managed projects' docs** (repo docs,
  auto-generated docs, plans/handoffs) plus curated **reading materials** — links, theoretical
  books/PDFs, introductory guides, expert explainers — and the system's own docs. Every item has a
  clean **reading view** and optional **audio narration (TTS)** so you can read or **listen** on the
  go; text + audio are **cached on the coordinator for offline**. A curated **learning path**
  (introductory→expert, unbiased, cited) helps expand your understanding.
- **Live session handoff & takeover.** Promote a watched session to autonomous, or seize a running
  autonomous session to drive by hand — from either desktop or phone.
- **Incremental UI.** Textual TUI now → Tauri + React/TS desktop later (ReactFlow graph, xterm.js
  terminals, Monaco co-edit, visx dashboards), both thin clients over the **same daemon API**.

---

## 9. Recommended stack

- **Core/daemon:** Python 3.12 + asyncio (PTY control, subprocess fan-out, AI tooling).
- **Transport:** WebSocket + JSON lines (`websockets`), localhost only.
- **PTY:** stdlib `pty` + `asyncio.add_reader`.
- **Model/API:** `httpx` async (SSE) for Reasoning Provider, embeddings, rerank.
- **Code parsing:** tree-sitter (multi-language codebase graph).
- **Retrieval:** hybrid dense (`sqlite-vec` / `fastembed`/ONNX) ⊕ lexical (SQLite FTS5/BM25), RRF.
- **Persistence:** SQLite (runs, sessions, events, handoffs, metrics, graph, chunks, memories,
  guidelines, verifications, caches, **workspaces/projects/tasks/resources**).
- **Isolation:** git worktrees.
- **Resource fetch:** `httpx` + readability/markdown extraction for attached links.
- **TUI:** Textual. **Desktop:** Tauri + React + TS (ReactFlow, xterm.js, Monaco, visx).
- **ML/RL:** numpy/scipy for bandits; small PyTorch for predictors/offline-RL when trained.

---

## 10. Roadmap

**Build track (makes it work):**

| Milestone | Delivers |
|---|---|
| **M0** | Core skeleton: daemon, worker contract, **Claude** PTY adapter, Brain v0, CLI test client, file transcripts. Chat the Brain, run a task, take over the terminal. Proves the spine. |
| **M1** | **Gemini + Codex** adapters, **OpenRouter** ApiSession worker, Workspace Manager (git worktrees), Capability Registry v0 (probe CLIs). |
| **M2** | Interaction Layer (sentinel + clarifier), Handoff Bus, Edit/Diff Sync, SQLite state, Pipeline mode. |
| **M3** | **Work Management v1**: native workspaces/projects/tasks store + board; task authoring; Resource Ingestion v0. |
| **M4** | Textual TUI (panes, address brain/agent, unified questions, inline edit, handoff approval, work board, activity feed). |
| **M5** | Templates/Feature registry + Router + Parallel modes; SDLC templates. |
| **M6** | **Autonomy Controller** (autopilot loop, gates, replenishment) + **TMS Bridge** (Linear/Jira/GitHub adapters). |
| **M7** | Native desktop UI (Tauri + xterm.js) on the same daemon. |
| **M8** | **Agent Teams** + **Overall Management Layer**; **Mobile companion** (monitor/docs/links/bookmarks/TMS; full control when laptop online, read/monitor via the always-on **coordinator** when offline); **live session handoff/takeover** (both directions). |
| **M9** | **Library & Knowledge layer** — all managed-project docs + curated reading materials (links/books/intro/expert explainers), full-text/RAG search with citations — with **read-or-listen (TTS audio)**, offline-cached; curated **learning paths**. |

**Intelligence track (makes it smart — interleaved, see `INTELLIGENCE.md`):**

| Increment | Delivers |
|---|---|
| **I0** | Reasoning Provider + Capability Registry (delegate-first). |
| **I1** | Codebase Graph + deep RAG + two-stage rerank + fresh-context. |
| **I2** | Memory Store + Guidelines Engine (additive, human-editable → AGENTS.md). |
| **I3** | Verifier/Critic + verify→repair; MoE-over-agents (bandit gate). |
| **I4** | Metrics flywheel → trained ML/RL (bandit router, predictors, offline RL, RLAIF); embedding adaptation. |

Each milestone/increment is independently usable. Build and intelligence tracks interleave (e.g.
I1 lands around M3–M5; the Autonomy Controller in M6 leans on I3's verifier).

---

## 11. ToS & ethical boundaries (the line we hold)

- ✅ Driving **official CLIs** headlessly on **your own machine, your own accounts**, one operator.
- ✅ Using **published free tiers** within their rate limits (Gemini free, OpenRouter free).
- ✅ Fetching **reading materials / links you attach** for task context (respecting robots/access).
- ✅ Syncing with **TMSes you own/are authorized to use** via their official APIs.
- ❌ Reverse-engineering claude.ai / Gemini-app web sessions or reusing auth cookies as a free API.
- ❌ Treating a subscription as unlimited API quota; multi-account rotation; limit evasion.
- ⚠️ Respect each CLI's rate limits — the engine backs off, never hammers, never parallel-spams a
  single subscription to dodge caps. Autopilot honors the same caps as interactive mode.

---

## 12. Decision log (locked)

1. **External agents = CLI only:** Claude Code, Codex, Gemini. Antigravity/GUI agents **dropped**.
2. **ToS-clean:** official CLIs, your accounts/machine, published free tiers. No scraping/rotation.
3. **Stack:** Python async core/daemon · **Textual TUI first** · **Tauri + React/TS** desktop later,
   same daemon API.
4. **Worker contract spans PTY *and* API** sessions from day one (Claude/Gemini/Codex + OpenRouter).
5. **Two-class agent model:** external coding CLIs + internal capability agents.
6. **Orchestration modes** (Router/Parallel/Pipeline) are **equal peers** — no single default.
7. **Two operating modes** — interactive co-pilot and autonomous autopilot — as a **gated spectrum**,
   set per workspace/project/task.
8. **Native Linear-like work management** (workspaces/projects/tasks) is first-class; the fleet is
   kept at **max utilization** via continuous supply + replenishment.
9. **TMS-agnostic & TMS-optional:** bidirectional adapters to any known TMS; fully standalone too.
   TMS is source-of-truth when present; native store otherwise.
10. **Per-task resources** (reading materials + links) are **ingested** into the context engine.
11. **Coordination & learning layer:** the system manages **human tasks**, narrates agent activity,
    and teaches the tool — two-way coordination.
12. **Reasoning Provider** smart-picks among CLI / free API / local for intelligence tasks.
13. **Delegate-but-authoritative + evolution-aware** via the Capability Registry; don't reinvent.
14. **Intelligence layer is the core value:** graph, deep RAG + rerankers, memory, additive
    guidelines, verifier, MoE-over-agents, fresh-context.
15. **Real but small ML/RL:** bandit router + outcome predictors + offline RL + RLAIF (verifier as
    reward model). **No LLM fine-tuning in v1.**
16. **Inline human editability** (co-editing; agent sees the diff) and **direct messaging** to both
    agents and the Brain.
17. **Metrics track human + agent efficiency;** falling **rework-rate** is the headline "smarter"
    signal and feeds the ML models.
18. **Incremental:** durable core + thin clients; each milestone independently usable.
19. **No premature component decisions.** Models, codebase-graph, embedders, rerankers, retrieval
    strategies, and routing policies are **pluggable providers**; a first-class **Comparison / Eval
    Harness** picks winners by head-to-head benchmark, empirically and later. (Corollary: `codegraph`
    and any named tool are *candidates*, not choices.)
20. **Quota rationing objective.** Fully utilise the perishable **5-hour** windows (burn each cycle
    as much as possible) while **pacing the weekly cap** so it lasts the whole week and drains almost
    completely just before reset — a reservoir/token-bucket controller. **Never spend overflow/API
    credits.** Regime-aware (one bucket today; two if the SDK-credit split un-pauses).
21. **All three workers run both interactive + headless**, each **rationed to its own message/rate
    limits** (Codex/Gemini included, per user opt-in; Claude the safest unattended default).
22. **Periodic self-maintenance agents** (internal): an **Updater/Watcher** (track evolving CLI/
    model/capabilities → Capability Registry) and a **Self-Diagnostic** (read internal error logs,
    debug failures). Dogfooded on this system first; generalizable to managed codebases later.
23. **Durable, auto-resuming, power-loss-tolerant.** The daemon runs as a boot/login service with
    persistent state; on restart it **auto-resumes autopilot** (re-dispatches interrupted tasks,
    resumes/forks sessions where possible) **unless settings disable it**. Deployment is flexible:
    single-machine today; optional **split topology** later — a lightweight always-on **coordinator**
    (queue/state/scheduler/watchdog/notifier) on the user's **free Oracle Cloud "Always Free" micro
    instance**, with heavy coding agents on the local (electricity-dependent) machine where the
    subscription + compute live; when local is off, the coordinator persists intent and resumes
    dispatch when it returns. (Topology decided later by comparison; not locked.)
24. **Agent Teams are first-class.** Named groups of external + internal + **workflow-peer** agents
    with roles (planner/implementer(s)/reviewer/verifier/tester…) and a **lead/coordinator**, sharing
    context/memory, **templated per SDLC**; composed by the Orchestration Engine as a teams layer over
    Router/Parallel/Pipeline. Workflow agent-peers are used heavily (fan-out, cross-check, synthesize).
25. **Overall Management Layer (mission-control).** A top-level control plane aggregating state +
    control across all workspaces/projects/teams/sessions/quota/TMS — the primary surface the operator
    monitors and steers, from laptop or phone.
26. **Mobile companion, online/offline-aware.** First-class phone client over the daemon/coordinator:
    monitor autonomous sessions + dashboards, read docs & links, **bookmark** things, reach the TMS.
    **Laptop online → full orchestration management;** **laptop offline → the always-on coordinator
    serves read/monitor** (docs/links/bookmarks/TMS/last-known state/notifications). Remote access is
    via the **authenticated coordinator**, never by exposing the localhost daemon.
27. **Bidirectional live session handoff.** Any running session can be **promoted
    interactive→autonomous** and **taken over autonomous→interactive** mid-flight, by swapping its
    driver + permission/gate policy.
28. **Bookmarks.** The operator can bookmark docs/links/resources; bookmarks sync to the phone and can
    attach to tasks (feeding Resource Ingestion).
29. **Utilise existing tools; invent only on demonstrated need.** Delegate to what exists (native CLI
    features, installed tools); build a new capability only when the Comparison/Eval Harness or a real
    gap shows nothing adequate exists.
30. **Library & Knowledge layer ("reading room").** Aggregate and make viewable everywhere (esp.
    phone) the docs of **all managed projects** (repo docs, auto-generated docs, plans/handoffs/
    artifacts), the system's own docs, and curated **reading materials** — links, theoretical
    books/PDFs, introductory guides, expert explainers. Max viewability + full-text/RAG search with
    citations. Overlaps Resource Ingestion + Coordination & Learning (library items ↔ task resources
    ↔ bookmarks).
31. **Read or listen (TTS/audio).** Every doc/material renders to a clean reading view AND an optional
    **audio narration** via a **pluggable TTS provider** (local/free-first candidate, harness-decided,
    no overflow credits); text + audio are **cached on the coordinator for offline** read/listen on
    the phone, with a listen queue and a curated **learning path** (introductory→expert, unbiased,
    cited) — the teach-and-expand rule as a product feature.
32. **Personalized CS/SWE education (not just the tool).** The Coordination & Learning layer teaches
    the operator **computer science & software engineering broadly** — theoretical + practical
    concepts *and* the specific tools/libraries used in each project. Loop: you send plain-language
    intent ("what I want to do") → the system **hunts** the relevant concepts/tools/libraries/
    materials → presents a **curated need** (a focused reading/learning set, read-or-listen) → you
    learn. **Push notifications** enabled; autonomous agents may **escalate to you, but only when
    needed**.
33. **Learner/personalization ML (trained on our own data).** Small models (no LLM fine-tuning) train
    on **our own data — the project, your behavior/understanding, and the agents' outcomes** — to
    learn *what works better*: a **learner model** (what you know / what to teach next) and a
    *what-works* preference model feeding curation, MoE routing, and guidelines. Extends decision 15;
    detailed in `INTELLIGENCE.md`.
34. **Main-workhorse is a worker-agnostic role, not a fixed identity.** No worker is hardwired as
    "the" primary. The workhorse role is filled at runtime by **availability + capability + quota +
    user config** (and, when trained, the MoE router) — any of Claude / Codex / Gemini / a future
    worker can be it. Claude is merely the **current default** (`config.DEFAULT_WORKER`, env-
    overridable) because it is the only installed worker with a sanctioned headless-subscription path
    (RESEARCH §2) — a runtime default, **not** an architectural privilege. All "Claude is the safest
    default for unattended saturation" statements are current-state facts about auth posture, not a
    lock; swap the default freely.
35. **Auto-detected tasks + a human approval inbox.** New tasks don't require manual authoring — the
    system **automatically detects/proposes** them (from verifier findings, code gaps/TODOs, failures,
    follow-ups from finished work, and ingested materials) — but they do **not** auto-run. They land
    in a dedicated **approval inbox** as **`proposed`** tasks where you **approve / edit / reject**
    (one-tap, reachable from the management layer and the phone) before they enter the active queue.
    Detection is automatic; the *gate* is yours. Complements decision 8 (continuous supply) and the
    autonomy gates (decision 7). **Task origin** is either **human-authored** (you create it → goes
    straight to the active queue, *no self-approval*) or **auto-proposed** (system-detected → waits in
    the inbox); you may also drop a rough **proposal** the system refines into a task for your
    approval. The inbox gate applies **only to auto-proposed tasks**, never to ones you authored.
36. **Asymmetric agents & human variability — absorb variance, don't depend on it.** Agent capability
    is fixed-or-rising; the human's capacity, attention, availability, and skill **vary** (energy,
    focus, time, mood, learning curve, life). The system must **not** treat the human as a constant
    oracle or **hard-block** on them: questions/approvals **queue** (never stall runnable work), the
    load is **right-sized and right-timed** to the human's current state, and the system
    **re-prioritizes** *which* already-autonomous work to run and *what* to surface — it does **not**
    widen autonomy scope. Low or varying energy **never** makes the system do *more* on its own;
    autonomy stays governed by the gates/design (it only ever runs work **already designated
    autonomous**), and the system re-orders + re-surfaces accordingly. It **degrades gracefully** when
    the human is unavailable (durable auto-resume). The **learner model** tracks fluctuating
    capacity/engagement (not just knowledge) — strictly to *serve* the human, human-controlled, **not
    surveillance**. Human-efficiency metrics model **variance**, not a fixed baseline (a low-energy
    week ≠ "getting worse").
37. **The code harness is not the solution for everything.** Not every task is a coding task. The
    router/classifier can conclude a task needs **human judgment/decision, an external or manual
    action, research/reading, design, or a non-code tool** — and route there (a **human task**, an
    internal **reasoning/research** agent, an **external tool**, or a **decide/clarify** step) instead
    of forcing a code agent. **Abstention/escalation is a first-class routing outcome**, not a failure;
    when nothing adequate exists, propose **inventing** a capability (decision 29) rather than faking
    it with the harness. Avoid the "everything looks like a nail" trap.
38. **User-state model (reads *how* you communicate).** A model infers the operator's *current state*
    from **how they're communicating** — message style, clarity, focus, verbosity: are they **"very
    off"** (scattered) or **"very to the point"** (sharp)? This state drives **prioritization** (what
    to tee up / defer) and **communication calibration** (how much to explain, when to ask, how to
    phrase) — **not** a change in autonomy scope (decision 36). Distinct from the CS/SWE
    *knowledge* learner-model (decision 33): this models **state/engagement**, not knowledge.
    Human-controlled, transparent, **not surveillance**.
39. **Authorship provenance (human vs agent, everywhere).** The system always tracks **who wrote
    what** — the human vs. *which* agent — across messages, edits, artifacts, and shared context. When
    the orchestrator launches an agent, that agent and every layer can **see what came from another
    agent vs. from the human**. Essential because: the user-state model (decision 38) must read
    **only the human's own words**, not agent output; co-editing/diff-sync attribution (decision 16)
    generalizes to *all* content; and agents must not mistake another agent's (or the human's) text
    for ground truth. Provenance is first-class, not inferred after the fact.
40. **ML-first; use the LLM only when necessary.** The intelligence signals — user-state detection
    (decision 38), the learner model / knowledge tracing (decision 33), MoE routing, outcome
    prediction, prioritization, rot/anomaly detection — are **real trained ML/RL/deep-learning models**
    (small, local/on-device), **not** LLM prompts. Reserve the **Reasoning Provider** (an LLM — it
    spends perishable subscription quota, decision 20) for what genuinely needs language
    understanding/generation (planning, listwise rerank of *critical* sets, clarifying, verifying,
    distilling). Default to a **cheap learned model or heuristic**; escalate to the LLM only when it
    clearly earns its cost. **And when an LLM *is* needed, pick the *correct* one:** the Reasoning
    Provider (decision 12) prefers **local / free models when adequate** (both first-class; expect more
    such options over time), reserving the premium subscription CLI for what truly needs it. Faster, quota-frugal, and *the* "real ML being trained" (extends decision
    15) — still **no LLM fine-tuning** in v1.
41. **Anti-clutter — correct context, not maximal (for humans *and* agents).** Clutter harms both the
    operator and an agent's context window; each needs the **right, minimal-sufficient** context, not
    everything. This is *why* the intelligence layer exists: RAG + rerankers + graph-expand +
    budget-fit + distiller + memory + fresh-context resets deliver **curated, scoped** context to
    agents; human-facing surfaces (narration, dashboards, inbox, learning) stay **concise and
    uncluttered** — show what matters, hide the rest, one thing at a time. Minimalism is a first-class
    objective, not a nicety.
42. **Runtime debugger & maintainer role.** The system is not just a coding agent: when the
    application runs somewhere, agents connect to the **live system** — infra, databases, logs,
    observability — to debug correctly. The *connecting* is delegated (native shell tools; MCP
    servers for DBs/Kubernetes/observability — all three CLIs are MCP clients); we build what no
    harness has: a per-project **environment/connection registry** (dev/staging/prod endpoints,
    credentials, access policy), **environment-scoped autonomy gates** (production read-only by
    default; any mutation is a human-approval task), and the **maintainer loop** (runtime signals →
    auto-proposed tasks in the approval inbox → diagnose → fix → verify against the live system) —
    the decision-22 Self-Diagnostic generalized to managed applications. Breakpoint-level (DAP)
    debugging is a shim/candidate for the Eval Harness. (`CAPABILITIES.md §5`.)
43. **Portfolio-wide dependency graph.** Working across multiple dependent and independent projects
    is where the operator's cognitive load peaks — the *system* carries it (extends decisions 36,
    41). Dependencies are first-class at every level and across boundaries: task↔task within *and
    across* projects, plus project↔project relations (consumes / shares-infra / independent). One
    DAG spans all workspaces; the scheduler pulls **portfolio-globally** (critical-path aware —
    unblocking work that gates many downstream tasks outranks local nice-to-haves). The multi-repo
    codebase graph auto-*proposes* dependency edges and follow-ups from cross-repo blast-radius —
    into the approval inbox, never auto-applied. **Authority:** operator-set edges are law (never
    reordered or overridden; agents cannot mutate the DAG); system-detected edges are proposals;
    the system *validates* (cycles/contradictions are flagged as questions to the operator, never
    silently fixed). (`CAPABILITIES.md §6`.)
44. **Consistency & no-churn; unbuilt = explicitly blocked.** Once a surface (command, state,
    behavior) is **declared "shipped"**, its semantics never silently change — any change is a
    deliberate, logged migration decision. The declaration is the start line (operator ruling
    2026-07-27): pre-v1 and explicitly-experimental surfaces may still evolve freely. Features that
    are not built yet appear as a visible **`blocked` / not-built-yet** state (board, UI, CLI) —
    never faked with provisional stand-in behavior that would train the operator wrong. Evolution
    is **additive** (new states/commands/panes that were previously `blocked`) — decision 13's
    shim-retirement handles *harness* gaps; this governs *our own* surfaces. (`CAPABILITIES.md §7`.)
45. **Review-load policy: unbounded queue, risk-profiled triage, ask-to-auto-merge.** The fleet is
    never throttled below quota because of the operator's review backlog (utilization objective,
    decision 20): finished work queues **without limit** and the operator catches up on their own
    rhythm (decision 36 — queue, don't stall). In exchange the system maintains a **risk profile**
    for every finished diff (size, blast-radius class, verifier signal, task class) and **triages
    the review queue** (highest-leverage first, low-risk batched); for low-risk items it may
    **propose auto-merge** — a one-tap ask in the approval inbox, **never a silent merge**.
    (Operator ruling 2026-07-27; `CAPABILITIES.md §8`.)
46. **Model Capability Matrix (per-model × task-type × task-stage).** Routing needs a *proper
    understanding of model capabilities* below worker granularity: an explicit matrix scoring each
    available **model tier** (Claude Opus/Sonnet/Haiku/Fable; Codex GPT tiers; Gemini Pro/Flash —
    all three CLIs expose model/effort selection flags) against **task types** and **task
    parts/stages** (plan, implement, review, test, docs, …) on capability, cost, and quota draw.
    **Seeded** from cited published benchmarks/model cards; **learned** thereafter from our own
    outcomes — verifier scores and the operator's accept/reject/rework as the preference signal
    (the RLAIF/reward-model loop, `INTELLIGENCE §7, §11`). This trains the **matrix and routing
    policy, not the LLMs** — decision 15 stands; "RLHF" here means preference-learning for
    routing. The matrix is a **visible, human-editable artifact** (the operator can pin or
    override any cell); the MoE gate (decisions 14, 40) consumes it to pick the worker *and* the
    model-within-worker per stage. (Operator addition 2026-07-27.)
47. **The product is named "Podium"** (operator choice, 2026-07-27) — where the conductor stands
    and sees everything: the mission-control identity. Repo, CLI command (`podium`), and product
    identity; per decision 44 the name is picked once and kept.
48. **Operator-identity commits, everywhere.** All commits — in Podium's own repo and in every
    managed repo — are authored under the **operator's name and email**, never a Claude/agent/
    orchestrator identity, and carry **no AI co-author trailers** unless the operator opts in.
    Podium *enforces* this: worker sessions get the operator's git identity injected
    (config/env/commit-template) and the verifier/pre-merge gate rejects commits with wrong
    authorship. Authorship *provenance* is still tracked internally (decision 39) — the system
    always knows which agent produced what — it just isn't written into public git metadata.
    **Extended 2026-07-29 to all public artifacts:** PR titles/bodies, issues, review comments,
    changelogs, release notes — no "Generated with …" badges or AI attribution of any kind,
    enforced by Podium's templates and the pre-merge/publish gate (ADR-0004).
49. **Quota is read, not estimated** (operator ruling, 2026-07-27, supersedes the "estimated
    gauge" language). The operator can see real quota numbers on every service; Podium reads the
    same authenticated, own-account surfaces rather than estimating: Claude — the CLI's `/usage`
    (PTY-parse) and the account's own OAuth usage endpoint that backs it (read-only, own account,
    one operator — ToS-clean reading; the Capability Registry verifies the exact surface per
    version); Gemini — `/stats` plus exact client-side request/day + RPM counting; Codex —
    `/status` + app-server rate-limit/credit methods. The metering ledger stays for attribution,
    history, and reconciliation against the read numbers — not as a substitute for them. If a
    read surface breaks, the gauge says **`unavailable`** (decision 44: never fake) until the
    Updater/Watcher re-probes.
50. **Peer harness calls — cross-vendor at any level, always brokered** (operator ruling,
    2026-07-29). A running worker session may call on another vendor's harness when available and
    needed, via a Podium **`peer` MCP tool** that expresses a **need, not a vendor choice**; every
    peer request goes through the routing gate — metered by the Quota Scheduler, provenance-tagged
    (decision 39), depth-capped — and **direct harness→harness calls are blocked by policy**.
    Intra-vendor fan-out (a harness's own native subagents) is likewise **monitored**: native
    hooks/OTEL feed a live subagent tree in mission-control (decision 25); no nested agent
    activity, cross-vendor or intra-vendor, is a black box. Long form + risk analysis in
    `CAPABILITIES.md §10`.
51. **Transparent operator models — visibility & the no-ceiling rule** (operator ruling,
    2026-07-29). Everything the system believes about the operator — learner-model estimates
    (decision 33) and user-state readings (decision 38) — is **inspectable with its evidence,
    editable, pinnable, and resettable**; operator edits are authoritative. Estimates **sequence
    and scaffold, never gate**: a low estimate expands support and never hides, locks, or
    withholds material, tasks, or ambition; an **effort override** ("show me the expert path
    anyway") is always one action away and is itself first-party learning signal. Long form in
    `CAPABILITIES.md §11`.

---

## 13. Document map

- **DESIGN.md** (this) — vision, goals, decisions, roadmap.
- **HLD.md** — architecture: containers, agent classes, worker contract, Interaction Layer,
  Capability Registry, data flows, tech choices, milestone→component map, risks.
- **LLD.md** — modules, class/interface specs, wire protocol, SQLite schema (incl. work-management &
  intelligence tables), algorithms, sequences, test plan.
- **INTELLIGENCE.md** — the smart layer: graph, RAG toolkit + rerankers, memory, guidelines,
  verifier, MoE, Reasoning Provider, metrics catalog, ML/RL systems, embedding adaptation, research
  spikes.
- **WORKFLOW.md** — work management: workspaces/projects/tasks model, operating modes, TMS bridge,
  Autonomy Controller (quota rationing, gates, throw-away minimization), resource ingestion,
  coordination & learning.
- **RESEARCH.md** — sourced grounding & prior-art positioning: how to drive each CLI, subscription
  auth + ToS matrix, the quota/utilization model, reuse-vs-build, tools to leverage. Link-rich for
  verification.
- **CAPABILITIES.md** — verified July-2026 inventory of what each harness natively ships
  (cross-harness matrix), the delegate/shim/build/de-scope verdict per component, corrections to
  RESEARCH facts, and decisions 42–45 in detail.
- **V1.md** — the bootstrap cut: the smallest self-hosting loop (dogfood-first), with visibility as
  a v1 invariant; what's in, what's deferred, definition of done.
