# High-Level Design (HLD) — Agentic Coding Orchestrator

**Status:** Draft for approval · **Date:** 2026-07-24 · **Author:** Claude (with sujal)

Describes *what the system is made of and how the pieces interact*. Vision and locked decisions
live in `DESIGN.md`; module/class/schema/algorithm detail lives in `LLD.md`. Everything below is
**provisional and pluggable**: each component implementation is a *candidate* behind an interface,
and a first-class **Comparison / Eval Harness** picks winners later by head-to-head benchmark
(DESIGN §12 #19, RESEARCH §5). No named tool is asserted as *the* choice.

> **Companion docs:** `DESIGN.md` (vision, goals, decision log 1–34) · `LLD.md` (detailed design) ·
> `INTELLIGENCE.md` (the smart layer) · `WORKFLOW.md` (work-management, modes, quota rationing) ·
> `RESEARCH.md` (sourced grounding: CLI drivers §1, auth/ToS §2, quota model §3, prior art §4).

---

## 1. Purpose & scope

A free, **ToS-clean framework over Claude Code, Codex, and Gemini** that drives those official
CLIs to full potential in **two modes** — an interactive co-pilot you steer live and a full
autopilot that saturates the fleet, runs tasks to completion, verifies, and replenishes work when
idle — organized around a native **Linear-like work-management layer** (workspaces → projects →
tasks) and lifted by an **intelligence layer** (codebase graph, deep RAG + rerank, cross-run
memory, self-maintaining guidelines, verifier, MoE-over-agents, small ML/RL) plus a **coordination
layer** that manages the human side of the loop.

**In scope (this doc):** the container decomposition; the two agent classes and how they compose into
**Agent Teams**; the worker contract and its six session kinds; the Capability Registry and evolution
principle; the Interaction Layer; the Autonomy Controller, quota-rationing scheduler, and
**bidirectional live-session handoff**; the **Overall Management Layer** (mission-control) that the
UIs — desktop **and mobile companion** — bind to; the **Bookmarks** store; the **Library &
Knowledge layer** (reading room) with its **Doc/Media Render pipeline** (reading-view + optional
TTS audio, offline-cached); intelligence and work-management overviews (with pointers to their own
docs); key data flows; technology choices; cross-cutting concerns; the milestone→component map;
risks.

**Out of scope / non-goals (DESIGN §2):** Antigravity or any GUI agent (dropped — GUI IDE, no
supported headless CLI); reverse-engineering subscription web sessions or reusing auth cookies as a
free API; spending overflow / raw-API credits; LLM fine-tuning in v1.

**ToS line held throughout (DESIGN §11, RESEARCH §2):** official CLIs on the operator's own
subscription accounts and machine, **no API keys** (`ANTHROPIC_/OPENAI_/OPENROUTER_/GEMINI_/GOOGLE_`
all unset — verified RESEARCH §0), published free tiers within their rate limits, one operator, no
scraping, no multi-account rotation, no limit evasion. Autopilot honors the same caps as interactive.

---

## 2. System context (C4 level 1)

Actors and side systems around the orchestrator. One operator, own accounts, own machine.

```
        ┌──────────────┐  chat · watch · edit · steer   ┌────────────────────────────────┐
        │   Operator    │◀──────────────────────────────▶│   ORCHESTRATOR (this system)    │
        │  (one person) │                                 │  daemon · Brain · UI            │
        └──────────────┘                                  └──┬──────────────┬──────────────┘
                                     drives via CLI (PTY /    │              │ uses as reasoning engine
                                     SDK / stream-json / RPC) │              │ (CLI-first, else free API)
                         ┌───────────────────────────────────┘              ▼
                         ▼                                          ┌────────────────────────┐
   ┌───────────────┬───────────────┬───────────────┐               │ Internal capability     │
   │  claude CLI    │  gemini CLI   │  codex CLI     │               │ agents (in-process):    │
   │ (Claude sub.)  │ (Google sub./ │ (OpenAI sub.)  │               │ graph-builder, deep-RAG │
   │                │  free tier)   │                │               │ retriever/reranker,     │
   └───────────────┴───────────────┴───────────────┘               │ clarifier, verifier,    │
       EXTERNAL coding-agent workers (CLI-only, dual-mode)          │ distiller, Updater/     │
                                                                     │ Watcher, Self-Diagnostic│
                                                                     └───────────┬────────────┘
                                                                                 │ fuelled by Reasoning
                                                                                 ▼ Provider (smart-pick)
   Side systems: local git repos (worktrees) · local filesystem ·      ┌───────────────────────┐
   SQLite (state + vectors + graph) · MCP servers (intelligence     │ CLI-as-LLM / free API  │
   injection) · optional TMS (Linear/Jira/GitHub, operator-owned)      │ (OpenRouter/Gemini) /  │
                                                                        │ local model            │
                                                                        └───────────────────────┘
```

**Deployment topology (DESIGN §12 #23 — decided later by comparison, not locked).**

- **Single-machine (today).** Daemon, Brain, workers, state, and UI all run on the operator's local
  machine, where the subscription auth and the compute live. Runs as a **boot/login service** with
  persistent state; on restart it **auto-resumes autopilot** (re-dispatches interrupted tasks,
  resumes/forks sessions where possible) unless settings disable it.
- **Optional split topology (later, candidate).** A lightweight **always-on coordinator**
  (queue · state · scheduler · watchdog · notifier · **authenticated remote endpoint**) on the
  operator's **free Oracle Cloud "Always Free" micro instance**, with the **heavy coding agents on
  the local, electricity-dependent machine** where the subscription + compute live. When local is
  off, the coordinator persists intent and resumes dispatch when local returns. The split is a
  **candidate** the Comparison/Eval Harness may or may not select — the container boundaries in §3
  are drawn so either topology works.
- **Remote / mobile access = through the coordinator, never the daemon (DESIGN §12 #26).** The
  local daemon's WebSocket API stays **localhost-only** and is *never* exposed to the internet. The
  **mobile companion** (and any off-LAN access) reaches the system **only through the authenticated
  coordinator endpoint** (tunnel + auth on the Always-Free node). When the laptop is **online** the
  coordinator relays full orchestration control down to the local daemon; when it is **offline** the
  coordinator serves read/monitor from its own persisted state (docs, links, **bookmarks**, the
  **Library & Knowledge** reading room — cached reading-view **text + TTS audio** for offline
  read/listen — TMS, dashboards, last-known session state, push notifications). This keeps the
  ToS/security line (§13) intact: no localhost daemon on the public internet, ever.

---

## 3. Container architecture (C4 level 2)

Three tiers over a single local WebSocket / JSON-RPC API. The **daemon owns all state and
sessions**; UIs are stateless views that attach/detach freely (durable core, disposable UIs). This
boundary is also the seam the optional split topology cuts along (§2).

The UIs do not bind to raw containers directly — they bind to the **Overall Management Layer
(mission-control)**, a top-level control plane that **aggregates monitor + control across all
workspaces / projects / teams / sessions / quota / TMS** and exposes it as one command+query surface
(DESIGN §12 #25). It is the primary place the operator steers from, on desktop or phone. Below it the
**Orchestration Engine** composes agents into **Agent Teams** — named, role-bearing groups layered
*over* Router/Parallel/Pipeline (DESIGN §12 #24). Everything here stays **provisional/pluggable**.

```
┌──────────────────────────── UI TIER (thin clients) ──────────────────────────────┐
│  Textual TUI (first)     Tauri + React/TS desktop (later)     Mobile companion     │
│  · live terminal panes   · ReactFlow orchestration graph      · monitor sessions   │
│  · address Brain|worker  · xterm.js terminals · Monaco co-edit · dashboards · TMS   │
│  · unified question/     · graph & RAG inspectors · visx dash  · docs/links + book- │
│    approval prompts       · team boards · promote/take-over     marks · notifs      │
│  · work board · activity narration · human-task inbox · quota/efficiency gauges     │
│      online → full orchestration control    ·    offline → coordinator read/monitor │
└───────────────────────────────────┬────────────────────────────────────────────────┘
     desktop/TUI: WebSocket/JSON-RPC │ (localhost)   mobile/off-LAN: authenticated coordinator only
┌─── OVERALL MANAGEMENT LAYER (mission-control) ─── the surface all UIs bind to ──────  [coord] ┐
│  Aggregates monitor + control across ALL workspaces · projects · TEAMS · sessions · quota · TMS │
└───────────────────────────────────┬────────────────────────────────────────────────────────────┘
                                     │
┌──────────── CORE TIER — Python async daemon ───────────  [split: local ⇄ Oracle coordinator] ┐
│                                                                                                │
│  Gateway (WS) ─────────── fan-out events to all clients; receive commands            [coord]  │
│  Orchestrator Brain ───── conductor you chat with; plans, routes, explains, narrates          │
│  Orchestration Engine ─── Router | Parallel | Pipeline  (equal peers, no default)             │
│  Agent Teams ──────────── named role-bearing groups (lead + planner/impl/review/verify/test)  │
│     └ teams layer OVER Router/Parallel/Pipeline; agent-to-agent peer channels (fan-out/x-check)│
│  Template/Feature Registry  composable SDLC workflows + team templates + meta-orchestration    │
│  Work Management ──────── native workspaces / projects / tasks (+ subtasks, deps)    [coord]  │
│  Bookmarks Store ──────── operator-saved docs/links/resources; sync to phone; attach→tasks [coord] │
│  TMS Bridge ───────────── bidirectional adapters: Linear / Jira / GitHub Issues (optional)    │
│  Autonomy Controller ──── pull→assign→run→verify→replenish loop                      [coord]  │
│     ├ Quota Scheduler ─── reservoir/token-bucket rationing of perishable quota       [coord]  │
│     ├ Watchdog ────────── self-healing: auto-compact, rate-limit backoff, auto-resume [coord] │
│     └ Live-Session Handoff  promote interactive→autonomous · take over autonomous→interactive │
│  Resource Ingestion ───── fetch / parse / index a task's reading materials + links (+bookmarks)│
│  Library & Knowledge ──── aggregate+index ALL managed-project docs + system docs + curated     │
│     │ reading materials (links/books/PDFs/guides/explainers); full-text + RAG search    [coord]│
│     │ w/ citations (reuses Context Engine); learning paths                                      │
│     └ Doc/Media Render ── item → readability/reading-view → optional TTS audio (pluggable       │
│        provider, local/free-first, harness-decided); text+audio cached on coordinator   [coord]│
│  ─────────── Intelligence subsystem (the core value — see INTELLIGENCE.md) ───────────         │
│  Capability Registry ──── probe & version each CLI's native features; delegate-first          │
│  Reasoning Provider ───── smart-pick LLM per call: CLI-as-LLM | free API | local              │
│  Interaction Layer ────── uniform question/approval across all workers (§7)                    │
│  Context Engine ───────── Codebase Graph (candidate) + deep RAG + rerank + fresh-context       │
│  Memory Store ─────────── cross-run decisions / conventions / preferences / failures / facts   │
│  Guidelines Engine ────── additive, self-proposed, human-editable rules → AGENTS.md            │
│  Verifier / Critic ────── independent verification; doubles as RLAIF reward model             │
│  MoE-over-agents router ─ contextual-bandit gate: sparse route vs ensemble + judge            │
│  Comparison / Eval Harness  head-to-head benchmarks; picks candidate winners empirically       │
│  Metrics / Observability ─ human + agent efficiency; rework-rate; live quota gauge   [coord]  │
│  Coordination & Learning ─ narrates the fleet; teaches the tool; mints human tasks            │
│  Self-maintenance agents ─ periodic Updater/Watcher + Self-Diagnostic (internal)     [coord]  │
│                                                                                                │
│  ─────────── Worker & session plane ───────────                                                │
│  Session Manager ──────── lifecycle of every session; the event sink/bus                      │
│  Worker Adapters ──────── uniform contract over 6 session kinds (§5)                           │
│  Workspace Manager ────── git worktrees; port allocation; semantic-conflict partitioning       │
│  Handoff Bus ──────────── readable, editable inter-stage artifacts                            │
│  Edit / Diff Sync ─────── captures human inline edits → feeds agent as new context            │
│  State Store (SQLite) ─── runs · sessions · events · handoffs · metrics · graph · chunks ·     │
│        memories · guidelines · verifications · caches · workspaces/projects/tasks/resources    │
└───────────────────────────────────┬────────────────────────────────────────────────────────────┘
                                     │  PTY (fork/exec) · headless stream-json · SDK · JSON-RPC · HTTPS
┌──────────────────────────── WORKER TIER (external) ────────────────────────────────┐
│  claude CLI      gemini CLI      codex CLI      OpenRouter / free-model API           │
│  (dual-mode: interactive PTY + headless, each rationed to its own message/rate caps)  │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

`[coord]` marks containers that would live on the **always-on coordinator** in the optional split
topology; heavy worker execution and code-intelligence indexing stay local with the subscription +
compute. The coordinator is also the **authenticated remote endpoint** the mobile companion binds to
(§2): with the laptop **online** it relays full control to the local daemon; **offline** it serves
read/monitor from the `[coord]` state it holds (Management Layer view, Work Management, Bookmarks,
the **Library & Knowledge** reading room with **cached reading-view text + TTS audio**, TMS, quota
gauge, last-known session state, notifications). Boundaries are provisional and confirmed later by
comparison.

**Subsystem notes (one line each):**

| Container | Responsibility |
|---|---|
| **Overall Management Layer** | Top-level mission-control; aggregates monitor + control across **all** workspaces/projects/teams/sessions/quota/TMS into one command+query surface all UIs (desktop + mobile) bind to. |
| **Gateway (WS)** | Single localhost socket; fan-out session/state events to all UIs; receive commands. |
| **Orchestrator Brain** | The conductor you chat with; plans, routes, explains routes, narrates the fleet. |
| **Orchestration Engine** | Router (split → best/cheapest worker), Parallel (N → compare/vote/synthesize), Pipeline (staged gates) — **equal peers**, chosen per run/template. |
| **Agent Teams** | Named, role-bearing groups (planner/implementer(s)/reviewer/verifier/tester…) with a **lead/coordinator**, drawn from external + internal + workflow-peer agents, sharing context/memory, templated per SDLC; a **teams layer over** Router/Parallel/Pipeline, with **agent-to-agent peer channels** (fan-out, cross-check, synthesize). |
| **Template/Feature Registry** | Composable SDLC workflows + **team templates** + user-built features; meta-orchestration. |
| **Work Management** | Native workspace→project→task store + board; task authoring; continuous supply. |
| **Bookmarks Store** | Operator-saved docs/links/resources; **sync to phone**; **attach to tasks** → feed Resource Ingestion. |
| **TMS Bridge** | Optional bidirectional sync to any owned TMS; TMS is source-of-truth when present. |
| **Autonomy Controller** | The autopilot loop with gates + replenishment; contains Quota Scheduler, Watchdog, Live-Session Handoff. |
| **Quota Scheduler** | Reservoir/token-bucket rationing of perishable 5h + weekly quota; regime-aware. |
| **Watchdog** | Self-healing: auto-compact on high context, rate-limit backoff, idle re-prompt, auto-resume on reset. |
| **Live-Session Handoff** | Bidirectional mid-flight driver swap: **promote interactive→autonomous** and **take over autonomous→interactive** by swapping a session's driver + permission/gate policy. |
| **Resource Ingestion** | Fetch/parse/index a task's attached reading materials + links (**and attached bookmarks**) into the Context Engine. |
| **Library & Knowledge** | Aggregates + indexes docs from **all managed projects** (repo docs, auto-generated docs, plans/handoffs/artifacts), the **system's own docs**, and curated **reading materials** (links, theoretical books/PDFs, introductory guides, expert explainers); **full-text + RAG search with citations** (reuses the Context Engine); curated **learning paths** (introductory→expert, unbiased, cited). Overlaps Resource Ingestion + Bookmarks + Coordination & Learning (library items ↔ task resources ↔ bookmarks). Core-tier, coordinator-served for phone read/listen. |
| **Doc/Media Render** | Renders a library item → clean **readability/reading-view** → optional **TTS audio narration** via a **pluggable provider** (local/free-first candidate, harness-decided, **no overflow credits**); **text + audio cached on the coordinator** for offline read/listen on the phone, with a listen queue. |
| **Capability Registry** | Version-aware probe of each CLI's native features + auth/ToS posture; delegate-first. |
| **Reasoning Provider** | Smart-picks the LLM for intelligence tasks by cost/latency/quota/quality. |
| **Interaction Layer** | One uniform question/approval experience across native + question-less workers. |
| **Context Engine** | Assembles per-stage context: graph-expand + hybrid retrieve + rerank + fresh-context. |
| **Memory Store** | Cross-run memory of decisions, conventions, preferences, failures, facts. |
| **Guidelines Engine** | Additive, self-proposed, human-editable rules surfaced to workers via AGENTS.md. |
| **Verifier / Critic** | Independent verification and best-of-N gating; the RLAIF reward signal. |
| **MoE-over-agents router** | Contextual-bandit gate deciding sparse route vs ensemble-and-judge. |
| **Comparison / Eval Harness** | Benchmarks candidate providers head-to-head; selects winners empirically. |
| **Metrics / Observability** | Human+agent efficiency, rework-rate trend, live quota utilization gauge. |
| **Coordination & Learning** | Narrates the fleet, teaches the tool, turns decisions into human tasks. |
| **Self-maintenance agents** | Periodic Updater/Watcher + Self-Diagnostic (internal capability agents). |
| **Session Manager** | Lifecycle + event bus for every session (PTY and API alike). |
| **Worker Adapters** | Uniform contract over the six session kinds (§5). |
| **Workspace Manager** | git worktrees, port allocation, graph-guided semantic-conflict partitioning. |
| **Handoff Bus** | Readable, editable inter-stage artifacts you can inspect/edit before use. |
| **Edit / Diff Sync** | Captures your inline edits, attributes human vs agent, feeds diff back as context. |
| **State Store (SQLite)** | Single durable store for all of the above; enables restart + auto-resume. |

---

## 4. Two classes of agent

| Class | Members | Integration | Role |
|---|---|---|---|
| **External coding agents** | Claude Code, Codex, Gemini | **CLI only**, driven **dual-mode** — interactive PTY *and* headless — each **rationed to its own message/rate limits** | Do the heavy coding. You watch the live terminal and can take over. Driven to full potential via native features probed by the Capability Registry. |
| **Internal capability agents** | graph-builder, deep-RAG retriever/reranker, clarifier, verifier, distiller, **Updater/Watcher**, **Self-Diagnostic** | In-process, **fuelled by the Reasoning Provider** (CLI-as-LLM / free API / local) | Make the system smart and fill CLI gaps; the self-maintenance pair keeps the system current and debugged. Run even with **zero external agents**. |

The **Reasoning Provider** is the fuel line for the internal class (RESEARCH §1 mode 3/2 for the
CLI-as-LLM path; free API/local otherwise). The self-maintenance agents (DESIGN §12 #22,
RESEARCH §6) are dogfooded on this system first and generalizable to managed codebases later.

Because internal agents are API-style and external ones are PTY/SDK-style, the **worker contract
spans PTY *and* API sessions from day one** (DESIGN §12 #4).

### 4.1 Agent Teams (composition + peer messaging)

Individual agents rarely act alone: the **Orchestration Engine** composes them into **Agent Teams**
(DESIGN §12 #24) — the unit it assigns to a project/task.

- **Membership is heterogeneous.** A team draws from all three sources: **external** coding agents
  (Claude/Codex/Gemini), **internal** capability agents (clarifier, verifier, distiller…), and
  **workflow-peer** agents — additional worker sessions spun up for a role. Members carry **roles**:
  planner, implementer(s), reviewer, verifier, tester, and a **lead / coordinator** that sequences
  the team, resolves peer disputes, and owns the team's handoff.
- **Shared context/memory.** A team shares one Context Engine view and Memory-Store namespace, so
  peers build on each other's findings rather than starting cold (RESEARCH §4 — cross-fleet memory
  is a named differentiator).
- **Templated per SDLC.** Teams are instantiated from **team templates** in the Template/Feature
  Registry (e.g. a "feature-delivery" team = planner → 2 implementers → reviewer → verifier →
  tester), just as runs are.
- **A layer *over* the orchestration modes, not a fourth mode.** A team *uses* Router / Parallel /
  Pipeline internally — e.g. Parallel fan-out to N implementer-peers, then a reviewer cross-checks,
  then the lead synthesizes. Router/Parallel/Pipeline remain equal peers (§3); Teams sit above them.
- **Agent-to-agent peer channels.** Beyond the operator↔Brain and operator↔worker channels (§8),
  teams add **peer channels** so members message each other directly (fan-out a sub-question,
  cross-check a diff, request a synthesis) under the lead's coordination. These ride the same
  `Session` sink/event bus (§5) — a peer message is just input written to another session — so no
  new transport is needed.
- **Steered from mission-control.** Teams are first-class objects in the **Overall Management Layer**
  (§3): the operator sees every team's roster, roles, and live activity, and steers them there.

Full team model, role DSL, and lead-coordination policy live in `WORKFLOW.md`.

---

## 5. Worker contract & session kinds

Everything downstream depends only on this uniform contract — a new worker is one adapter.

```
Session:
  info()                 -> {id, label, kind, status, quota_posture}
  start(initial_input?)  -> spawn backend, begin streaming
  write(text|bytes)      -> inject input (Brain OR human — identical path)
  interrupt()            -> cooperative pause / steer
  stop()                 -> terminate
  emits via sink: output · status · question · approval · usage events
```

Five concrete **session kinds** (drivers), from RESEARCH §1:

| Kind | Driver | Transport | Autonomy / interactivity | Primary use |
|---|---|---|---|---|
| **Claude Agent SDK** | `claude-agent-sdk` (`ClaudeSDKClient`) — bundles + shells to the `claude` binary | stream-json over the SDK's control channel | Autonomous, with `can_use_tool` + `PreToolUse` hooks, `permission_mode`, resume/fork | Structured autonomous Claude runs; the recommended headless path. |
| **Headless stream-json** | raw `claude -p --output-format stream-json --input-format stream-json --verbose` (SDK-equivalent) | stream-json over pipe/PTY | Autonomous | Structured runs without the SDK. **Gotcha:** non-TTY stdout is block-buffered → looks like a hang; use a PTY or the SDK (RESEARCH §1.1). |
| **PTY** | fork/exec the real CLI under a pseudo-terminal | raw terminal bytes | Interactive, **take-over-able** | Live watch + human takeover; the takeover substrate for all three CLIs. |
| **OpenRouter ApiSession** | HTTPS chat to free models | SSE deltas | API worker | Cheap/bulk drafting **and** fuel for internal reasoning (optional adapter, not the core). |
| **Codex app-server JSON-RPC** | **`codex app-server`** — the blessed public integration surface; official TS SDK (`@openai/codex-sdk`) wraps it; `codex mcp-server` separately exposes Codex *as* an MCP tool | JSON-RPC over stdio (WS/unix experimental) | Autonomous **with approval callbacks**; `turn/steer` mid-turn input; thread fork/resume; **rate-limit queries** | Bidirectional Codex control (its analog to Claude stream-json). Fire-and-forget runs via `codex exec --json` / `exec resume` (`CAPABILITIES.md §1`). |
| **Gemini ACP** | `gemini --acp` (Agent Client Protocol) | JSON-RPC over stdio | Autonomous **with approval callbacks**; `setSessionMode` mid-session | Bidirectional Gemini control; shadow-git checkpointing via settings (the `--checkpointing` flag was removed in v0.11.0). |

- **PtySession** covers claude/gemini/codex interactive: `write()` = bytes to the PTY, so Brain and
  human are two input sources into the *same* terminal.
- **ApiSession** covers OpenRouter and the JSON-RPC/ACP/SDK structured drivers: `write()` = append
  message + stream deltas / RPC.
- **MCP is the universal intelligence-injection layer** (RESEARCH §1.4): the Context Engine,
  Memory Store, Guidelines Engine, and Codebase-Graph candidate are exposed **once** as MCP tools
  and pulled by all three CLIs (all confirmed MCP hosts). Which specific graph/RAG implementation
  sits behind those MCP tools is a **candidate** decided by the Comparison/Eval Harness.

All driver/auth choices are recorded per worker in the Capability Registry (§6). Auth is
**subscription-only** with API-key env vars unset (RESEARCH §2).

---

## 6. Capability Registry & the evolution principle

The CLIs evolve fast and already ship real capabilities (memory files, MCP, subagents, web search,
structured output). We must not reinvent what they do well (DESIGN §7, §12 #13).

- **Probe (version-aware).** The Registry detects each worker's installed version, native feature
  set, session-kind support, and **auth + ToS posture** (RESEARCH §2 matrix). Today's verified state
  (RESEARCH §0, updated 2026-07-27): `claude` v2.1.218 with subscription creds; **`gemini` v0.52.0
  installed** (operator holds a Gemini subscription; one-time OAuth login pending); `codex` not
  installed — so "max utilization" spans **Claude now, + Gemini after its login**. The verified
  per-harness feature inventory lives in `CAPABILITIES.md`.
- **Delegate-first.** Prefer the CLI's native feature over our own (native resume/fork over a
  reimplementation, native MCP loading over hand-rolled injection, native approval callbacks over
  the sentinel fallback).
- **Authoritative layer.** Our orchestration stays the **source of truth**; native features are
  delegated *to*, not depended *on* — a crashed or changed CLI feature degrades gracefully.
- **Shim gaps, then retire the shim.** Where a CLI lacks a capability (e.g. Codex `exec` has no
  interactive answer channel), we shim it (§7). When the CLI later ships that capability, the
  Updater/Watcher agent (§4) flags it and we switch to delegating — the shim retires.

This is the same seam that makes every component a **candidate**: the Registry records what each
worker/provider *can* do, and the Comparison/Eval Harness decides what the system *should* use.

---

## 7. Interaction Layer (uniform question/approval)

Some workers pause and ask in their own idiom; some (notably Codex `exec`) run autonomously and
never ask. The Interaction Layer gives **one consistent question/approval experience across all
workers**, delegating to native mechanisms first and shimming only where they are absent.

- **Native-first detection.** Use each driver's own control channel when present:
  Claude's **`can_use_tool` callback / `PreToolUse` hooks** (which can return `deny`), Codex
  **app-server** approval callbacks, Gemini **ACP** approval callbacks (RESEARCH §1.1–1.3). These
  are the programmatic "answer the prompt". As of mid-2026 native approval callbacks exist on
  **all three** drivers (`CAPABILITIES.md §2.1`) — the fallbacks below are degraded-mode paths,
  no longer the expected Codex plan.
- **Sentinel fallback for question-less CLIs.** When a driver can't ask (e.g. `codex exec`,
  block-on-input PTY states), the worker is instructed (via system prompt / wrapper) to emit a
  structured marker — `<<ASK>>{json}<<END>>` — that the layer detects and lifts into a `question`
  event.
- **Clarifier for "no interface at all."** When even a sentinel can't be injected, the internal
  **clarifier** agent watches the trajectory and *proactively* raises clarifying questions on the
  worker's behalf.
- **Surfacing + answering.** Detected questions/approvals become uniform structured events → the UI
  shows one prompt (and the Brain may answer autonomously per the configured autonomy gates). The
  answer is written back the same way input always flows (`Session.write`): typed into the PTY, fed
  as the next message, or returned through the SDK/RPC callback.

Result: even a headless autonomous CLI behaves like a full interactive agent, without our having to
reinvent an approval UX per CLI.

---

## 8. Autonomy & the quota-rationing scheduler

The **Autonomy Controller** runs the autopilot loop and contains the **Quota Scheduler** and the
self-healing **Watchdog**. Full detail (states, gate DSL, throw-away minimization) is in
`WORKFLOW.md §3, §5`; the essentials:

- **Autonomy is a spectrum, gated** (DESIGN §5): *interactive co-pilot* → *assisted* →
  *autonomous autopilot*, set per workspace/project/task. The autopilot loop is
  **pull ready task → assign best worker (MoE gate) → run to completion → verify (verify→repair) →
  commit/handoff → replenish**, escalating to the operator only on configured gates (ambiguity,
  risk, verification failure, cost thresholds). Gates are declarative
  (e.g. "human-approve any migration", "auto-merge if verifier passes and diff < N lines").
- **Dual-mode saturation, per-worker rationed** (DESIGN §12 #21). All three workers run **both**
  interactive-PTY and headless, each **rationed to its own message/rate limits** (Codex/Gemini per
  operator opt-in; Claude the safest unattended default). The Controller saturates each worker up
  to — never beyond — its caps, and **never spends overflow / API credits** (RESEARCH §2, §3).
- **Quota-rationing objective (the heart of "max usage" — RESEARCH §3).** A **reservoir /
  token-bucket controller** that:
  - treats each perishable **rolling 5-hour window** as *burn-it-or-lose-it* (fresh allocation each
    window, no rollover — "use it or lose it"), finishing as much of every cycle as possible; and
  - **paces the weekly cap** so it lasts the whole week and **drains almost completely just before
    reset**, never front-loaded and starved mid-week.
  - **Regime-aware:** today interactive + headless draw from **one pool** (5h + weekly) because the
    Agent-SDK monthly-credit split announced for 2026-06-15 was **paused and is not in effect**
    (RESEARCH §3). If it un-pauses, headless becomes a **second pool** and the scheduler detects the
    regime and soaks both. Remaining balance isn't CLI-exposed; the Metrics layer tracks a live
    utilization gauge from session telemetry (`/usage`, `ccusage`-style consumption, RESEARCH §3).
- **Throw-away minimization.** Best-of-N + checkpoint-merge and a pre-merge verifier gate
  (RESEARCH §4) keep discarded work low so perishable quota buys landed change, not churn.
- **Bidirectional live-session handoff** (DESIGN §12 #27; the live twin of the modes spectrum). Any
  running session can be moved between modes **mid-flight**, without restarting it:
  - **Promote interactive → autonomous:** hand a session the operator has been driving to the
    Autonomy Controller with a goal + gates and walk away — the Controller becomes the session's
    driver.
  - **Take over autonomous → interactive:** seize a running autonomous session's live terminal,
    drive it by hand, then hand it back.
  The mechanism is a **driver + permission/gate-policy swap** on the *same* live `Session` (§5): the
  session's input source flips between operator (`write` into the PTY) and Controller, and its
  `permission_mode`/gate policy is re-pointed (interactive approvals ⇄ autonomy gates). No session
  restart, no lost context. Handoff can be triggered from desktop **or** the mobile companion (when
  the laptop is online), and applies to individual sessions and to team members alike.
- **Durability & auto-resume** (DESIGN §12 #23). The daemon runs as a **boot/login service** with
  persistent SQLite state; on restart it **auto-resumes autopilot** — re-dispatches interrupted
  tasks and resumes/forks sessions where possible — **unless settings disable it**. Power-loss
  tolerant. In the optional split topology the coordinator persists intent while local is off and
  resumes dispatch when it returns (§2).

---

## 9. Intelligence subsystem overview

The intelligence layer is the **core value-add** (DESIGN §12 #14); it is specified in
`INTELLIGENCE.md` and only sketched here. All of it is **candidate-based** — models, graph builder,
embedders, rerankers, retrieval strategy, and routing policy sit behind interfaces and are selected
by the Comparison/Eval Harness (RESEARCH §5).

- **Context Engine** — per-stage context via **Codebase Graph** (a *candidate* graph provider:
  tree-sitter graph vs SCIP-based vs `codegraph` vs others — no choice asserted) **graph-expansion**,
  **hybrid retrieval** (dense ⊕ lexical/BM25, fused), **rerank**, **fresh-context resets**, and a
  per-stage context budget.
- **Memory Store** — cross-run decisions, conventions, preferences, failures, facts.
- **Guidelines Engine** — additive, self-proposed, human-editable rules → AGENTS.md (delegating to
  each CLI's native memory-file surface via the Capability Registry).
- **Verifier / Critic** — independent verification and best-of-N gating; also the RLAIF reward model.
- **MoE-over-agents router** — a contextual-bandit gate choosing sparse route vs ensemble+judge
  (identified in RESEARCH §4 as a genuine differentiator — nobody routes to the *right* agent by
  task-type/cost/quota/track-record).
- **Small, real ML/RL** — bandit router, outcome predictors, offline RL, RLAIF; **no LLM
  fine-tuning in v1** (DESIGN §12 #15).

See `INTELLIGENCE.md` for the graph schema, RAG toolkit, metrics catalog, and ML/RL designs.

---

## 10. Work-management overview

Work is first-class (DESIGN §12 #8); the model lives in `WORKFLOW.md` and is sketched here.

- **Native hierarchy.** **Workspace → Project → Task** (+ subtasks, dependencies, priority, status,
  labels, assignee = agent *or* human) — a Linear-like model you author into.
- **Task = intent + workflow + resources.** Each task carries a description, an assigned
  workflow/template, and **resources** (reading materials, links, references) that **Resource
  Ingestion** fetches/parses/indexes into the Context Engine so the worker uses cited sources.
- **Continuous supply + replenishment.** A backlog feeds the Autonomy Controller; the operator tops
  it up or the system proposes follow-ups from verifier findings and gaps — nothing idles silently.
- **TMS-agnostic & TMS-optional.** The TMS Bridge syncs bidirectionally with any owned TMS (Linear/
  Jira/GitHub Issues, …); TMS is source-of-truth when present, the native store otherwise.
- **Bookmarks (DESIGN §12 #28).** The operator can **bookmark** docs/links/resources into the
  Bookmarks Store; bookmarks **sync to the phone** (readable offline via the coordinator) and can be
  **attached to a task**, at which point they feed **Resource Ingestion** exactly like any other
  attached resource — a low-friction path from "saw something useful" to "task context."
- **Library & Knowledge (DESIGN §12 #30–31).** The **Library & Knowledge** container (§3) aggregates
  and indexes the docs of **all managed projects** (repo docs, auto-generated docs,
  plans/handoffs/artifacts), the **system's own docs**, and curated **reading materials** (links,
  theoretical books/PDFs, introductory guides, expert explainers) into one searchable **reading
  room** — **full-text + RAG search with citations, reusing the Context Engine**, with curated
  **learning paths** (introductory→expert, unbiased, cited). Every item renders (Doc/Media Render,
  §3) to a clean **reading view** and optional **TTS audio**, **cached on the coordinator** for
  offline **read-or-listen** on the phone. It overlaps Resource Ingestion + Bookmarks + Coordination
  & Learning: library items ↔ task resources ↔ bookmarks are the same materials seen from three angles.
- **Human tasks.** The queue also holds tasks for *you* (decisions, reviews, learning items); the
  Coordination & Learning container narrates the fleet and teaches the tool.

---

## 11. Key data flows

**11.1 Goal → Brain → worker (M0)**
```
UI {brain.send} → Gateway → Brain → SessionManager.spawn(worker, prompt)
Session output → sink → Gateway → ALL UIs (live). Brain explains via {brain.message}.
```

**11.2 Take over a live worker (M0)** — `UI {agent.send, sid, text} → Session.write`
(the *same* path the Brain uses; PTY makes Brain + human two inputs into one terminal).

**11.3 Autonomous pull → run → verify → replenish (M6)**
```
Autonomy Controller: pull ready task → Quota Scheduler admits (5h/weekly budget OK, no overflow)
  → MoE gate assigns worker → run to completion → Verifier (verify→repair) → commit/handoff
  → replenish (pull next / generate follow-ups). Escalates to a human task only on a gate.
```

**11.4 Question / approval, any worker (M2)**
```
Native callback (can_use_tool / app-server / ACP) OR sentinel <<ASK>> OR clarifier
  → Interaction Layer → {question|approval} event → UI prompt (or Brain per gate)
  → answer → Session.write / callback → worker continues.
```

**11.5 Smart context for a stage (I1 / M3–M5)**
```
Engine requests stage context → Context Engine:
  graph-expand → hybrid retrieve (dense ⊕ lexical) → rerank → budget-fit → inject via MCP tools.
```

**11.6 Restart → auto-resume (M0 state, hardened M6)**
```
Boot/login service starts daemon → load SQLite state → (unless disabled) auto-resume autopilot:
  re-dispatch interrupted tasks, resume/fork sessions, Quota Scheduler recomputes budget → continue.
```

**11.7 Resource ingestion → context (M3)**
```
Task attaches links/materials → Resource Ingestion: fetch (httpx) → parse/extract → index
  (chunks + vectors + graph) → available to the Context Engine for the assigned worker.
```

**11.8 Pipeline w/ human gate + inline edit (M2)** — stage-1 writes HANDOFF → `gate:human` pauses →
you inline-edit (Diff Sync captures + attributes) + approve → stage-2 starts with handoff + your diff.

**11.9 New UI attaches** — Gateway sends snapshot + per-session backlog; nothing lost.

**11.10 Live-session handoff — promote / take over (M8)**
```
Promote:  UI/Mobile {session.promote, sid, goal, gates} → Management Layer → Autonomy Controller
  becomes driver → permission_mode/gates re-pointed → session runs autonomously (same live session).
Take over: UI/Mobile {session.takeover, sid} → Controller yields driver → operator write()→PTY,
  interactive approvals restored → (later {session.handback} returns it to the Controller).
```

**11.11 Mobile companion — online vs offline via the coordinator (M8)**
```
Phone → authenticated coordinator endpoint (never the localhost daemon, §2, §13):
  laptop ONLINE  → coordinator relays to local daemon → FULL control (start/stop, steer,
                   promote/take-over, chat Brain/worker), live events streamed back.
  laptop OFFLINE → coordinator serves READ/MONITOR from its [coord] state: Management-Layer view,
                   dashboards, docs/links, bookmarks, TMS, last-known session state, push notifs.
```

**11.12 Bookmark → task → ingestion (M8 → M3 path)**
```
Operator/phone {bookmark.add, url|doc} → Bookmarks Store → sync to phone.
Later {bookmark.attach, taskId} → task resource → Resource Ingestion (11.7) → Context Engine.
```

**11.13 Library read/listen — view or narrate, online live / offline cached (M9)**
```
Managed-project docs (repo/auto-gen/plans/handoffs/artifacts) + system docs + curated reading
  materials (links/books/PDFs/guides/explainers)
    → Library & Knowledge index (full-text + RAG w/ citations, reuses Context Engine)
    → Doc/Media Render: readability/reading-view → (optional) TTS audio via pluggable provider
    → cache text + audio on the coordinator
    → phone: READ or LISTEN
        laptop/coordinator ONLINE  → live render + search + citations
        OFFLINE                    → coordinator serves cached reading-view text + audio (listen queue).
Search hit → citation → jump to source doc; a library item can also {bookmark.add} / attach→task (11.12).
```

---

## 12. Technology choices

All rows are **provisional/pluggable**; the "Pluggable?" column flags what the Comparison/Eval
Harness will benchmark before any lock-in (RESEARCH §5).

| Concern | Candidate choice | Why | Pluggable? |
|---|---|---|---|
| Core / daemon | Python 3.12 + asyncio | PTY control, subprocess fan-out, AI tooling; one loop drives many sessions | Foundational |
| Transport | WebSocket + JSON lines (`websockets`), localhost only | Bidirectional streaming, multi-client fan-out | Low |
| PTY | stdlib `pty` + `asyncio.add_reader` | Dependency-light real terminals; non-blocking | Low |
| Claude driver | `claude-agent-sdk` (`ClaudeSDKClient`) + raw stream-json fallback | Correct subscription-auth headless path (RESEARCH §1.1) | Driver-level |
| Codex / Gemini drivers | app-server JSON-RPC · ACP · PTY | Bidirectional control + takeover (RESEARCH §1.2–1.3) | Driver-level |
| Model / API (internal) | `httpx` async (SSE) via Reasoning Provider | Stream free-model deltas, embeddings, rerank | **Yes** |
| Codebase graph | tree-sitter graph · SCIP (`scip-python`/`scip-typescript`) · `codegraph` — **candidates** | Structural queries; **no choice asserted** (RESEARCH §5) | **Yes — harness picks** |
| Retrieval | hybrid dense (`sqlite-vec`/`fastembed`/ONNX) ⊕ lexical (FTS5/BM25), RRF | Free, local, deep RAG | **Yes** |
| Rerank / embedders | free-API / local candidates | Quality vs cost/latency/quota | **Yes — harness picks** |
| Routing policy | contextual bandit / heuristics — candidates | MoE-over-agents gate | **Yes — harness picks** |
| Persistence | SQLite (+ vector + FTS + graph tables) | One durable store; enables auto-resume | Low |
| Isolation | git worktrees (+ port allocation) | Parallel workers edit independently | Low |
| Resource fetch | `httpx` + readability/markdown extraction | Ingest attached links | **Yes** |
| Library render / reading-view | readability/markdown extraction (shared with resource fetch) | Clean read view for any doc/material | **Yes** |
| TTS provider (read-or-listen) | **pluggable** — local/free-first candidate (e.g. on-device/offline engine), harness-decided; **no overflow/API credits** | Optional audio narration, ToS-clean, offline-cacheable | **Yes — harness picks** |
| Config / specs | YAML | Templates + run definitions | Low |
| TUI | Textual | Fast multi-pane live terminals | Client-swappable |
| Desktop | Tauri + React + TS (ReactFlow, xterm.js, Monaco, visx) | Rich-viz client on the same daemon | Client-swappable |
| Mobile companion | thin client (candidate: responsive PWA / Tauri-mobile / native) over the **authenticated coordinator endpoint** | Monitor + control from phone without exposing the daemon | **Yes — client-swappable** |
| Remote endpoint (mobile/off-LAN) | authenticated tunnel + auth on the coordinator (candidate) | Reach the system remotely, ToS/security-clean (never localhost daemon on the internet) | **Yes — with topology** |
| ML/RL | numpy/scipy bandits; small PyTorch predictors/offline-RL when trained | Real but small; no LLM fine-tuning | **Yes** |
| Coordinator host (split) | Oracle Cloud "Always Free" micro (candidate) | Always-on, free, ToS-clean | **Yes — topology TBD** |

---

## 13. Cross-cutting concerns

- **ToS & security (DESIGN §11, RESEARCH §2).** The daemon's transport is **localhost-only** and is
  **never exposed to the internet**; **subscription auth only** with all API-key env vars unset;
  published free tiers within rate limits; one operator; no session scraping, no multi-account
  rotation, no limit evasion. **Remote / mobile access is *only* through the authenticated
  coordinator endpoint** (tunnel + auth on the Always-Free node, §2) — the single internet-facing
  surface, which relays to the daemon when local is online and serves read/monitor from its own
  state when offline. The Quota Scheduler **never spends overflow/API credits** and backs off as caps
  approach; autopilot honors the same caps as interactive. The Capability Registry records each
  worker's auth/ToS posture; Codex/Gemini unattended participation is a per-worker opt-in the
  operator controls.
- **Reuse first; invent only on demonstrated need (DESIGN §12 #29).** We **delegate to what already
  exists** — native CLI features (via the Capability Registry, §6), installed tools (`codegraph`,
  SCIP indexers, tmux/PTY/worktree substrate, watchdog patterns — RESEARCH §4–5) — and **build a new
  capability only when a real gap or the Comparison/Eval Harness shows nothing adequate exists.** The
  same principle governs the new surfaces here: the mobile companion, teams layer, and handoff reuse
  the existing `Session` bus, Gateway, and coordinator rather than adding parallel machinery. The
  **Library & Knowledge** layer likewise reuses the **Context Engine** (for its RAG search +
  citations), the **Resource Ingestion** fetch/parse/index path, the readability extraction, and the
  **coordinator cache** — it aggregates and indexes what already exists, adding only the reading-room
  surface and the pluggable **TTS** step.
- **Durability & failure isolation.** All state in SQLite; boot/login service; **auto-resume** on
  restart; power-loss tolerant. A crashed session never kills the daemon; runs are replayable from
  State. The Watchdog self-heals (auto-compact, rate-limit backoff, idle re-prompt, auto-resume on
  reset — reused from prior art, RESEARCH §4).
- **Observability.** Metrics track **human + agent efficiency**; the headline "getting smarter"
  signal is a falling **rework-rate** trend, and the same metrics are ML training data. A live quota
  utilization gauge shows how full the 5h + weekly reservoirs are.
- **Extensibility.** New worker = one adapter; new workflow = one template; **new team = one team
  template**; new UI (desktop **or mobile**) = one client over the Management Layer; new language =
  one tree-sitter grammar; new intelligence provider = one interface behind the Comparison/Eval
  Harness; **new library source** (doc kind / reading-material feed) = one loader, and **new TTS
  provider** = one interface behind the Harness.
- **Concurrency.** Single async loop; PTY via `add_reader`, API/RPC streams via tasks; cooperative.
  Parallel workers isolated by worktrees and (candidate) graph-guided semantic-conflict partitioning.
- **Self-maintenance.** The internal Updater/Watcher tracks evolving CLI/model/capabilities → feeds
  the Capability Registry; the Self-Diagnostic reads internal error logs and debugs failures
  (DESIGN §12 #22).

---

## 14. Milestone → component map

Aligns with DESIGN §10 (build track M0–M9, intelligence track I0–I4). Each milestone/increment is
independently usable; the two tracks interleave.

| Milestone | Containers delivered |
|---|---|
| **M0** | Gateway, Session Manager, PtySession, **Claude** adapter (SDK + stream-json + PTY), Brain v0, CLI test client, file transcripts, minimal State Store + auto-resume seed. |
| **M1** | **Gemini + Codex** adapters (ACP / app-server + PTY), **OpenRouter** ApiSession, Workspace Manager (worktrees), **Capability Registry v0** (probe CLIs, auth/ToS posture). |
| **M2** | Interaction Layer (native callbacks + sentinel + clarifier), Handoff Bus, Edit/Diff Sync, full SQLite State Store, Pipeline mode. |
| **M3** | **Work Management v1** (native workspaces/projects/tasks + board, authoring), **Resource Ingestion v0**. |
| **M4** | Textual TUI (panes, address Brain/worker, unified questions, inline edit, handoff approval, work board, activity feed, quota gauge). |
| **M5** | Template/Feature Registry + **Router + Parallel** modes; SDLC templates. |
| **M6** | **Autonomy Controller** (autopilot loop, gates, replenishment) + **Quota Scheduler** + **Watchdog** + **TMS Bridge** (Linear/Jira/GitHub); hardened durability/auto-resume. |
| **M7** | Native desktop UI (Tauri + xterm.js) on the same daemon. |
| **M8** | **Agent Teams** (team templates, roles, lead/coordinator, agent-to-agent peer channels over Router/Parallel/Pipeline) + **Overall Management Layer** (mission-control aggregation) + **Live-Session Handoff** (promote/take-over, both directions) + **Bookmarks Store** + **Mobile companion** over the **authenticated coordinator endpoint** (full control when laptop online; read/monitor via the always-on coordinator when offline). |
| **M9** | **Library & Knowledge layer** — aggregate + index all managed-project docs + system docs + curated reading materials (links/books/PDFs/intro/expert explainers), **full-text + RAG search with citations** (reuses Context Engine), curated **learning paths** — plus the **Doc/Media Render pipeline** (readability/reading-view + optional **TTS audio** via a pluggable local/free-first provider), **text + audio cached on the coordinator** for offline **read-or-listen** on the phone. |

| Increment | Intelligence delivered (see INTELLIGENCE.md) |
|---|---|
| **I0** | Reasoning Provider + Capability Registry (delegate-first). |
| **I1** | Codebase Graph (candidate) + deep RAG + two-stage rerank + fresh-context. |
| **I2** | Memory Store + Guidelines Engine (additive, human-editable → AGENTS.md). |
| **I3** | Verifier/Critic + verify→repair; MoE-over-agents (bandit gate). |
| **I4** | Metrics flywheel → trained ML/RL (bandit router, predictors, offline RL, RLAIF); embedding adaptation. |

The **Comparison/Eval Harness** and **self-maintenance agents** are cross-cutting: the Harness runs
whenever a component has competing candidates (from I0 onward); the Updater/Watcher + Self-Diagnostic
come online once there is a Capability Registry and internal error log to read (≈ M1/I0+).

Interleave example (DESIGN §10): I1 lands around M3–M5; the Autonomy Controller in M6 leans on I3's
verifier and I0's Quota-aware Reasoning Provider.

---

## 15. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Codex/others lack an interactive question interface | Interaction Layer: native app-server/ACP callbacks first; sentinel `<<ASK>>` + clarifier fallback (§7). |
| Perishable quota wasted, or overflow accidentally spent | Quota Scheduler reservoir/token-bucket: saturate 5h, pace weekly to near-empty at reset, hard "no overflow/API credits" stop (§8, RESEARCH §3). |
| SDK-credit split un-pauses → quota regime changes | Regime-aware scheduler detects one-pool vs two-pool and adapts (§8, RESEARCH §3, §6). |
| Codex/Gemini subscription-headless is ToS-gray | Per-worker opt-in; rationed to message/rate limits; Claude the safe unattended default; Registry records posture (§6, RESEARCH §2). |
| Premature component lock-in (graph/embedder/reranker/router) | Everything a **candidate** behind an interface; Comparison/Eval Harness picks winners empirically (§12, RESEARCH §5). |
| Power loss / restart loses autopilot progress | Boot/login service + SQLite + auto-resume; optional Oracle coordinator persists intent while local is off (§2, §8). |
| CLIs evolve and our shims rot | Capability Registry version-probing + Updater/Watcher; delegate-first, retire shims when native lands (§6). |
| Parallel workers collide semantically despite worktrees | Graph-guided semantic-conflict partitioning (candidate) + verifier gate (§13, RESEARCH §4). |
| Non-TTY headless stdout block-buffers → looks hung | Use the SDK or allocate a PTY (RESEARCH §1.1). |
| Phone/remote access tempts exposing the localhost daemon to the internet | **Never**; the only internet-facing surface is the **authenticated coordinator endpoint** (tunnel + auth); daemon stays localhost-only (§2, §13, DESIGN §12 #26). |
| Live-session handoff corrupts in-flight state (driver/permission swap mid-run) | Swap on the *same* live `Session` (no restart); re-point `permission_mode`/gates atomically; State Store checkpoints so a botched handoff is recoverable (§8). |
| Agent Teams add coordination complexity / cost | Teams reuse the existing `Session` bus + orchestration modes (no new transport); team templates keep composition declarative; lead-coordinated peer channels bound fan-out; reuse-first principle (§4.1, §13). |
| Offline mobile shows stale state as if live | Coordinator serves clearly-labeled **last-known** read/monitor while offline; full control resumes only when the laptop is online (§2, §11.11). |
| TTS/library render tempts a paid cloud API or leaks docs off-box | TTS provider is **pluggable, local/free-first, harness-decided**, spending **no overflow/API credits**; render + cache run on the operator's own coordinator; library is served **only through the authenticated coordinator**, same ToS/security line as everything else (§2, §12, §13, DESIGN §12 #31). |
| Cached library text+audio bloats the coordinator | Audio generated **on demand / for the listen queue** and cached with the reading-view text; caches live in the State Store like other coordinator state, evictable — provisional, tuned later (§3, §12). |
| Scope (large system) | Strict milestone gating; each milestone/increment independently usable (§14). |

---

## 16. For approval

1. The **container decomposition** (§3) and the **two-class agent model** (external CLI workers +
   internal capability agents incl. Updater/Watcher + Self-Diagnostic).
2. The **six session kinds** and MCP-as-injection-layer worker contract (§5), with
   **subscription-only, dual-mode, per-worker-rationed** operation.
3. The **quota-rationing scheduler** objective and regime-awareness (§8) — saturate 5h, pace weekly,
   never spend overflow.
4. **Durability + auto-resume** and the **optional local-worker + Oracle-coordinator split
   topology** as a candidate (§2, §8).
5. **Agent Teams** as a **teams layer over** Router/Parallel/Pipeline (roles, lead/coordinator,
   agent-to-agent peer channels, team templates), the **Overall Management Layer** (mission-control)
   that all UIs bind to, **bidirectional live-session handoff** (promote/take-over), **Bookmarks**,
   and the **mobile companion** reaching the system **only through the authenticated coordinator
   endpoint** — full control when the laptop is online, read/monitor via the always-on coordinator
   when offline (§2, §3, §4.1, §8; DESIGN §12 #24–29).
6. The **Library & Knowledge layer** (reading room) — aggregating/indexing all managed-project docs
   + system docs + curated reading materials with **full-text + RAG search + citations** (reusing the
   Context Engine) and curated **learning paths**, plus the **Doc/Media Render pipeline**
   (readability reading-view + optional **TTS audio** via a **pluggable local/free-first provider**,
   no overflow credits), **text + audio cached on the coordinator** for offline **read-or-listen** on
   the phone (§2, §3, §10, §11.13, M9; DESIGN §12 #30–31).
7. The **provisional/pluggable** stance — every component a candidate, winners chosen by the
   **Comparison/Eval Harness** (§12); **reuse first, invent only on demonstrated need** (§13,
   DESIGN §12 #29); no named tool asserted as the choice.

→ Detailed class/protocol/schema design is in `LLD.md`; the smart layer in `INTELLIGENCE.md`;
work-management, modes, and quota rationing in `WORKFLOW.md`; sourced grounding in `RESEARCH.md`.
