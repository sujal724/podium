# Intelligence Layer — The Smart Layer

**Status:** Draft for approval · **Date:** 2026-07-24 · **Author:** Claude (with sujal)

The intelligence layer is **the core value-add** (`DESIGN.md` §2, decision 14): everything that makes
the fleet *smarter than any one CLI alone* — a codebase graph, deep RAG with rerankers, cross-run
memory, self-maintaining additive guidelines, an independent verifier, MoE-over-agents routing, and a
small-but-real ML/RL substrate. This doc specifies those components, their interfaces, and — above
all — **how each is chosen by benchmark rather than assumed**.

> **The governing rule (decision 19).** No premature component decisions. Every model,
> codebase-graph, embedder, reranker, retrieval strategy, and routing policy is a **candidate behind
> an interface**. A first-class **Comparison / Eval Harness** (§13) picks winners head-to-head,
> **empirically and later**. Where this doc names a tool (`codegraph`, a given embedder, an RRF
> constant), read it as *"one candidate the harness will weigh"* — never *"the choice."*

> **Companion docs:** `DESIGN.md` (vision, decisions) · `HLD.md` (architecture, Capability Registry) ·
> `LLD.md` (schemas, interfaces) · `WORKFLOW.md` (autonomy, quota scheduler, coordination) ·
> `RESEARCH.md` (sourced grounding). Citations below point to `RESEARCH.md §N` or primary URLs.
> "Fact" = verified in `RESEARCH.md`; "Design intent" = our proposed design, not yet benchmarked.

---

## 1. Thesis & gap analysis

**Thesis.** The official CLIs (Claude Code, Codex, Gemini) are excellent *single-agent* coders. They
do **not** provide the *fleet-level intelligence* that turns three good agents into one system that
gets measurably better at *your* codebase over time. We build exactly the layer the field leaves
thin — and nothing it already solves.

`RESEARCH.md §4` surveyed the prior art and found **five thin areas** ("what we build"). Each maps to
a section here:

| # | Gap the field leaves (`RESEARCH.md §4`) | What the CLIs give you today | What we add | Section |
|---|---|---|---|---|
| 1 | **Capability / MoE routing** — nobody routes a task to the *right* agent/model by task-type, cost, quota, or track record; everyone runs a fixed default or naive best-of-N. | One model per invocation; `--model`/`--effort` flags you set by hand. | Capability/cost/quota/track-record **gate** over the fleet; sparse route vs ensemble+judge vs abstain. | §6 |
| 2 | **Graph-aware decomposition & semantic conflict avoidance** — worktrees isolate files but agents still collide *semantically*. | File-level isolation (git worktrees). | A real **codebase graph** that partitions work by blast-radius so parallel tasks don't clobber each other's semantics. | §3.1, §9 |
| 3 | **Shared RAG / memory across the fleet** — most agents start cold every task. | Per-session context; per-repo `AGENTS.md`/`CLAUDE.md`. | Cross-run **memory**, shared **deep-RAG**, self-maintaining **guidelines** — one brain all workers read. | §3.2, §3.3, §4 |
| 4 | **A real verifier in the loop** — most tools merge on green CI or eyeball. | You review diffs; CI runs tests. | An **independent verifier/critic** that scores runs, drives verify→repair, gates "done" and best-of-N, and doubles as the RLAIF reward. | §7 |
| 5 | **Utilization / scheduling intelligence** — "keep the best agent busy within perishable quota, never overflow" is essentially unsolved. | Rate-limit errors; manual retries. | A **quota-aware scheduler** the router feeds (bandits-with-knapsacks); detailed in `WORKFLOW.md`, informed by §6/§11 here. | §6, §11 |

> **Mid-2026 correction (`RESEARCH.md §7.5`).** Two of these five have since been *overtaken* and are
> now **table stakes**, not open gaps: **#4 verifier-in-the-loop** is common (Cursor Bugbot, bernstein,
> kodo, Qodo judge, Amp oracle) — our still-thin angle is only the *scored best-of-N gate* + verify→
> repair; and **#3 shared RAG/memory** is *filling in fastest* (Linear agent-memory beta, Devin
> DeepWiki). The genuinely-unique gaps remain **#1 MoE routing, #2 graph-based conflict-avoidance, and
> #5 quota-aware utilization** — plus the free-CLI-subscription economics, owned work-management, and
> learning pillars in `RESEARCH.md §7.5`.

Everything else in `RESEARCH.md §4` ("what we reuse") — tmux/PTY, worktrees, watchdog, atomic task
claiming, best-of-N plumbing — we **clone, not reinvent** (decision 13, evolution principle).

---

## 2. Design principles

Six principles constrain every component below.

1. **Pluggable providers + a Comparison/Eval Harness (decision 19).** Graph, embedder, reranker,
   router, and Reasoning Provider are each an interface with ≥2 candidates. The harness (§13) selects
   winners per-repo, per-task-type, empirically and *later*. Heuristic-first cold start; trained/tuned
   when data is ready.
2. **Delegate-first but authoritative (decision 13; `RESEARCH.md §1.4`).** Probe each CLI's *native*
   features via the **Capability Registry** (memory files, MCP, subagents, web search, structured
   output) and **use them**; keep our layer the **source of truth**; shim only gaps. **MCP is the
   universal injection mechanism** — the graph, RAG, memory, and guidelines are exposed once as MCP
   tools and pulled by every agent (`RESEARCH.md §1.4`; all three CLIs are confirmed MCP hosts).
3. **Cite sources in outputs.** Retrieval results, verifier findings, and guideline proposals carry
   **provenance** (file:line, chunk id, memory id, or source URL). An agent that acts on retrieved
   context can show *why*. This is the same discipline this doc follows (fact vs. design intent).
4. **Teach / expand / unbiased.** The layer explains its reasoning ("why this route?", "why this
   snippet?") to the human, *expands* the human's intent rather than narrowing it, and stays
   **unbiased across workers** — no CLI is privileged except where quota/ToS posture demands it
   (`RESEARCH.md §2`). Coordination & narration of these signals is the **Coordination & Learning**
   layer's job (`WORKFLOW.md §7`).
5. **Provisional until proven.** Anything not yet benchmarked is flagged **(design intent)** or
   **(research spike)** and gated behind a flag with kill-criteria (§14). No component asserts itself
   as final.
6. **ML-first; the LLM only when necessary, and then the *right* one (decisions 40, 12).** User-state
   detection (§11), the learner model / knowledge tracing (§11), MoE routing (§6), outcome prediction,
   prioritization, and rot/anomaly detection are **real trained ML/RL/DL models** (small, local) —
   **not** LLM prompts. Reserve the **Reasoning Provider** (§8 — an LLM that spends perishable quota,
   decision 20) for what genuinely needs language understanding/generation (planning, *critical*
   listwise rerank, clarify/verify/distill). Default to a cheap learned model or heuristic; escalate
   to an LLM only when it earns its cost — and then pick the **correct** LLM, **preferring local/free
   models when adequate** (both first-class; more expected over time), the premium subscription CLI
   only when truly needed. **No LLM fine-tuning** (decision 15).

---

## 3. Knowledge layer

Three stores form the shared brain: the **Codebase Graph** (structure), the **Memory Store**
(episodic/semantic cross-run knowledge), and the **Guidelines Engine** (normative rules). All three
are exposed to workers over MCP and are **human-editable**.

### 3.1 Codebase Graph — provider-pluggable, none assumed

**What it is.** A queryable graph of the repository: symbols and files as **nodes**, relationships as
**edges**.

| Element | Kinds |
|---|---|
| **Nodes** | file, module, class, function/method, symbol/definition, (optional) test, (optional) doc chunk |
| **Edges** | `defines`, `imports`, `calls`, `references` (extensible: `overrides`, `tests`, `configures`) |
| **Derived** | **repo-map** (importance ranking, e.g. PageRank/centrality over the call graph — *candidate* algorithm); **blast-radius** (transitive callers/callees/impact of a change) |

**Provider interface (design intent).** The graph sits behind a `GraphProvider` interface so the
harness can swap implementations:

```python
class GraphProvider(Protocol):
    def build(self, repo: Path) -> GraphHandle: ...
    def sync(self, changed: list[Path]) -> None: ...          # incremental re-index
    def neighbors(self, node: NodeId, edge: EdgeKind, depth: int) -> list[Edge]: ...
    def callers(self, sym: NodeId) -> list[NodeId]: ...
    def callees(self, sym: NodeId) -> list[NodeId]: ...
    def blast_radius(self, changed: list[NodeId]) -> Subgraph:  # for conflict avoidance + review
        ...
    def repo_map(self, budget_tokens: int) -> RepoMap: ...     # importance-ranked skeleton
```

**Candidates the harness weighs (none is the choice — decision 19, `RESEARCH.md §5`):**

| Candidate | Basis | Notes |
|---|---|---|
| **Own tree-sitter graph** | multi-language parse → our node/edge model | full control; `RESEARCH.md §0` lists tree-sitter in the stack |
| **SCIP-based** | `scip-python` / `scip-typescript` precise indexers | exact symbol resolution; installed (`RESEARCH.md §0`) |
| **`codegraph`** | v1.1.0 daemon + MCP server; `callers/callees/impact/affected` | installed & MCP-native, but **a candidate, not a decision** (`RESEARCH.md §5` — "may or may not win; do not assume") |
| **Others / LSP-backed** | language servers, hybrid | open |

**How it's benchmarked (§13).** Head-to-head on: symbol-resolution precision/recall, incremental
`sync` latency, blast-radius accuracy against known change sets, and **downstream task success** when
the graph feeds retrieval (§4) and decomposition (§9). The winner may differ **per language/repo**.

**Why it matters.** Gap #2: worktrees isolate files, not semantics. Blast-radius partitioning lets the
decomposer hand parallel agents *non-overlapping* subgraphs, and lets the verifier scope review to
what a change can actually affect.

### 3.2 Memory Store — cross-run, human-editable

A durable store of what the fleet has learned, persisted in SQLite (`DESIGN.md §9`) and surfaced over
MCP. Six **memory kinds** (decision 14; `DESIGN.md §3` map):

| Kind | Example | Written by | Lifecycle |
|---|---|---|---|
| **decision** | "we chose SQLite over Postgres for the daemon store" | distiller (§9), human | durable; supersede-able |
| **convention** | "all timestamps are UTC ISO-8601" | distiller, human | durable |
| **preference** | "sujal prefers small diffs, no drive-by refactors" | learned + human | durable |
| **failure** | "migration X deadlocks under load — don't retry naively" | verifier (§7), watchdog | durable; high-signal |
| **fact** | "the auth service lives in `services/auth`" | graph + distiller | refreshable |
| **pointer** | "see RESEARCH.md §3 for the quota model" | any | link/provenance |

**Properties.** Cross-run (survives session/worktree churn); **human-editable** (you can add, edit,
or delete any memory — it is *your* brain); provenance-carrying (each memory cites its source run,
file, or URL); and **scoped** (workspace / project / repo / global). Retrieval (§4) treats memories as
first-class chunks with their own recency/importance signals.

**Delegate-first.** Where a CLI has native memory (Claude `CLAUDE.md`/memory, Gemini/Codex `AGENTS.md`),
the Guidelines Engine (§3.3) **materializes** the relevant subset *into* that native surface so the
worker reads it natively — while our store stays authoritative (decision 13).

### 3.3 Guidelines Engine — the centerpiece of "gets smarter"

**What it is.** An **additive, self-proposed, human-editable** ruleset that accumulates the operating
knowledge of your codebase and is **materialized into native memory** (`AGENTS.md` / `CLAUDE.md` /
Gemini settings) so every worker follows it *without* us re-prompting. This is the most visible way
the system "gets smarter" over time (decision 14).

- **Additive.** Rules are appended and refined, never silently overwritten; conflicts are surfaced to
  the human, not auto-resolved.
- **Self-proposed.** The distiller (§9) and verifier (§7) *propose* new guidelines from repeated
  outcomes ("three tasks failed lint rule X → propose a guideline"). Proposals are **suggestions**,
  gated by human approval (or an autonomy gate, `WORKFLOW.md §3`).
- **Human-editable.** You own the ruleset; edit or veto anything. (Ties to co-editing, decision 16.)
- **Materialized.** On task dispatch, the engine writes the scoped, budget-fit guideline set into the
  worker's native memory surface — MCP for tools, file for `AGENTS.md`-style memory.

```
outcome signals ──▶ distiller/verifier ──▶ PROPOSED guideline ──▶ human/gate approval
                                                                        │
                              native AGENTS.md / CLAUDE.md  ◀── materialize (scoped, budgeted)
```

**Provenance & citation.** Every guideline records *why it exists* (the runs/failures that spawned it),
so it can be audited and — if it stops helping (rework-rate doesn't fall, §10) — retired.

---

## 4. Context Engine & deep-RAG toolkit

The Context Engine assembles the right, cited context for a task under a token budget. It is a
**pipeline of pluggable stages** (design intent); each stage is a swappable provider the harness (§13)
benchmarks. The distinguishing move is **graph-expansion** — retrieval follows the code graph, not
just text similarity (a user priority, gap #2/#3).

```
query
  │  ① DECOMPOSE      split intent into sub-queries (Reasoning Provider, §8)
  ▼
  ├─ ② HYBRID RETRIEVE  lexical (BM25 / SQLite FTS5)  ⊕  dense (embeddings, pluggable §4/§12)
  │
  ▼  ③ RRF FUSE       reciprocal-rank fusion of the two lists  (candidate fuser)
  │
  ▼  ④ GRAPH-EXPAND   pull 1–2 hops along callers/callees/imports/blast-radius (§3.1)
  │
  ▼  ⑤ RERANK         two-stage: local cross-encoder → LLM listwise for critical (§5)
  │
  ▼  ⑥ BUDGET-FIT     pack to the token budget; attach provenance to every snippet
  ▼
context pack  (each snippet cites file:line / chunk id / memory id / URL)
```

**Stage notes (all providers pluggable; none assumed):**

| Stage | What it does | Candidates / knobs |
|---|---|---|
| ① Decompose | break a task into retrievable sub-questions | Reasoning Provider prompt (§8); heuristic vs LLM |
| ② Hybrid retrieve | lexical **⊕** dense in parallel | BM25/FTS5 (`RESEARCH.md §0`) ⊕ `sqlite-vec`/`fastembed`/ONNX embedders — **candidate** embedders (§12) |
| ③ RRF fuse | rank-fuse the two lists | RRF (candidate); constant `k` tuned by harness |
| ④ **Graph-expand** | 1–2 hop expansion along the code graph | depth, edge-kinds, and importance-weighting are tunable; **the code-aware differentiator** |
| ⑤ Rerank | precision re-scoring | §5 — cross-encoder + LLM listwise, both pluggable |
| ⑥ Budget-fit | pack to budget, dedup, order | budget from Reasoning Provider / worker context window |

**Fresh-context resets & rot detection (decision 14; DESIGN "fresh-context resets").** Long sessions
accumulate stale or contradictory context ("context rot"). The engine:
- **detects rot** — signals include contradiction with memory/guidelines, staleness vs. the graph's
  last `sync`, and declining verifier scores across turns;
- **resets** — rebuilds a clean context pack from the authoritative stores rather than carrying a
  polluted transcript forward (this pairs with the watchdog's auto-compact, `RESEARCH.md §4`).

**Per-stage scoping.** Every stage is scoped (workspace / project / repo / task) so retrieval never
leaks across projects, and memories/guidelines are pulled at the right granularity (§3).

**Same engine powers Library search (`DESIGN.md` decision 30).** The code-aware RAG pipeline above is
also the retrieval engine behind the **Library & Knowledge layer ("reading room")** — the same hybrid
retrieve → RRF → graph-expand → rerank → budget-fit stages run over the aggregated corpus of **all
managed-project docs** (repo docs + auto-generated docs/plans/handoffs), the **system's own docs**, and
curated **reading materials** (attached links, theoretical books/PDFs, introductory guides, expert
explainers). It stays **code-aware** (graph-expansion still applies where a library item maps onto the
graph) and **provisional & pluggable** (every stage remains a harness-benchmarked candidate, §13; no
LLM fine-tuning, decision 15). Above all, **citations everywhere**: every Library answer carries
provenance (file:line / chunk id / memory id / source URL, §2) so the human can **verify** — the same
cite-sources discipline this doc holds itself to. This overlaps Resource Ingestion (`DESIGN.md §6`) and
Coordination & Learning (§10 below; `WORKFLOW.md §7`): library items ↔ task resources ↔ bookmarks.

---

## 5. Reranking — pluggable, two-stage, benchmarked

Reranking is where retrieval precision is won. Two stages, both **provider-pluggable** and
**benchmarked** (§13); neither vendor/model is asserted.

| Stage | Role | Default candidate | When |
|---|---|---|---|
| **Stage-1: local cross-encoder** | cheap, fast precision pass over fused candidates | a local ONNX cross-encoder (candidate) | **always** — no quota cost |
| **Stage-2: LLM listwise** | expensive, high-precision ordering for critical tasks | Reasoning Provider (§8) listwise prompt | **only** for high-stakes / high-blast-radius tasks (§3.1), gated by cost/quota |

```python
class Reranker(Protocol):
    def rerank(self, query: str, candidates: list[Chunk], k: int) -> list[Scored]: ...
```

**Selection is empirical.** The harness scores rerankers on nDCG / MRR against labeled retrieval sets
**and** on end-task success and cost, per repo/task-type. Stage-2 is invoked *sparingly* — its LLM
calls draw on the Reasoning Provider and thus the quota budget (`WORKFLOW.md`; `RESEARCH.md §3`), so
the router (§6) and quota scheduler decide when the precision is worth the spend.

---

## 6. MoE-over-agents — the routing gate

**Gap #1 (`RESEARCH.md §4`).** Nobody routes a task to the *right* agent/model by task-type, cost,
quota, or track record. Our **MoE-over-agents** gate does. The "experts" are the fleet: Claude / Codex
/ Gemini (each interactive+headless, `RESEARCH.md §2`, decision 21), plus effort/model variants and
internal capability agents.

**The gate is capability / cost / quota / track-record aware:**

| Signal | Source |
|---|---|
| **capability** fit (task-type ↔ agent strengths) | Capability Registry (`HLD §6`) + track record |
| **cost** | per-token / per-message estimate |
| **quota** | live utilization gauge + weekly-pacing state (`RESEARCH.md §3`; `WORKFLOW.md`) |
| **track record** | outcome history per (agent, task-type) — feeds the bandit (§11) |

**Four routing regimes:**

1. **Sparse route (default).** Pick the single best expert for the task — the MoE analogy: activate
   *one* expert, not the whole fleet. Cheapest; the standard path.
2. **Ensemble + judge (best-of-N).** For high-stakes/ambiguous tasks, run N experts in parallel and
   let the **Verifier (§7)** select the winner. This is **throw-away minimization** (`RESEARCH.md §4`;
   `WORKFLOW.md`): spend extra compute only where the expected quality gain justifies it.
3. **Abstain / escalate.** If no expert clears a confidence/quota threshold, **defer** — escalate to a
   human task (decision 11) or wait for quota (learning-to-defer, §11).
4. **Route off the code harness — "not-a-coding-task" is a first-class outcome (`DESIGN.md` decision
   37).** Not every task is a coding task, and the gate may conclude *no code agent is the right
   answer*. The classifier can route instead to a **human task** (needs human judgment/decision or a
   manual/external action), an internal **reasoning/research** agent (research, reading, design), an
   **external tool** (a non-code capability), or a **decide/clarify** step — rather than forcing a code
   agent onto a non-coding problem ("everything looks like a nail"). **Abstention/escalation here is a
   correct answer, not a failure.** When *nothing adequate exists* across the fleet or tools, the gate
   proposes **inventing** a new capability (decision 29) rather than faking it with the harness. This
   regime remains **provisional & pluggable** — the not-a-coding-task classifier is a **candidate** the
   harness (§13) tunes, heuristic-first at cold start.

**The gate is human-state-aware (`DESIGN.md` decision 36, revised).** Beyond capability/cost/quota/
track record, the gate weighs the **human's current state and availability** (the **User-State model**,
§11) — but this drives **prioritization and communication calibration, *not* wider autonomy**. The
scope of what may run autonomously stays governed by the gates/design: the system **only ever runs work
already designated autonomous** (decision 7's gated spectrum). Low, varying, or absent human energy
**never** makes the gate do *more* on its own; instead it **re-prioritizes** *which* already-autonomous
work to run and **re-surfaces** *what* to show the human, and it **calibrates communication** (how much
to explain, when to ask, how to phrase) — right-timing any human task or clarify step to the human's
state rather than hard-blocking on a constant oracle. Questions/approvals **queue**, never stalling
runnable work; the system **degrades gracefully** when the human is away (durable auto-resume) without
expanding autonomy scope.

```python
def route(task, fleet, quota, verifier):
    scores = gate.score(task, fleet)                 # capability×cost×quota×track_record
    if scores.best.confidence < τ_abstain:
        return Escalate(reason="low confidence / quota", to="human")   # learning-to-defer §11
    if task.stakes >= τ_bestofN and quota.affords(N):
        cands = run_parallel(top_k(scores, N))
        return verifier.select(cands)                # ensemble + judge  (best-of-N)
    return run(scores.best.expert)                   # sparse route (default)
```

**Ties to:** the **Verifier** (§7, selects best-of-N and gates "done"); the **Quota Scheduler**
(`WORKFLOW.md`, bandits-with-knapsacks, §11) which the gate must respect — **never spend overflow/API
credits** (decision 20, `RESEARCH.md §3`). Cold-start is **heuristic-first** (Capability Registry
priors); the contextual bandit (§11) takes over as track-record data accrues.

### 6.1 Team-aware composition & peer routing *(design intent; DESIGN decision 24)*

The gate is not confined to picking a single best expert. For richer tasks it can **compose a team** —
selecting both **roles** and **members**: a planner, one or more implementers, a reviewer, a verifier,
and a tester — drawn from external CLIs, internal capability agents, and **workflow agent-peers**
(`DESIGN.md` decision 24, Agent Teams). Having composed the team, the gate **routes among the peers**
in a fan-out → cross-check → synthesize pattern: parallel peers attempt or critique in parallel, their
outputs are cross-checked against each other, then reconciled into one result.

The **Verifier (§7)** is the gate on team output: it **scores and gates** the synthesized result and
drives **best-of-N selection among peers** (the same throw-away-minimization mechanism as regime 2,
now applied across a team rather than N lone experts). Composition remains **provisional and
pluggable**: the team-composition policy (which roles, how many peers, when to fan out) is itself a
**candidate** the Comparison / Eval Harness (§13) tunes per repo/task-type, heuristic-first at cold
start and bandit-driven (§11) as track record accrues — never asserted as the choice (decision 19).

---

## 7. Verifier / Critic

**Gap #4 (`RESEARCH.md §4`; see the §1 mid-2026 correction).** Basic verification is now **table
stakes** (`RESEARCH.md §7.5`) — most tools do *some* self-review. Our differentiator is a **dedicated,
independent verifier/critic** separate from the agent that did the work (so it can't rubber-stamp
itself), that **scores** runs and **gates best-of-N** selection + verify→repair.

**Four jobs:**

1. **Independent verification.** Score a run against the task's acceptance criteria, tests, the
   graph's blast-radius (did it touch what it shouldn't? §3.1), guidelines (§3.3), and retrieved spec.
   Output: a score + **cited findings** (provenance, §2).
2. **Verify → repair loop.** On failure, feed findings back for a bounded number of repair attempts
   before escalating (`DESIGN.md §5` autopilot loop; `WORKFLOW.md §5`).
3. **Gates autonomous "done" and best-of-N selection.** Autopilot may only mark a task done when the
   verifier passes its gate (decision 7, gated spectrum); and it **selects the winner** among ensemble
   candidates (§6) — this is the mechanism behind **throw-away minimization**.
4. **RLAIF reward model.** The verifier's scores are the **reward signal** for the ML/RL substrate
   (§11, decision 15) — verifier-as-reward.

```python
class Verifier(Protocol):
    def verify(self, task, artifact, ctx) -> Verdict:   # score + cited findings + pass/fail
        ...
    def select(self, candidates: list[Artifact]) -> Artifact:   # best-of-N judge
        ...
```

**Independence & pluggability.** The verifier runs on the **Reasoning Provider** (§8) and *should*
prefer a different model/agent than the producer (bias reduction). Its scoring rubric and model are
**pluggable and benchmarked** (§13) — including future execution-grounded / differential verification
(§14 spike). It backstops the honesty of the whole flywheel: reward-model quality caps RLAIF quality
(§11 dangers).

---

## 8. Reasoning Provider — smart-pick interface

Intelligence tasks (decompose, rerank, clarify, verify, distill) need an LLM. Rather than a hard
dependency, the **Reasoning Provider smart-picks** per call (decision 12; `DESIGN.md §7`;
`RESEARCH.md`) among three source classes:

| Source | Use when | Cost / ToS |
|---|---|---|
| **A coding CLI itself** (Claude/Gemini/Codex as an LLM) | quality matters and subscription quota is available; keeps us on ToS-clean subscription auth | draws on the perishable 5h/weekly pool (`RESEARCH.md §3`) — pace it |
| **Free API** (OpenRouter free / Gemini free tier) | high-volume, low-stakes calls (bulk rerank, distill); saves subscription quota | published free tiers only (decision 2); **no overflow credits** |
| **Local model** | offline, private, or when both above are quota-constrained; deterministic cheap passes | electricity only; latency/quality tradeoff |

```python
class ReasoningProvider(Protocol):
    def complete(self, task: ReasoningTask) -> Completion: ...   # picks source by policy below
```

**Pick policy (design intent).** Choose by **quality-need × cost × latency × live quota**, honoring
decision 20 (utilise perishable windows, pace the weekly cap, **never** spend overflow/API credits).
High-stakes verification/clarification → a coding CLI; bulk low-stakes passes → free API or local. The
policy itself is a **candidate** the harness/bandit (§11, §13) can tune. Every intelligence component
above (decompose §4, rerank §5, verify §7, distill §9) calls *through* this one interface.

---

## 9. Internal capability agents

The **internal capability agents** (decision 5; `DESIGN.md §4`) are in-process, Reasoning-Provider-
fuelled, and run **even with zero external workers** (`DESIGN.md §2`). They *are* the smart layer's
moving parts.

| Agent | Job | Feeds |
|---|---|---|
| **Graph-builder** | build/`sync` the codebase graph (§3.1) | Context Engine, decomposer, verifier |
| **Retriever / Reranker** | run the deep-RAG pipeline (§4) + reranking (§5) | every worker's context pack |
| **Clarifier** | detect ambiguity, ask the human (uniform question interface, `HLD §7`) | task intent, before dispatch |
| **Verifier** | independent verification + repair + best-of-N judge (§7) | autonomy gates, RLAIF reward |
| **Distiller** | turn finished runs into memories (§3.2) & proposed guidelines (§3.3) | the "gets smarter" flywheel |
| **Updater / Watcher** *(self-maintenance, decision 22)* | track evolving CLI/model/capabilities → **Capability Registry** | routing (§6), delegate-first (§2) |
| **Self-Diagnostic** *(self-maintenance, decision 22)* | read internal error logs, debug failures | reliability; dogfooded on this system first (`RESEARCH.md §6`) |

The two self-maintenance agents (decision 22, `RESEARCH.md §6`) are **dogfooded on this orchestrator
first** and **generalizable later** to the external codebases the system manages.

---

## 10. Metrics & observability catalog

Metrics serve two masters: **the human** (is this helping me?) and **the ML/RL models** (training data,
§11). Two categories (decision 17):

**Human-efficiency metrics**

| Metric | Meaning |
|---|---|
| **rework-rate trend** *(headline)* | fraction of agent output later reverted/redone; **a falling trend is *the* "getting smarter" signal** (decision 17) |
| human-touch per task | approvals/edits/interventions needed |
| time-to-first-useful-artifact | latency from task → something you can use |
| clarification rate | how often the clarifier must interrupt you |
| teaching signal | did narration/explanations reduce your questions over time |

**Agent-efficiency metrics**

| Metric | Meaning |
|---|---|
| task success / verifier pass-rate | first-pass and post-repair |
| throw-away rate | best-of-N candidates discarded (§6/§7) — minimize |
| retrieval quality | nDCG/MRR, context-pack hit-rate (§4/§5) |
| route accuracy | did the chosen expert win vs. alternatives (§6) |
| **live quota-utilization gauge** | perishable 5h burn + weekly-pacing state (`RESEARCH.md §3`; decision 20) — is capacity being used, not wasted, and not overflowing |
| repair-loop depth | verify→repair iterations per task |

**Learning-path companion (`DESIGN.md` decision 31; the teach-and-expand rule, principle 4).** The
teaching signal above is produced by a **learning companion** that builds a **learning path**
(**introductory → expert**, **unbiased**, **cited**) over the Library corpus (§4). It uses the same
retrieval engine plus the **Reasoning Provider (§8)** to sequence materials and generate explainers,
carrying **provenance on every step** so the human can verify (no privileged worker/source; principle
4). The **curation/sequencing policy is provisional & pluggable** — a **candidate** the **Comparison /
Eval Harness (§13)** tunes per learner/topic (heuristic-first cold start), scored against the teaching
signal (does the path lower questions over time?), with **no LLM fine-tuning** (decision 15). This is
the teach-and-expand rule delivered as a product feature.

**How the user sees it (decision 18; `DESIGN.md §8`).** Efficiency dashboards + a **live
quota-utilization gauge** in the TUI first (Textual), rich desktop later (visx). The **rework-rate
trend line** is the front-page headline; the quota gauge shows the reservoir draining on-pace, never
front-loaded (decision 20). Narration of *why* (route reasons, verifier findings) is the Coordination
layer's surface (`WORKFLOW.md §7`).

**Mission-control consumption (`DESIGN.md` decision 25).** The **Overall Management Layer
(mission-control)** consumes this metrics catalog plus the **live quota-utilization gauge** to give the
operator a single **cross-workspace / cross-team** view — aggregating rework-rate, verifier pass-rate,
and quota burn across all teams and sessions — on **desktop and mobile** alike, the top-level surface
they monitor and steer from.

---

## 11. ML/RL systems (real but small)

**Decision 15: real but small ML/RL — and NO LLM fine-tuning in v1.** We do not fine-tune the coding
LLMs. We *do* train small models on the metrics flywheel (§10). Everything is **heuristic-first at
cold start** and trained **only when data is ready** (design intent).

| System | Model class (candidate) | Purpose | Reward / label |
|---|---|---|---|
| **Contextual-bandit router** | **LinUCB / Thompson sampling**; **non-stationary** variants (evolving models); **bandits-with-knapsacks** for quota | the MoE gate (§6) — learn which expert wins per context under a quota budget | verifier score + rework (§7/§10) |
| **Outcome predictors** | small classifiers/regressors | predict success / cost / repair-depth *before* running → informs route & best-of-N | historical outcomes |
| **Learning-to-defer** | defer/abstain classifier | when to escalate to a human or wait for quota (§6 abstention) | downstream human-correction cost |
| **Offline RL** | **IQL / CQL / Decision Transformer** | learn scheduling/routing policy from *logged* runs without risky online exploration | logged trajectories + verifier reward |
| **RLAIF** | reward model = **the Verifier (§7)** | align routing/repair toward verified-good outcomes | verifier-as-reward |
| **Learner model** *(personalization ML; `DESIGN.md` decision 33)* | **Knowledge Tracing** — **BKT / DKT** and the programming-specific **[Code-DKT](https://arxiv.org/pdf/2112.08273)** | model *what the user understands* → sequence teaching and what-to-teach-next (§10) | **first-party signal**: the user's actual edits, questions, and the agents' traces |
| **User-State model** *(state/engagement ML; `DESIGN.md` decision 38)* | small state estimator over communication features (style/clarity/focus/verbosity; scattered ↔ to-the-point) | infer the user's *current state* → **prioritization** (tee up / defer) + **comms calibration** (how much to explain, when to ask) — **not** autonomy scope (§6, decision 36) | **only the human's own words** (authorship provenance, decision 39) — never agent output |

**Learner model — proven technique, first-party twist (`RESEARCH.md §7.4`).** The **learner model** (the
personalization ML of decision 33 that models what the user understands) is implemented via **Knowledge
Tracing** — **Bayesian Knowledge Tracing (BKT)**, **Deep Knowledge Tracing (DKT)**, and the
programming-specific **[Code-DKT](https://arxiv.org/pdf/2112.08273)** — but trained on **first-party
signal** (the user's real edits, questions, and the agents' traces) rather than quiz answers. It is
**provisional & pluggable**: the exact model is a **candidate** the **Comparison / Eval Harness (§13)**
tunes, heuristic-first at cold start, with **no LLM fine-tuning** (decision 15). It feeds the
learning-path companion (§10).

**User-State model — infers state from *how* you communicate (`DESIGN.md` decision 38).** A distinct,
lightweight model infers the operator's **current state / engagement / availability** as a
*time-varying* signal — read from **how they're communicating**: message style, clarity, focus, and
verbosity, on a spectrum from **"very off"** (scattered) to **"very to the point"** (sharp). It is kept
**separate** from the CS/SWE **knowledge** learner-model above (decision 33): that one models *what the
user understands*; this one models *state*, not knowledge. The state signal is used **only for
prioritization + communication calibration** — right-sizing and right-timing asks, tuning how much to
explain and when to ask — and is what makes the routing gate **human-state-aware** (§6): it **does not**
widen autonomy scope (decision 36, revised). It is strictly **human-controlled, transparent, and not
surveillance** (it exists to *serve* the human). Consequently, **human-efficiency metrics must model
variance** (§10): state is expected to fluctuate, so a low-energy week is **normal variance, not
regression** — the metrics catalog reads efficiency against a modeled variance band, never a fixed
baseline. **No LLM fine-tuning** (decision 15).

**Reads only the human's own words — authorship provenance (`DESIGN.md` decision 39).** The User-State
model must consume **only the human's authored text**, *never* agent output — otherwise it would infer
the human's state from words a machine wrote. This depends on **authorship provenance** being tracked
**first-class**: the system always records **who wrote what** — the human vs. *which* agent — across
messages, edits, artifacts, and shared context (generalizing the co-editing/diff attribution of
decision 16 to *all* content). Provenance is captured at write time, not inferred after the fact, and
feeds three consumers: the **routing gate** (§6, so agents can tell another agent's text from the
human's and not mistake it for ground truth), the **User-State model** (which filters to human-authored
words only), and the **metrics catalog** (§10, attributing human- vs. agent-originated work correctly).

**Why bandits/offline-RL and not fine-tuning.** They are small, data-efficient, safe to run online
(bandits) or purely on logs (offline RL), and directly optimize the decisions we actually own
(routing, deferral, scheduling) — *not* the LLM weights (decision 15). Non-stationarity matters because
the underlying CLIs/models **evolve** (Updater/Watcher, §9; decision 13).

**Dangers (design intent — we build guards for each):**

| Danger | How it bites here | Guard |
|---|---|---|
| **Reward hacking** | agents game the verifier instead of solving the task | independent verifier (§7), rotate rubric/model, human spot-audit |
| **Sparse reward** | few verified outcomes early → slow learning | heuristic-first priors; shaped signals (partial credit); free-API bulk labeling |
| **Distribution shift** | model/CLI upgrades invalidate learned policy | **non-stationary** bandits; Watcher-triggered policy reset; offline-RL conservatism (CQL/IQL) |
| **Feedback loops** | router self-confirms a favored expert, starving others | exploration floor (Thompson), track-record decay, harness re-eval (§13) |

All trained-when-data-ready; **nothing blocks v1** — the heuristics ship first and the models slot in
behind the same interfaces (§6/§8).

---

## 12. Embedding adaptation ladder *(provisional / research)*

Retrieval quality (§4) rests on embeddings. This is a **flag-gated ladder** of increasingly ambitious
adaptations — each a **candidate** the harness (§13) must prove beats the rung below; **no LLM
fine-tuning** (decision 15). Climb only if the data earns it.

| Rung | Technique | Cost | Status |
|---|---|---|---|
| 0 | **off-the-shelf embedder** (baseline) | none | default |
| 1 | **per-repo whitening / PCA** | cheap, unsupervised | design intent |
| 2 | **Matryoshka dims** — truncate to fit budget without re-embed | cheap | design intent |
| 3 | **learned linear projection** from usage feedback (clicks/verifier signal) | small train | flag-gated |
| 4 | **graph ⊕ semantic fusion** — node2vec / GNN over the code graph (§3.1) fused with text vectors | medium | flag-gated |
| 5 | **hyperbolic / steering embeddings** | research | **research spike (§14)** |

Each rung is measured on retrieval nDCG/MRR **and** downstream task success (§13); a rung that doesn't
beat its predecessor is dropped. Rungs 4–5 are where the **code-aware** thesis (gap #2/#3) could pay
off most — embeddings that *know the graph*.

---

## 13. Comparison / Eval Harness

**The mechanism that makes decision 19 real.** Every "candidate" in this doc — graph provider (§3.1),
embedder (§4/§12), reranker (§5), router policy (§6/§11), reasoning source (§8), even the verifier
rubric (§7) — is selected **here, empirically, later**, not asserted upfront (`RESEARCH.md §5`).

**How a bake-off runs (design intent):**

```
candidates[]  ×  eval set (per repo / task-type)  ──▶  run under identical context
      │                                                        │
      ▼                                                        ▼
   metrics (§10):  task success · nDCG/MRR · cost · latency · quota · rework
      │
      ▼
   select winner  (per repo × task-type; may differ across languages)  ──▶ registry
      │
      └── periodic RE-EVAL (models/CLIs evolve — Watcher §9, non-stationarity §11)
```

**Metrics** are the §10 catalog plus retrieval-specific nDCG/MRR and cost/latency/quota. **Keeping it
honest:**

- **Held-out / temporal splits** — evaluate on tasks the candidate didn't train on; guard against
  overfitting a favored tool.
- **Blind to vendor** — the harness scores outcomes, not names (principle 4, unbiased).
- **Re-eval on evolution** — winners are **not permanent**; the Watcher (§9) triggers re-benchmarks
  when CLIs/models change (decision 13; non-stationarity, §11).
- **Provenance** — every selection records the eval set, date, and scores, so a choice can be audited
  and rolled back.

The harness is what lets this whole document stay **provisional without being vague**: we *specify
interfaces and how we'll choose*, and let evidence pick the implementations.

---

## 14. Research spikes *(flag-gated, with kill-criteria)*

Ambitious ideas worth trying — each **behind a flag**, each with an explicit **kill-criterion** so a
spike that doesn't pay off is *dropped*, not carried (principle 5). None is on the v1 critical path;
none involves LLM fine-tuning (decision 15).

| Spike | Idea | Kill-criterion |
|---|---|---|
| **Synthetic self-play cold-start** | generate synthetic tasks to warm the bandit/predictors before real data | if synthetic-trained policy doesn't beat heuristics on held-out real tasks (§13) |
| **Sleep-time consolidation** | off-peak, distill runs → memories/guidelines/embeddings (uses idle quota) | if consolidated knowledge doesn't lower rework-rate (§10) |
| **Speculative execution** | pre-run likely-next tasks behind gates during idle quota | if speculative work's throw-away rate exceeds the quota it saves (decision 20) |
| **Execution-grounded / differential verification** | verify by *running* code / diffing behavior, not just reading it | if it doesn't raise verifier precision over the LLM critic (§7) at acceptable cost |
| **Execution-trace RAG** | index runtime traces/logs as retrievable context | if trace context doesn't improve task success (§4/§13) |
| **Time-travel / branching runs** | checkpoint + fork run state to explore alternatives (leverages session `fork`, `RESEARCH.md §1.1) | if branching's compute cost exceeds best-of-N's quality gain (§6) |

Each spike, if it survives its kill-criterion in the harness (§13), graduates from *(research spike)*
to a real candidate behind the same interfaces as everything else.

---

## Appendix — fact vs. design intent

- **Facts** (verified in `RESEARCH.md`): the five thin areas (§1 ← `RESEARCH.md §4`); MCP as the
  universal injection mechanism, all three CLIs are MCP hosts (§2 ← `RESEARCH.md §1.4`); the quota
  model — perishable 5h + weekly cap, no overflow credits (§6/§8/§10 ← `RESEARCH.md §3`); installed
  candidates `codegraph`, `scip-*`, tree-sitter (§3.1 ← `RESEARCH.md §0/§5`); subscription-auth/ToS
  posture (§8 ← `RESEARCH.md §2`); session resume/fork (§14 ← `RESEARCH.md §1.1`).
- **Design intent** (our proposed design, **not yet benchmarked**): all pipeline/interface pseudocode,
  the routing regimes, the adaptation ladder, the ML/RL model choices, and every "candidate" — all to
  be selected by the Comparison/Eval Harness (§13), empirically and later (decision 19).
