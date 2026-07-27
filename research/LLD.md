# Low-Level Design (LLD) — Agentic Coding Orchestrator

**Status:** Draft for approval · **Date:** 2026-07-24 · Companion to `HLD.md`, `DESIGN.md`, `RESEARCH.md`.

Detailed design: module layout, class/interface specs, wire protocol, data models, and
algorithms. **M0–M2 are specified concretely** (built first); **later milestones at interface
level** (refined when reached). Language: Python 3.12 + asyncio, unless noted.

> **Provisional & pluggable (decision-log 19).** Component *implementations* below are **candidates
> behind interfaces** — the **Comparison / Eval Harness** (§15) picks winners head-to-head, later.
> Interfaces are concrete; fluid internals are marked *(provisional)*; no named tool (e.g.
> `codegraph`, a specific embedder/reranker/bandit) is THE choice.
> **ToS-clean (decision-log 2, RESEARCH §2).** Official CLIs, subscription/login auth (no API keys
> for coding workers), one operator, own machine. Never spend overflow / API credits.

---

## 1. Package / module layout

Extends the M0 tree with the work-management, autonomy, intelligence, and maintenance subsystems.
Later-milestone packages are interface stubs until their milestone lands.

```
agent-orchestrator/
├── DESIGN.md HLD.md LLD.md INTELLIGENCE.md WORKFLOW.md RESEARCH.md · pyproject.toml
├── orchestrator/
│   ├── config.py protocol.py ids.py sink.py         # settings·wire·ids·bus (§18,§20)
│   ├── gateway.py brain.py manager.py workspace.py   # WS server·Brain·SessionManager·worktrees
│   ├── state.py                                      # StateStore (SQLite §19; M0 file transcripts)
│   ├── sessions/   # Session ABC + kinds (§2): base·pty·sdk·headless·api·codex_rpc·gemini_acp
│   ├── workers/    # Worker ABC + WORKERS registry + claude·codex·gemini·openrouter (§3)
│   ├── interaction/# M2 uniform question/approval (§7): layer·sentinel·clarifier
│   ├── work/       # Work-management store (§9): store (WorkStore) · queue (atomic claim)
│   ├── tms/        # TMS Bridge (§10): base (TmsAdapter) + linear·jira·github_issues stubs
│   ├── autonomy/   # Autopilot (§8): controller · scheduler (QuotaScheduler) · watchdog · detectors (TaskDetector §8.5)
│   ├── ingestion/  # Resource Ingestion pipeline (§11): fetch→parse→chunk→index
│   ├── capability/ # Capability Registry (§5): probe/version/feature/posture
│   ├── reasoning/  # Reasoning Provider (§6): smart-pick over CLI / free-API / local
│   ├── context/    # Context Engine (§12, M5): graph · rag · engine (provider-pluggable)
│   ├── moe/        # MoE router + bandit (§14)      ├── verify/    # Verifier / Critic (§13)
│   ├── memory/     # Cross-run memory (§13)         ├── guidelines/# Additive → AGENTS.md (§13)
│   ├── evalharness/# Comparison / Eval Harness (§15)├── metrics/   # Observability (§16)
│   ├── maintenance/# Updater/Watcher · Self-Diagnostic (§17)
│   ├── coord/      # Coordination & learning (narration, human-tasks)
│   ├── teams/      # Agent Teams (§22): team · member(role) · coordinator/lead · peers (peer bus) · templates
│   ├── management/ # Overall Management Layer (§23): aggregation + control plane (desktop+phone bind here)
│   ├── remote/     # Mobile/remote path (§24): coordinator proxy · auth · push  *(provisional)*
│   ├── bookmarks.py# Bookmarks store + interface (§26)
│   ├── library/    # Library & Knowledge layer (§27, M9): docsource · index · scope  *(provisional)*
│   ├── media/      # Read-or-Listen pipeline (§28, M9): readability · tts · listen-queue · learning-path  *(provisional)*
│   ├── engine/ templates/ handoff.py editsync.py    # run strategies·templates·handoff·diff (M2)
│   └── agents/     # internal capability agents (graph-builder, retriever, …)
├── clients/cli.py  # reference client (test driver)     └── tests/
```

Console scripts: `orchd` → `orchestrator.gateway:main`, `orch` → `clients.cli:main`.
Optional split topology (decision-log 23): `orchd` runs the **coordinator** role (queue/state/
scheduler/watchdog/notifier) on a free Oracle Cloud micro instance; a **worker host** runs the
PTY/SDK sessions locally. Role is a config flag; *(topology provisional — decided by comparison)*.

---

## 2. Core types — Session base + session kinds (`sessions/`)

All driver kinds implement one contract, so everything downstream depends only on `Session`.
Session kinds map to the four documented drive mechanisms (RESEARCH §1).

### 2.1 Session ABC (`sessions/base.py`)
```python
class Session:
    id: str            # short id, e.g. "s_7f3a"
    label: str         # worker name: "claude" | "gemini" | "codex" | "openrouter"
    kind: str          # "pty"|"sdk"|"headless"|"api"|"codex_rpc"|"gemini_acp"
    status: str        # starting|running|waiting_input|exited|error
    cwd: str
    _sink: Sink
    _buffer: bytearray                          # full transcript, for late-attaching clients

    def info() -> dict                          # {id,label,kind,status,cwd}
    def backlog() -> str
    def _emit_output(text: str)                 # buffer += ; sink(output event)
    def _emit_question(q: dict)                 # sink(question event); status=waiting_input
    def set_status(s: str)
    async def start(initial_input: str | None)  # abstract
    async def write(text: str)                  # abstract — Brain & human share this path
    async def stop()                            # abstract
    # optional (capability-gated): interrupt(), resume(session_id), fork(session_id)

    # live session handoff (§25) — bidirectional driver swap, mid-flight (decision-log 27)
    drive_mode: str    # "interactive" | "autonomous"  — which driver owns write()/gates
    async def promote_to_autonomous(goal: str, gates: list["Gate"])  # interactive → autonomous
    async def takeover_to_interactive()                              # autonomous → interactive
```
`drive_mode` is a per-session state machine (`interactive ⇄ autonomous`) orthogonal to `status`;
transitions swap the *driver* (human/Brain ↔ Autonomy Controller) and the permission/gate policy
without restarting the session (§25).

### 2.2 Session kinds (interface-level; PTY concrete in M0)

| Kind | Class | Backend | Drives | Notes / source |
|---|---|---|---|---|
| `pty` | `PtySession` | `pty.fork` + `execvpe`, `add_reader` | any CLI's real TUI | Live watch + human takeover. **Concrete M0.** |
| `sdk` | `SdkSession` | Claude Agent SDK `ClaudeSDKClient` | `claude` | Persistent, bidirectional; interrupt/queue/live permission requests; `can_use_tool`+`PreToolUse` hooks; `resume`/`fork`; in-proc MCP. RESEARCH §1.1. |
| `headless` | `HeadlessStreamSession` | `claude -p --output-format stream-json --input-format stream-json --verbose` | `claude` | Raw stream-json; `result` line is terminal. **PTY gotcha:** piped stdout is **block-buffered** → looks like a hang; allocate a **PTY** or use the SDK. RESEARCH §1.1. |
| `api` | `ApiSession` | `httpx` SSE | OpenRouter free models | Fuel for internal agents; not a coding-worker default. |
| `codex_rpc` | `CodexAppServerSession` | Codex **app-server JSON-RPC** (`codex mcp`/app-server) | `codex` | Bidirectional control + **approval callbacks**. `exec` has no answer channel; `-a never` fails on approval. RESEARCH §1.2. |
| `gemini_acp` | `GeminiAcpSession` | Gemini **`--acp`** (Agent Client Protocol, JSON-RPC/stdio) | `gemini` | Bidirectional + approval callbacks; `--checkpointing` snapshots. RESEARCH §1.3. |

Common shape for structured kinds: an async reader task decodes backend events
(`assistant`, `tool_use`, `permission_request`, `result`) → normalized `output`/`question`/
`status` events on the sink. `write()` submits a user turn (PTY: bytes + `\r`; structured: a
protocol message). Which kind a worker uses for a given run is chosen by the Capability
Registry (§5) + interactive-vs-headless policy (§8).

### 2.3 ApiSession detail (`sessions/api.py`)
`kind="api"`, no PTY: holds `model` + `messages` + `httpx.AsyncClient`; `start`/`write` append a
turn and `_generate()` POSTs `stream=True`, parsing SSE deltas into `_emit_output`. Endpoint/model/
key from config; OpenRouter etiquette headers (`Authorization`, `HTTP-Referer`, `X-Title`).
**API keys are only ever used for the OpenRouter fuel worker**, never for Claude/Codex/Gemini
coding workers (subscription auth only; decision-log 2).

---

## 3. Worker ABC + registry + adapters (`workers/`)

### 3.1 Worker ABC (`base.py`)
```python
class Worker(ABC):
    name: str            # "claude" | "codex" | "gemini" | "openrouter"
    kinds: list[str]     # session kinds this worker can produce
    def available() -> "Availability"          # auth + ToS posture, not just on-PATH
    @abstractmethod
    def make_session(sid, sink, cwd, prompt, kind=None) -> Session

@dataclass
class Availability:
    ok: bool
    on_path: bool
    auth: str            # "subscription" | "oauth" | "none"
    tos: str             # "clean" | "gray" | "n/a"        (RESEARCH §2 matrix)
    headless_ok: bool    # may this worker run unattended?
    hint: str = ""       # friendly install/login guidance when not ok

WORKERS: dict[str, type[Worker]] = {}
def register(cls): WORKERS[cls.name] = cls; return cls
```

### 3.2 Adapters & posture

| Adapter | Interactive | Structured/headless | `available()` posture (RESEARCH §2) |
|---|---|---|---|
| **ClaudeWorker** | `["claude"]` PTY | `SdkSession` / `HeadlessStreamSession` | subscription (`~/.claude/.credentials.json`, unset `ANTHROPIC_API_KEY`); **ToS clean**; `headless_ok=True`. The safe unattended default. |
| **CodexWorker** | `["codex"]` PTY | `CodexAppServerSession` | `codex login`; **ToS gray**, user opt-in; `headless_ok` = user setting. Rationed to message/rate limits. |
| **GeminiWorker** | `["gemini"]` PTY | `GeminiAcpSession` | Login-with-Google OAuth; **ToS gray**, user opt-in; `headless_ok` = user setting. Rationed to message/rate limits. |
| **OpenRouterWorker** | — | `ApiSession` | API key present; `tos="n/a"` (free models); fuel only. |

`available()` reports the full **auth + ToS posture** so the Autonomy Controller and Capability
Registry can decide whether a worker may be saturated unattended (decision-log 21, RESEARCH §2).
Missing CLI → Brain surfaces the install/login `hint` instead of spawning.

---

## 4. SessionManager (`manager.py`)
```python
class SessionManager:
    sessions: dict[str, Session]
    _sink: Sink
    async def spawn(worker, prompt, cwd=None, kind=None) -> Session:
        w = WORKERS[worker]()
        av = w.available()
        if not av.ok: raise WorkerUnavailable(av.hint)
        sid = new_id("s")
        sess = w.make_session(sid, self._sink, cwd or config.WORKDIR, prompt, kind)
        self.sessions[sid] = sess
        self._sink(session_created(sess.info()))
        await sess.start(prompt)
        return sess
    async def write(sid, text)        # → sessions[sid].write
    async def stop(sid)
    def list() -> list[dict]
    def snapshot() -> dict            # {sessions:[info], backlogs:{sid:text}}
```
On restart the manager can **rehydrate** interrupted sessions from `sessions` rows and
resume/fork where the driver supports it (§21).

---

## 5. Capability Registry (`capability/registry.py`)

Probes each CLI, version-aware, and records what to **delegate to natively** vs. shim
(decision-log 13, RESEARCH §6). Provider-facing interface is concrete; the probe internals
evolve as CLIs change.
```python
@dataclass
class Capability:
    worker: str
    version: str                      # e.g. claude v2.1.218
    features: set[str]                # {"mcp","subagents","stream_json","resume","fork",
                                      #  "web_search","structured_output","worktree","acp",...}
    session_kinds: list[str]
    auth: str; tos: str; headless_ok: bool     # posture record (RESEARCH §2)
    probed_at: int

class CapabilityRegistry:
    def probe(worker) -> Capability   # `--version`, help scan, feature dry-runs
    def get(worker) -> Capability
    def supports(worker, feature) -> bool
    def refresh_all()                 # called by Updater/Watcher (§17)
```
Persisted so the fleet knows its own posture across restarts. The **Updater/Watcher** (§17)
refreshes it when CLIs update.

---

## 6. Reasoning Provider (`reasoning/provider.py`)

Intelligence tasks (planning, reranking, clarifying, verifying, distilling) need an LLM. The
provider **smart-picks** per call among a coding CLI used as an LLM, a free API, or a local
model — by cost, latency, quota, and quality (decision-log 12).
```python
class ReasoningProvider:
    async def complete(task: "ReasoningTask") -> str
    async def embed(texts: list[str]) -> list[list[float]]     # provider-pluggable
    async def rerank(query, candidates) -> list[float]
    def pick(task) -> "Backend"       # policy: cost/latency/quota/quality; quota-aware (§8)

@dataclass
class ReasoningTask:
    kind: str        # "plan"|"rerank"|"clarify"|"verify"|"distill"|"embed"
    prompt: str; budget_tokens: int; quality: str   # "cheap"|"balanced"|"best"
```
Backends are candidates *(provisional)*; the smart-pick policy consults the QuotaScheduler (§8)
so intelligence work never overflows a coding subscription's perishable window.

---

## 7. Interaction Layer (`interaction/`) — M2

One consistent question/approval experience across all agents, including CLIs that don't natively
pause to ask (RESEARCH §1.2–1.3; HLD §6).

- **Native channels first (delegate).** For `sdk` use `can_use_tool` + `PreToolUse` hooks (a hook
  returning `deny`/`ask` is our programmatic answer). For `codex_rpc`/`gemini_acp` use their
  **approval callbacks**. These are the authoritative interfaces when present.
- **Sentinel fallback (`sentinel.py`).** For raw PTY CLIs we don't control, inject a system
  preamble instructing the agent to emit one delimited line when it needs a decision:
  ```
  <<ASK>>{"q":"Which DB?","choices":["pg","sqlite"],"id":"a1","kind":"choice"}<<END>>
  ```
  `kind ∈ {choice,text,approve}`. The parser scans streamed output, de-dupes by `id`, yields a
  `Question`.
- **Block detection.** No output for N ms after a read ending without a newline + per-adapter
  prompt regexes → advisory generic input prompt.
- **Clarifier (`clarifier.py`).** Internal agent (Reasoning Provider) that watches trajectory +
  task and proactively raises questions for CLIs that can't ask at all. Off by default per-agent.

```python
class InteractionLayer:
    def on_output(session, text): ...          # sentinel scan + block detect → _emit_question
    async def answer(session_id, question_id, value):
        # native: route to hook/approval callback; PTY: write(value+"\r"); api: write(value)
```

---

## 8. Autonomy Controller + Quota Scheduler (`autonomy/`)

### 8.1 AutonomyController (`controller.py`)
The autopilot loop, gated by declarative policy. Autonomy is a **spectrum** set per
workspace/project/task (decision-log 7).
```python
class AutonomyController:
    async def tick():
        if not self.scheduler.may_dispatch():  return        # quota gate (§8.3)
        task = await self.queue.claim(worker_posture())      # atomic claim (§9)
        if task is None: task = await self.replenish()        # keep fleet busy
        worker, kind = self.moe.route(task)                   # MoE gate (§14)
        sess = await self.manager.spawn(worker, self.prompt_for(task), task.worktree, kind)
        result = await self.run_to_completion(sess, task)     # watchdog-supervised (§8.4)
        verdict = await self.verifier.check(task, result)     # verify→repair gate (§13)
        if verdict.ok: await self.handoff_or_commit(task, result)
        else:          await self.repair_or_escalate(task, verdict)
        self.scheduler.record(sess.usage())                   # replenish meters
        await self.coord.narrate(task, result)                # coordination/learning
```

**Gates (declarative).** e.g. `human-approve any migration`, `auto-merge if verifier passes and
diff < N lines`, `escalate on ambiguity | risk | verification-failure | cost-threshold`.

**Live-handoff hooks.** `attach(session, goal, gates)` / `detach(session)` let the controller take
or release an **already-running** session mid-flight (promote/takeover, §25) rather than only
spawning fresh ones. **Team hook.** When work is assigned to a **team** (§22), the controller runs
the team via its `TeamCoordinator` (a layer over the same engine/ run strategies) instead of a
single worker; MoE routing (§14) then applies per member.

**Throw-away minimization.** Worktree with **no auto-merge** (verifier gate before merge);
**plan/read-only** first pass for risky tasks; **cheap-model speculation** then escalate only
low-confidence work (Reasoning Provider `quality` tier); best-of-N + checkpoint-merge behind the
verifier (RESEARCH §4).

### 8.2 Task-claim queue
Backed by SQLite with an atomic claim (§9): `UPDATE tasks SET status='in_progress',
assignee=:worker WHERE id=(SELECT id FROM ready_tasks ... LIMIT 1) RETURNING *` in one
transaction so two workers never grab the same task (pattern from amux; RESEARCH §4).

### 8.3 QuotaScheduler (`scheduler.py`) — reservoir / token-bucket (decision-log 20, RESEARCH §3)
Objective: **fully utilise perishable 5-hour windows** (burn-or-lose) while **pacing the weekly
cap** so it drains almost completely just before reset — never overflow, never spend API credits,
regime-aware.
```python
class QuotaScheduler:
    # meters, per worker
    window_5h: Reservoir      # capacity C5, resets 5h after first prompt of the window
    weekly:    Reservoir      # capacity Cw, 7-day rolling
    msg_budget: dict[str,int] # per-worker message/rate budget (Codex/Gemini opt-in)
    regime: str               # "one_pool" (today) | "two_pool" (if SDK-credit split un-pauses)

    def may_dispatch(worker=None) -> bool
    def record(usage)                       # decrement reservoirs from session telemetry
    def pace_target() -> float:
        # weekly budget smoothed across time-to-reset; allow full 5h burn up to
        # min(window_remaining, weekly_pace_allowance). Drain weekly ~0 just before reset.
```
**Pacing sketch:** let `w_left` = weekly units left, `t_left` = seconds to weekly reset. Target
burn rate `r = w_left / t_left`. Each 5h window may spend up to `min(C5_remaining, r * window_len
+ smoothing_slack)`; if ahead of pace, throttle to protect mid-week; if behind, open the throttle
to fully use the current window. Balance is read from session telemetry (no CLI exposes remaining;
`/usage` / `ccusage` are references only — RESEARCH §3). Regime detected at startup and re-probed.

### 8.4 Watchdog + auto-resume (`watchdog.py`)
Self-healing (RESEARCH §4): auto-compact on high context / suggest fresh-context (§12), rate-limit
backoff, idle re-prompt, **auto-resume on limit reset**. On daemon restart it re-dispatches
interrupted `in_progress` tasks and resumes/forks sessions where the driver supports it — unless a
setting disables it (decision-log 23; §21).

### 8.5 TaskDetector — auto-detected tasks into the approval inbox (`autonomy/detectors.py`, decision-log 35)
New work does **not** require manual authoring: small **detectors** watch the running fleet and
**propose** tasks into the `proposed` state (§9) — an **approval inbox**, never straight to `ready`.
Detection is automatic; the *gate* stays human (complements continuous supply, decision-log 8, and
the autonomy gates, §8.1). Interface-level *(provisional)*:
```python
class TaskDetector(ABC):
    label: str           # "verifier"|"gap"|"failure"|"followup"|"ingest"  → tasks.detector (§9,§19)
    async def detect(ctx) -> list["Proposal"]     # scan a signal source → candidate tasks

@dataclass
class Proposal:
    title: str; description: str; detector: str    # origin="auto", status="proposed"
    project_id: str | None; refs: list[str]; priority: int | None   # provenance/citations (§11)

class DetectorRegistry:
    def tick(): 
        for d in self.detectors:
            for p in await d.detect(ctx):
                self.store.propose_task(p, detector=d.label)  # → inbox; emits task.proposed (§18)
```
Candidate detectors *(provisional)*: **verifier** (turn Verifier findings §13 into fix tasks),
**gap** (code TODOs / missing tests / coverage holes via the Context Engine §12), **failure**
(Self-Diagnostic clusters §17 → repair tasks), **followup** (spawn next-step tasks from finished
work / handoffs §25), **ingest** (surface actionable items from newly ingested materials §11).
Detectors run on a timer under the watchdog (§8.4); each proposal lands as a `proposed` task the
operator approves / edits / rejects (§18) before the autopilot may claim it (§8.2).

---

## 9. Work Management store (`work/store.py`) — SQLite

Native Linear-like hierarchy: **workspace → project → task** (decision-log 8). Works standalone
with zero TMS. Tables `workspaces`/`projects`/`tasks`/`task_events` defined canonically in §19 —
`tasks` carries subtasks (`parent_id`), `deps`, `priority`, `status`
(`proposed|backlog|ready|in_progress|blocked|review|done`, extensible toward the
proposed→ready→assigned→running→verifying→review→done/blocked/parked lifecycle), `labels`,
`assignee` (agent name or `human:*`), `resources` (ingested §11), and `workflow` (template).
**Task origin (decision-log 35).** Every task records where it came from: `origin`
(`auto` | `human`) distinguishes an **auto-detected/proposed** task from a **human-authored** one,
with a `detector` label (`verifier|gap|failure|followup|ingest`, NULL for human-authored) naming
the TaskDetector (§8.5) that proposed it, plus `approved_by`/`approved_at` recording who cleared it
out of the inbox. Auto-detected tasks enter as **`proposed`** and **never** go straight to `ready` —
approval is the gate (decision-log 35; §8.5, §18). Interface:
```python
class WorkStore:
    def create_workspace/project/task(...) -> str
    def propose_task(fields, detector) -> str  # origin="auto", status="proposed", detector=…
    def approve_task(id, actor); def reject_task(id, actor); # proposed→ready / proposed→(dropped)
    def update_task(id, **fields); def add_dependency(id, dep)
    def proposed_tasks(scope) -> list[Task]    # the approval inbox (status=proposed)
    def ready_tasks(posture) -> list[Task]     # status=ready & deps done & worker-eligible
    def log_event(task_id, type, actor, data)
```
`approve_task` sets `status=ready`, `approved_by`/`approved_at`, and logs a `task_events` row;
`ready_tasks` excludes `proposed` so the autopilot only ever claims human-cleared work.
`queue.py` layers atomic claim (§8.2) and replenishment hooks over the store.

---

## 10. TMS Bridge (`tms/`)

Bidirectional, optional. TMS is source-of-truth when present; native store otherwise
(decision-log 9). **Works with any, works without any.**
```python
class TmsAdapter(ABC):
    name: str
    def available() -> bool                          # creds/config present
    async def import_issues(project) -> list[Task]   # TMS → native
    async def export_task(task)                      # native → TMS (status/result)
    async def sync(project)                          # reconcile per source-of-truth rules
    def source_of_truth() -> str                     # "tms" | "native"
```
Stubs: `linear.py`, `jira.py`, `github_issues.py` (official APIs only — decision-log 2). Sync
rules: when TMS is truth, native mirrors + augments (never overwrites TMS blindly); conflicts
logged to `task_events` and surfaced as a human task. `tms_links` (§19) maps native↔external ids.

---

## 11. Resource Ingestion pipeline (`ingestion/pipeline.py`)

Each task's reading materials & links become cited context (decision-log 10). Respects
robots/access (decision-log/ToS).
```python
class IngestionPipeline:
    async def ingest(task) -> list["Chunk"]:
        for res in task.resources:
            raw   = await self.fetch(res)      # httpx link OR local file/path read
            doc   = self.parse(raw, res.kind)  # readability/markdown/code extract (provisional)
            chunks= self.chunk(doc)            # span-aware, provenance-tagged
            self.index(chunks, provenance=res) # → chunks table + Context Engine (§12)
        return chunks
```
Each chunk keeps **provenance** (`source`, `url|path`, `span`) so the agent works from **cited**
sources (citations flow into handoffs and verifier evidence). Parsers/extractors *(provisional)*;
chosen by the Eval Harness.

---

## 12. Context Engine (`context/`) — provider-pluggable, M5

Graph provider is a **plugin**; the choice (tree-sitter graph vs. SCIP-based vs. `codegraph` vs.
others) is decided by the Eval Harness — none asserted (decision-log 19, RESEARCH §5).
```python
class GraphProvider(ABC):        # candidates: tree-sitter | scip | codegraph | ...  (provisional)
    def index(repo); def sync(repo)
    def callers(sym); def callees(sym); def neighbors(node, depth)
    def definition(name); def blast_radius(path)

class Retriever(ABC):            # hybrid dense ⊕ lexical
    def retrieve(query, k) -> list[Candidate]     # vector + BM25 → RRF merge → graph-expand

class Reranker(ABC):             # cross-encoder OR free-LLM judge  (provisional)
    def rerank(query, candidates) -> list[Candidate]

class ContextEngine:
    def build(stage, agent, goal, budget) -> str  # scoped, budget-fit, cited context block
    def fresh(session_or_run)                      # drop accumulated context; reseed minimal
    def detect_rot(session) -> bool                # repetition/drift/size heuristics → suggest fresh
```
Embeddings/rerank via the Reasoning Provider (§6). Vector store `sqlite-vec`/sidecar
*(provisional)*.

---

## 13. Memory / Guidelines / Verifier interfaces

```python
class MemoryStore:               # cross-run decisions/conventions/preferences/failures/facts
    def add(kind, text, scope, refs); def query(scope, q, k) -> list[Memory]

class GuidelinesEngine:          # additive, self-proposed, human-editable → AGENTS.md
    def propose(observation) -> Guideline      # never silently rewrites; additive only
    def approved() -> list[Guideline]; def render_agents_md(scope) -> str

class Verifier:                  # independent verification; also RLAIF reward signal
    async def check(task, result) -> "Verdict"     # {ok, score, findings:[...], repair_hint}
```
The verifier gates best-of-N selection and pre-merge (RESEARCH §4). Its score doubles as the
reward model for later RLAIF (decision-log 15; INTELLIGENCE.md). Guidelines are **additive only**
and human-editable.

---

## 14. MoE router (`moe/router.py`) — interface + bandit stub

Route each task to the *right* agent/model by task-type, cost, quota, and track record — our
differentiator (RESEARCH §4). Interface concrete; policy *(provisional)*.
```python
class MoERouter:
    def route(task) -> tuple[str, str]:       # (worker, session_kind)
        # gate: sparse single route vs. ensemble+judge (parallel best-of-N)
        feats = self.featurize(task)          # task-type, blast-radius, posture, quota
        return self.policy.select(feats)      # heuristic v0 → contextual bandit later

class BanditPolicy:                            # provisional (numpy/scipy)
    def select(feats) -> Arm                   # contextual bandit over (worker,kind,effort)
    def update(feats, arm, reward)             # reward from Verifier score + metrics (§16)
```
Quota-aware: the router asks the QuotaScheduler (§8.3) whether a candidate worker `may_dispatch`
before selecting it.

---

## 15. Comparison / Eval Harness (`evalharness/harness.py`)

First-class; picks provider winners empirically and later (decision-log 19, RESEARCH §5).
```python
class EvalHarness:
    def register(slot, candidate)             # slot: "graph"|"embedder"|"reranker"|"router"|
                                              #        "worker"|"retriever"|... ; candidate impl
    async def run(slot, benchmark) -> Report  # head-to-head over a task/dataset benchmark
    def pick(slot) -> str                     # select winner; writes choice to config/registry
```
Until a slot is benchmarked, its default is a labelled candidate, **not** a decision. Nothing in
this doc locks an implementation the harness hasn't chosen.

---

## 16. Metrics / Observability (`metrics/collector.py`)

Track **human and agent efficiency**; the headline "getting smarter" signal is a falling
**rework-rate** trend (decision-log 17). Also training data for the ML models.
```sql
CREATE TABLE metrics(id INTEGER PRIMARY KEY, run_id TEXT, session_id TEXT, task_id TEXT,
                     actor TEXT,     -- "human" | "agent" | "system"
                     kind TEXT,      -- time|tokens|tool_calls|rework|handoff_accept|
                                     -- human_intervention|edit|wait|verify_pass|utilization
                     value REAL, ts INT);
```
Derived gauges: **rework-rate** trend, **utilization gauge** (5h/weekly reservoir fill from
telemetry — §8.3), human vs agent throughput, handoff-acceptance. Utilization is emitted to
clients as a live event (§18).

---

## 17. Self-maintenance agents (`maintenance/`) — decision-log 22, RESEARCH §6

Internal capability agents, dogfooded on this system first, generalizable later.
```python
class UpdaterWatcher:            # tracks evolving CLI/model/capabilities
    async def tick():            # check versions/updates → CapabilityRegistry.refresh_all() (§5)

class SelfDiagnostic:            # reads internal error logs, debugs failures
    async def tick():            # scan logs/events → cluster failures → open a task or repair (§9)
```
Both run on a timer under the watchdog; findings become tasks/guidelines or Capability updates.

---

## 18. Wire protocol (`gateway.py`, `protocol.py`)

WebSocket on `127.0.0.1:8765`, one JSON object per message, no auth (localhost, single-operator).
Extends the M0 message set with work-management, autonomy, quota, and messaging.

### 18.1 Client → Server
| type | fields | effect |
|---|---|---|
| `brain.send` | `text` | → `Brain.handle` |
| `agent.send` | `session_id, text` | direct line into an agent |
| `spawn` | `worker, prompt, cwd?, kind?` | spawn directly (bypass Brain) |
| `answer` | `session_id, question_id, value` | → `InteractionLayer.answer` |
| `stop` / `resize` / `list` | … | session control / winsize / reply `sessions` |
| `work.create` | `entity, fields` | create workspace/project/task |
| `work.update` | `id, fields` | update task (status/priority/deps/resources/assignee) |
| `work.list` | `scope` | reply `work.snapshot` |
| `task.assign` | `task_id, worker\|human` | set assignee |
| `task.claim` | `task_id?` | manual atomic claim (§8.2) |
| `task.approve` | `task_id` | approve a `proposed` task → `ready` (§8.5, §9); records approver |
| `task.reject` | `task_id, reason?` | reject a `proposed` task (drop from the inbox) |
| `task.edit` | `task_id, fields` | edit a `proposed` task before approving (title/desc/priority/deps) |
| `task.inbox` | `scope?` | reply `task.inbox` — list the approval inbox (`proposed` tasks) |
| `autonomy.set` | `scope, level, gates?` | on/off + spectrum + gates (§8) |
| `tms.sync` | `project_id, adapter?` | run TMS bridge (§10) |
| `resource.add` | `task_id, url\|path, kind` | queue ingestion (§11) |
| `team.create` | `name, template?, members?` | create a team (§22) |
| `team.update` | `id, fields` | edit members/roles/lead |
| `team.assign` | `team_id, project_id\|task_id` | assign a team to work (§22) |
| `team.message` | `team_id, from, to?, text` | post to the peer bus (§22); `to` omitted = broadcast |
| `mgmt.query` | `scope?` | reply `mgmt.snapshot` — aggregate across all (§23) |
| `mgmt.control` | `target, action, args?` | fleet-wide control: start/stop/pause/steer (§23) |
| `session.promote` | `session_id, goal, gates?` | interactive → autonomous (§25) |
| `session.takeover` | `session_id` | autonomous → interactive (§25) |
| `bookmark.add` | `url\|path, kind, note?, tags?` | save a bookmark (§26) |
| `bookmark.list` | `filter?` | reply `bookmark.snapshot` |
| `bookmark.to_task` | `bookmark_id, task_id` | attach bookmark as a task resource (§26 → §11) |
| `library.browse` | `scope?, filter?` | list library items by scope global/workspace/project/task (§27) |
| `library.search` | `query, scope?, k?` | full-text + RAG search → cited results (§27, reuse §12) |
| `library.get` | `item_id` | fetch the clean **reading-view** for an item (§28) |
| `tts.request` | `item_id, voice?` | request/stream **TTS audio** for a reading-view (§28) |
| `listen.queue` | `op, item_id?, to?` | listen-queue ops: `add\|remove\|reorder\|next\|clear` (§28) |
| `path.get` | `path_id?, scope?` | fetch a curated **learning path** (introductory→expert, cited) (§28) |

Mobile clients speak the **same** message set but reach the daemon **only** through the
authenticated coordinator proxy (§24) — never a direct localhost connection. The **approval inbox**
frames (`task.proposed`/`task.inbox` + `task.approve`/`task.reject`/`task.edit`) are served through
the **management API / coordinator** (§23–24) so the one-tap approve/edit/reject is **phone-reachable**;
like other control messages they **queue offline** and dispatch when the daemon returns (§24, §21;
decision-log 35).

### 18.2 Server → Client
| type | fields | meaning |
|---|---|---|
| `hello` | `version, workers:[{name,available,tos,auth}]` | on connect |
| `snapshot` | `sessions:[info], backlogs:{sid:text}` | on connect (no work lost) |
| `session.created` / `output` / `session.status` | … | lifecycle + streamed text |
| `question` | `session_id, question:{id,q,kind,choices?}` | unified prompt (incl. approvals) |
| `brain.message` | `text` | Brain talking to you |
| `work.snapshot` / `task.updated` | `…` | work board state / a task changed |
| `task.claimed` | `task_id, worker` | queue dispatch |
| `task.proposed` | `task:{id,title,detector,origin,project_id,refs}` | a new auto-detected proposal appeared in the inbox (§8.5) |
| `task.inbox` | `tasks:[{id,title,detector,origin,project_id,refs}]` | approval-inbox listing (`proposed` tasks) |
| `autonomy.state` | `scope, level` | autopilot on/off changed |
| `quota.update` | `worker, window_5h, weekly, utilization` | live utilization gauge (§16) |
| `approval.request` | `session_id, request:{id,detail}` | native approval callback surfaced |
| `narration` | `task_id, text` | coordination/learning feed (§coord) |
| `team.updated` | `team:{id,name,lead,members}` | a team changed (§22) |
| `team.message` | `team_id, from, to?, text, ts` | peer-bus message fan-out (§22) |
| `mgmt.snapshot` | `workspaces, projects, teams, sessions, quota, tms` | mission-control aggregate (§23) |
| `session.drive` | `session_id, drive_mode` | handoff state changed: interactive⇄autonomous (§25) |
| `bookmark.snapshot` / `bookmark.added` | `…` | bookmarks list / one added (§26) |
| `library.snapshot` | `items:[{id,source,scope,kind,title,project_id,provenance}]` | browse result (§27) |
| `library.results` | `query, hits:[{item_id,span,text,citation}]` | search hits with **citations** (§27) |
| `library.item` | `item_id, title, reading_view, audio?` | reading-view (+ cached audio ref if present) (§28) |
| `tts.audio` | `item_id, voice, audio_path\|chunk, hash, done` | streamed/complete audio artifact (§28) |
| `listen.state` | `queue:[{item_id,title,pos}], now_playing?` | listen-queue state (§28) |
| `path.snapshot` | `path_id, title, items:[{item_id,order,level}]` | learning path (introductory→expert) (§28) |
| `push` | `kind, title, body, ref?` | notification (also delivered as a phone push via §24) |
| `error` | `message, detail?` | failure |

Backpressure: per-client send queue; drop a lagging client past a cap (it reconnects + re-snapshots).
When the laptop daemon is offline, the coordinator (§24) answers read/monitor queries
(`snapshot`, `mgmt.snapshot`, `bookmark.snapshot`, **`library.*` / `path.*` / reading-views +
cached TTS audio** §27–28, docs/links/TMS, last-known state) from cache and emits `push`
notifications; control messages queue until the daemon returns. The coordinator caches **both text
(reading-views) and audio (TTS artifacts)** so the phone can read *or* listen fully offline (§28).

---

## 19. Full State Store SQLite schema (`state.py`) — §19

**M0:** append-only file transcripts (`WORKDIR/transcripts/<sid>.log`). **M2+ SQLite**, all tables:
```sql
-- core runtime
CREATE TABLE runs(     id TEXT PRIMARY KEY, spec JSON, mode TEXT, status TEXT,
                       created_at INT, ended_at INT);
CREATE TABLE sessions( id TEXT PRIMARY KEY, run_id TEXT, task_id TEXT, worker TEXT, kind TEXT,
                       cwd TEXT, status TEXT, resume_key TEXT,     -- for resume/fork (§21)
                       created_at INT, ended_at INT);
CREATE TABLE events(   id INTEGER PRIMARY KEY, session_id TEXT, ts INT, type TEXT, data JSON);
CREATE TABLE handoffs( id TEXT PRIMARY KEY, run_id TEXT, from_stage TEXT, to_stage TEXT,
                       md TEXT, json JSON, approved INT, edited_diff TEXT);
CREATE TABLE metrics(  id INTEGER PRIMARY KEY, run_id TEXT, session_id TEXT, task_id TEXT,
                       actor TEXT, kind TEXT, value REAL, ts INT);
-- intelligence
CREATE TABLE graph_nodes(id TEXT PRIMARY KEY, repo TEXT, kind TEXT, name TEXT, path TEXT, span JSON);
CREATE TABLE graph_edges(src TEXT, dst TEXT, kind TEXT);      -- imports|calls|defines|refs
CREATE TABLE chunks(   id TEXT PRIMARY KEY, repo TEXT, path TEXT, span JSON, text TEXT,
                       node_id TEXT, provenance JSON);         -- ingestion citations (§11)
CREATE TABLE memories( id TEXT PRIMARY KEY, kind TEXT, scope TEXT, text TEXT, refs JSON, ts INT);
CREATE TABLE guidelines(id TEXT PRIMARY KEY, scope TEXT, text TEXT, approved INT, source TEXT, ts INT);
CREATE TABLE verifications(id TEXT PRIMARY KEY, task_id TEXT, session_id TEXT, score REAL,
                       ok INT, findings JSON, ts INT);
CREATE TABLE embed_cache( key TEXT PRIMARY KEY, vec BLOB, model TEXT, ts INT);
CREATE TABLE rerank_cache(key TEXT PRIMARY KEY, scores JSON, model TEXT, ts INT);
-- work management (§9)
CREATE TABLE workspaces(id TEXT PRIMARY KEY, name TEXT, created_at INT);
CREATE TABLE projects( id TEXT PRIMARY KEY, workspace_id TEXT, name TEXT, autonomy TEXT, created_at INT);
CREATE TABLE tasks(    id TEXT PRIMARY KEY, project_id TEXT, parent_id TEXT, title TEXT,
                       description TEXT, status TEXT,     -- proposed|backlog|ready|in_progress|blocked|review|done (§9)
                       priority INT, labels JSON, assignee TEXT,
                       origin TEXT DEFAULT 'human',       -- "auto"|"human": auto-detected vs human-authored (§8.5, decision-log 35)
                       detector TEXT,                     -- "verifier"|"gap"|"failure"|"followup"|"ingest"; NULL if human-authored
                       approved_by TEXT, approved_at INT, -- who cleared it from the approval inbox (proposed→ready)
                       deps JSON, resources JSON, workflow TEXT, autonomy TEXT, worktree TEXT,
                       created_at INT, updated_at INT);
CREATE TABLE task_events(id INTEGER PRIMARY KEY, task_id TEXT, ts INT, type TEXT, actor TEXT, data JSON);
CREATE TABLE tms_links(native_id TEXT, adapter TEXT, external_id TEXT, source_of_truth TEXT,
                       synced_at INT);
CREATE TABLE resources(id TEXT PRIMARY KEY, task_id TEXT, url TEXT, path TEXT, kind TEXT,
                       status TEXT, ingested_at INT);           -- fetch/parse/index state (§11)
-- quota (§8.3)
CREATE TABLE quota_ledger(id INTEGER PRIMARY KEY, worker TEXT, window TEXT,   -- "5h"|"weekly"|"msg"
                       spent REAL, budget REAL, window_start INT, window_reset INT, ts INT);
-- agent teams (§22)
CREATE TABLE teams(    id TEXT PRIMARY KEY, name TEXT, template TEXT,      -- SDLC template, e.g. "feature-squad"
                       lead_member_id TEXT, scope TEXT,                   -- workspace/project this team serves
                       created_at INT);
CREATE TABLE team_members(id TEXT PRIMARY KEY, team_id TEXT, role TEXT,    -- planner|implementer|reviewer|verifier|tester|lead…
                       agent_kind TEXT,        -- "external"|"internal"|"workflow_peer"
                       worker TEXT, session_id TEXT,                       -- bound worker/live session when active
                       config JSON);
CREATE TABLE peer_messages(id INTEGER PRIMARY KEY, team_id TEXT, from_member TEXT,
                       to_member TEXT,          -- NULL = broadcast to the team
                       kind TEXT,               -- ask|inform|handoff|critique|vote
                       text TEXT, refs JSON, ts INT);   -- agent-to-agent peer channel (§22)
-- bookmarks (§26)
CREATE TABLE bookmarks(id TEXT PRIMARY KEY, url TEXT, path TEXT, kind TEXT, -- doc|link|resource
                       title TEXT, note TEXT, tags JSON, task_id TEXT,      -- task_id set when promoted to a resource (§11)
                       created_at INT);
-- management / remote (§23–24, provisional)
CREATE TABLE mgmt_cache( id TEXT PRIMARY KEY, scope TEXT, snapshot JSON, ts INT);  -- last-known state served offline (§24)
CREATE TABLE remote_tokens(id TEXT PRIMARY KEY, label TEXT, token_hash TEXT,       -- phone auth to the coordinator (§24)
                       scopes TEXT, created_at INT, revoked_at INT);
CREATE TABLE push_subscriptions(id TEXT PRIMARY KEY, device TEXT, endpoint TEXT, keys JSON, ts INT);
-- library & knowledge (§27, M9)
CREATE TABLE library_items(id TEXT PRIMARY KEY, source TEXT,   -- "project_doc"|"autogen"|"plan"|"handoff"|"artifact"|"system_doc"|"curated"
                       scope TEXT,               -- "global"|"workspace"|"project"|"task"
                       kind TEXT,                -- "doc"|"link"|"book"|"explainer"
                       title TEXT, path TEXT, url TEXT,          -- local file/PDF path OR link (user's own books)
                       project_id TEXT, provenance JSON,         -- origin ref (repo/task/bookmark), citations (§11)
                       created_at INT, updated_at INT);
-- library_index: reuse chunks/FTS (§19 chunks + FTS5) keyed by library_items.id via provenance;
-- a thin mapping table when a dedicated index is warranted (provisional):
CREATE TABLE library_index(item_id TEXT, chunk_id TEXT, PRIMARY KEY(item_id, chunk_id));  -- → chunks (§11–12)
-- read-or-listen (§28, M9)
CREATE TABLE tts_cache(   id TEXT PRIMARY KEY, item_id TEXT, voice TEXT, provider TEXT,
                       audio_path TEXT, hash TEXT,               -- content hash → invalidate on reading-view change
                       bytes INT, created_at INT);               -- offline audio cache (LRU by §20 cap)
CREATE TABLE learning_paths(id TEXT PRIMARY KEY, title TEXT, scope TEXT, cited INT, created_at INT);
CREATE TABLE learning_path_items(path_id TEXT, item_id TEXT, ord INT,   -- introductory→expert ordering
                       level TEXT, PRIMARY KEY(path_id, ord));    -- "intro"|"intermediate"|"expert"
-- vectors via sqlite-vec virtual table or sidecar index (provisional)
```

---

## 20. Config (`config.py`) — extended

Existing (M0): `ORCH_HOST/PORT`, `ORCH_WORKDIR`, `ORCH_DEFAULT_WORKER`, `ORCH_PTY_ROWS/COLS`,
`ORCH_INITIAL_DELAY`, `OPENROUTER_API_KEY/MODEL/URL`.

Added (later milestones, env-driven; defaults keep single-machine + Claude-only working today):
```python
# durability / topology (decision-log 23)
ORCH_ROLE           = "all"        # "all" | "coordinator" | "worker"
ORCH_AUTORESUME     = True         # auto-resume autopilot on restart unless disabled
ORCH_STATE_DB       = "~/.orch/state.db"
# autonomy + quota (decision-log 20, 21)
ORCH_AUTONOMY       = "interactive" # default level: interactive|assisted|autonomous
ORCH_WORKERS_HEADLESS = "claude"    # comma list opted into unattended headless (posture-gated)
ORCH_QUOTA_REGIME   = "auto"        # auto|one_pool|two_pool
ORCH_WEEKLY_PACING  = True          # pace weekly cap to drain just before reset
ORCH_MSG_BUDGET_<WORKER> = int      # per-worker message/rate budget (Codex/Gemini opt-in)
# intelligence (provisional slots — Eval Harness fills)
ORCH_GRAPH_PROVIDER = "treesitter"  # candidate; NOT a decision
ORCH_EMBED_MODEL / ORCH_RERANK_MODEL / ORCH_REASONING_POLICY
# teams (decision-log 24)
ORCH_TEAM_TEMPLATES = "~/.orch/teams/"   # SDLC role templates (planner/impl/reviewer/verifier/tester + lead)
# mobile / remote path (decision-log 25–26; provisional)
ORCH_COORDINATOR_URL   = ""          # authenticated public endpoint the phone binds to (§24); empty = no remote
ORCH_COORDINATOR_TUNNEL= "none"      # "none"|"tunnel"|"oracle"  — how the coordinator is reached  *(provisional)*
ORCH_REMOTE_TOKEN      = ""          # phone auth token (hashed at rest in remote_tokens); NEVER exposes the daemon
ORCH_PUSH_PROVIDER     = "none"      # push-notification backend  *(provisional)*
ORCH_MGMT_CACHE_TTL    = 900         # seconds the coordinator serves last-known state offline (§24)
# library & read-or-listen (decision-log 30–31, M9; provisional — harness may swap TTS/index)
ORCH_LIBRARY_ROOTS     = ""          # extra doc roots to index (managed-project docs auto-discovered from worktrees)
ORCH_TTS_PROVIDER      = "piper"     # candidate local/free engine (§28); NOT a decision — Eval Harness picks
ORCH_TTS_VOICE         = "default"   # default narration voice
ORCH_MEDIA_CACHE_DIR   = "~/.orch/media/"   # reading-view text + TTS audio cache (offline read-or-listen)
ORCH_MEDIA_CACHE_MAX_MB= 2048        # cache cap; LRU eviction of tts_cache/reading-views (§19, §28)
```
Coding workers use **subscription/login auth only** — no `ANTHROPIC_/OPENAI_/GEMINI_/GOOGLE_` API
keys (RESEARCH §2); config never introduces them for coding workers. The **only** API key ever
read is `OPENROUTER_API_KEY` for the fuel worker (§2.3). Remote access is gated by
`ORCH_REMOTE_TOKEN` at the coordinator; the localhost daemon is **never** bound to a public
interface (decision-log 26, §24).

---

## 21. Durability / auto-resume mechanism (decision-log 23)

- **Boot/login service.** `orchd` runs as a persistent service — e.g. a **systemd user unit**
  *(provisional)* — so it survives logout and starts at boot/login.
- **Persistent state.** All runtime + work state in SQLite (§19); the daemon is stateless between
  restarts beyond the DB + transcripts.
- **Auto-resume on restart** (unless `ORCH_AUTORESUME=False`):
  1. Load `runs`/`sessions`/`tasks`; find `in_progress` tasks and non-terminal sessions.
  2. **Resume/fork** sessions whose driver supports it (SDK `resume`/`fork`, Gemini
     `--checkpointing`) via `sessions.resume_key`; otherwise **re-dispatch** the task fresh into a
     new worktree.
  3. Re-arm the QuotaScheduler from `quota_ledger` (reservoir positions, window resets).
  4. Watchdog (§8.4) resumes the autopilot loop.
- **Split topology (provisional).** Coordinator role (queue/state/scheduler/watchdog/notifier)
  can run always-on on a free Oracle Cloud micro instance; the local worker host holds the
  subscription + compute. When local is off, the coordinator persists intent and resumes dispatch
  when it returns. Topology decided later by comparison; not locked.

---

## 22. Agent Teams (`teams/`) — interface-level, M8

Named groups of collaborating agents with roles + a lead, **templated per SDLC**, composed by the
Orchestration Engine as a **layer over** Router/Parallel/Pipeline (decision-log 24). A team mixes
**external** coding agents (§3), **internal** capability agents (§agents), and **workflow peers**;
it shares the team's context/memory scope and leans heavily on peer fan-out / cross-check /
synthesize. **Reuse before invent (decision-log 29):** teams *compose the existing* run strategies
(engine/), Session kinds (§2), and MoE router (§14) — no new execution primitive.
```python
@dataclass
class TeamMember:
    id: str
    role: str            # "planner"|"implementer"|"reviewer"|"verifier"|"tester"|"lead"|…
    agent_kind: str      # "external" | "internal" | "workflow_peer"
    worker: str | None   # bound worker (external) — e.g. "claude"; None for pure workflow peers
    session_id: str | None   # live session when active

@dataclass
class Team:
    id: str; name: str
    template: str | None  # SDLC template that seeded the roles (e.g. "feature-squad")
    lead: str             # TeamMember.id of the coordinator/lead
    members: list[TeamMember]
    scope: str            # workspace/project this team serves

class TeamCoordinator:               # the lead's control surface (interface-level)
    def assign(work) -> None                     # place a project/task onto the team
    def plan(work) -> list["Assignment"]         # lead decomposes → per-role assignments
    def run(work) -> "RunHandle"                 # compose over engine/ (Router/Parallel/Pipeline)
    def on_peer(msg: "PeerMessage") -> None      # react to the peer bus (below)

class TeamTemplates:                 # SDLC role bundles  *(provisional; Eval Harness may tune)*
    def get(name) -> Team            # e.g. planner+2×implementer+reviewer+verifier+tester+lead
    def list() -> list[str]
```

**Agent-to-agent peer channel (`teams/peers.py`).** A per-team **peer-message bus** lets members
talk directly (ask / inform / handoff / critique / vote) without routing every exchange through the
Brain — the substrate for fan-out and synthesize.
```python
@dataclass
class PeerMessage:
    team_id: str; frm: str; to: str | None       # to=None → broadcast to the team
    kind: str        # "ask"|"inform"|"handoff"|"critique"|"vote"
    text: str; refs: list[str]; ts: int

class PeerBus:
    async def post(msg: PeerMessage)              # persist → peer_messages (§19) → fan-out on sink
    def subscribe(team_id, member_id) -> "AsyncIterator[PeerMessage]"
```
Teams/members/peer traffic persist to `teams` / `team_members` / `peer_messages` (§19); clients
drive them over `team.*` wire messages (§18) and see them in mission-control (§23).

---

## 23. Overall Management Layer (`management/`) — interface-level, M8

A top-level **mission-control** aggregation + control plane spanning **everything** — all
workspaces/projects/teams/sessions/quota/TMS — the single surface both **desktop and phone** bind to
(decision-log 25). Read *and* control.
```python
class ManagementAPI:
    # aggregate (read)
    def snapshot(scope=None) -> "MgmtSnapshot"   # {workspaces,projects,teams,sessions,quota,tms}
    def quota_rollup() -> dict                    # per-worker 5h/weekly/utilization (§8.3, §16)
    def activity() -> list                        # merged narration/events across the fleet
    # control (write) — fans out to the owning subsystem
    async def control(target, action, args=None) # start|stop|pause|steer|autonomy.set|team.assign|
                                                  #   session.promote|session.takeover|tms.sync
```
It composes the existing stores/controllers (WorkStore §9, SessionManager §4, QuotaScheduler §8.3,
TMS §10, teams §22) — an aggregation surface, **not** a new source of truth (decision-log 29).
Exposed over `mgmt.query` / `mgmt.control` + `mgmt.snapshot` (§18). This is the surface the mobile
path (§24) proxies to.

---

## 24. Mobile / Remote Access — coordinator proxy *(provisional)*, M8

The phone reaches the fleet **only** through the always-on **coordinator** (the split-topology
coordinator role of §21, e.g. the free Oracle Cloud micro instance) — the **authenticated public
endpoint**. The localhost daemon is **never** exposed directly (decision-log 26). *(Provisional —
topology/transport decided later by comparison.)*

```python
class CoordinatorProxy:              # runs in ORCH_ROLE="coordinator" (§20)
    async def authenticate(token) -> "Principal"     # ORCH_REMOTE_TOKEN → remote_tokens (§19)
    async def proxy(msg) -> dict     # laptop ONLINE  → forward to local daemon (management §23)
    async def serve_cached(msg) -> dict  # laptop OFFLINE → mgmt_cache/bookmarks/docs/TMS/last-known
    async def push(sub, event)       # push notifications → push_subscriptions (§19)
```

- **Online (laptop up).** The coordinator authenticates the phone (token / tunnel) and **proxies**
  its `mgmt.*` / control / `session.promote|takeover` / `team.*` messages to the local daemon —
  **full orchestration control** from the phone.
- **Offline (laptop down).** The coordinator serves **read/monitor** from cache: docs, links,
  **bookmarks** (§26), TMS mirror, and **last-known state** (`mgmt_cache`, `ORCH_MGMT_CACHE_TTL`),
  plus **push notifications**. Control messages **queue** and dispatch when the daemon returns
  (ties into auto-resume, §21).
- **Auth & ToS.** Phone auth is a hashed `ORCH_REMOTE_TOKEN` at the coordinator; no subscription
  credential or coding-worker API key ever leaves the worker host. Coordinator holds **no**
  coding-worker secrets — only queue/state/notify (decision-log 23, 26; RESEARCH §2–4).

Wire surface: the phone speaks the same protocol (§18) but always through the proxy; new frames
`push`, and the cache-served variants of `snapshot`/`mgmt.snapshot`/`bookmark.snapshot`.

---

## 25. Live Session Handoff & Takeover (`sessions/` + `autonomy/`) — M8

Bidirectional, mid-flight driver swap for a running session (decision-log 27) — the live-session
twin of the modes spectrum (§8). Implemented as a **state machine on `Session.drive_mode`**
(`interactive ⇄ autonomous`, §2.1), swapping the *driver* and the *permission/gate policy* **without
restarting** the session (buffer, cwd, worktree, resume_key all preserved).

```python
# interactive → autonomous
async def promote_to_autonomous(session, goal, gates):
    policy = GatePolicy(gates)                    # declarative gates (§8.1)
    autonomy.attach(session, goal, policy)        # Autonomy Controller becomes the driver
    session.drive_mode = "autonomous"             # human/Brain writes now go through gates
    emit(session.drive("autonomous"))             # → wire (§18)

# autonomous → interactive
async def takeover_to_interactive(session):
    autonomy.detach(session)                      # controller releases the driver; loop paused
    session.drive_mode = "interactive"            # human/Brain regain the live PTY/write() path
    emit(session.drive("interactive"))
```

- **Driver swap.** Autonomous ⇒ the AutonomyController (§8) owns dispatch/verify/replenish for this
  session; interactive ⇒ human + Brain own `write()` (§2). Both share the same PTY/structured
  channel — takeover is *seizing the wheel*, not respawning.
- **Permission/gate policy change.** Promote installs the goal's declarative gates (approve-on-risk,
  auto-merge thresholds, escalation triggers §8.1); takeover restores interactive prompting
  (Interaction Layer §7).
- **Transitions** are logged to `task_events`/`events` (§19) and surfaced as `session.drive` (§18);
  triggered from desktop **or** phone (§23–24). On daemon restart, `drive_mode` rehydrates with the
  session (§21).

---

## 26. Bookmarks (`bookmarks.py`) — M8

The operator saves docs/links/resources; bookmarks **sync to the phone** and can **become task
resources**, feeding Resource Ingestion (decision-log 28 → §11).
```python
class BookmarkStore:
    def add(url_or_path, kind, note=None, tags=None) -> str   # kind: "doc"|"link"|"resource"
    def list(filter=None) -> list["Bookmark"]
    def to_resource(bookmark_id, task_id)     # attach as a task resource → IngestionPipeline (§11)
```
Persisted in `bookmarks` (§19); driven over `bookmark.*` wire messages (§18); read on the phone
online **and** offline (served from cache by the coordinator, §24). A bookmark promoted via
`to_resource` enters the same fetch→parse→chunk→index path as any task resource (§11), keeping
provenance/citations intact.

---

## 27. Library & Knowledge layer (`library/`) — interface-level, M9

The "reading room": aggregate and make searchable **all managed-project docs** (repo docs,
auto-generated docs, plans/handoffs/artifacts), the **system's own docs**, and curated **reading
materials** — links, theoretical **books/PDFs**, introductory guides, and expert explainers
(decision-log 30; DESIGN §8, §3). Max viewability (esp. phone, §24) + full-text/RAG search with
**citations**. **Reuse before invent (decision-log 29):** the index rides on the existing Context
Engine (§12) + `chunks`/FTS (§11, §19) rather than a parallel retrieval stack.

```python
class DocSource(ABC):            # a pluggable origin of library items  *(provisional candidates below)*
    name: str                    # "project_doc"|"autogen"|"plan"|"handoff"|"artifact"|"system_doc"|"curated"
    def discover(scope) -> list["LibraryItem"]   # enumerate items in a scope (repo walk, curated list…)
    async def load(item) -> "RawDoc"             # fetch/read raw content (local file/PDF/book, or link)

@dataclass
class LibraryItem:
    id: str
    source: str          # DocSource.name
    scope: str           # "global"|"workspace"|"project"|"task"
    kind: str            # "doc"|"link"|"book"|"explainer"
    title: str
    path: str | None     # local file / PDF / user's own book
    url: str | None      # link (curated reading material)
    project_id: str | None
    provenance: dict     # origin ref (repo/task/bookmark) + citation seed (§11)

class LibraryIndex:              # full-text + RAG over library items, returning citations
    def ingest(item: LibraryItem)                # → chunks (§11 pipeline) + library_index map (§19)
    def browse(scope, filter=None) -> list[LibraryItem]
    def search(query, scope=None, k=10) -> list["Citation"]  # FTS5 ⊕ RAG via Context Engine (§12)

@dataclass
class Citation:                  # a cited search hit — provenance-preserving (§11)
    item_id: str; span: dict; text: str; source: str; url_or_path: str
```

- **Candidate `DocSource`s** *(provisional)*: a **repo-doc** source (walks each managed project's
  worktree for `*.md`/docs/), an **autogen** source (generated API/reference docs), a **plan/handoff/
  artifact** source (reads `handoffs` §19 + run artifacts), a **system-doc** source (this repo's
  `DESIGN/HLD/LLD/…`), and a **curated** source (links, **books/PDFs**, intro/expert explainers the
  operator adds). All ToS-clean: **the user's own books/materials**, links they attach — no scraping
  (decision-log 2; RESEARCH §2).
- **Scope levels.** `global` (system + curated), `workspace`, `project`, `task` — search and browse
  filter by scope; a task-scoped view surfaces exactly that task's reading materials.
- **Overlap (by design).** Library items ↔ **task resources** (§11 Resource Ingestion) ↔
  **bookmarks** (§26): a library item can be promoted to a task resource (same fetch→parse→chunk→
  index path, provenance intact), and a bookmark can enter the library. The library **reuses** the
  ingestion pipeline and `chunks` table rather than duplicating them (decision-log 28–30).

## 28. Read-or-Listen — media render pipeline (`media/`) — interface-level, M9

Every library item renders to a clean **reading view** and an optional **audio narration (TTS)** so
the operator can read *or* listen on the go; text + audio are **cached on the coordinator for
offline** (decision-log 31; DESIGN §8). A curated **learning path** (introductory→expert, unbiased,
cited) helps expand understanding — the teach-and-expand rule as a product feature.

```python
class ReadingView:               # item → clean, readable text (readability/markdown/PDF extract)
    def render(item: "LibraryItem") -> str        # reuse ingestion parsers (§11); PDF/book aware  *(provisional)*

class TTSProvider(ABC):          # pluggable narration engine  *(candidates below; harness-decided)*
    name: str
    async def synthesize(text: str, voice: str) -> "AudioArtifact"   # → audio bytes/file

@dataclass
class AudioArtifact:
    item_id: str; voice: str; provider: str; audio_path: str; hash: str

class MediaPipeline:             # item → reading-view → (optional) TTS → cached artifact
    async def reading_view(item) -> str                       # cached text (offline)
    async def narrate(item, voice=None) -> AudioArtifact      # ReadingView → TTSProvider → tts_cache (§19)
    def cache_get(item_id, voice) -> AudioArtifact | None     # hash-checked; re-synthesize on change

class ListenQueue:               # ordered "play next" queue for narrated items
    def add(item_id); def remove(item_id); def reorder(item_id, to); def next() -> str | None

class LearningPath:              # curated introductory→expert ordering, cited
    def build(scope, goal) -> "Path"        # order items by level; each step carries citations (§27)
    def get(path_id) -> "Path"
```

- **TTSProvider candidates** *(provisional, Eval Harness picks — decision-log 19)*: a **local/free
  engine first** (e.g. **Piper**), others compared head-to-head. **Free/local-first, no overflow /
  API credits** (decision-log 31; RESEARCH §2–3) — narration must never spend paid quota. Voice/
  provider from config (§20).
- **Offline cache.** Reading-view text and TTS audio both land in `ORCH_MEDIA_CACHE_DIR` (§20) and
  `tts_cache` (§19), keyed by a content **hash** so a changed reading-view invalidates stale audio;
  LRU-evicted at `ORCH_MEDIA_CACHE_MAX_MB`. The **coordinator caches both** (§24) so the phone reads
  *or* listens with the laptop off.
- **Learning path.** Built over library items (§27) with an introductory→expert ordering and
  per-step **citations**; served via `path.*` (§18). Unbiased + cited is the teach-and-expand rule
  (DESIGN §8, decision-log 31).
- **Wire + serving.** Reading-views, TTS audio, listen-queue, and learning-paths are served through
  the **coordinator** (§24): live from the daemon when the laptop is **online**, from the media/text
  cache when **offline**. Remote access stays coordinator-only — the daemon is never exposed
  (decision-log 26).

## 29. Error handling & edge cases; test plan

### Edge cases
- **CLI missing / not logged in:** `available()` false → Brain surfaces install/login hint; no crash.
- **Subscription vs API key:** if `ANTHROPIC_API_KEY` is set, warn (it overrides subscription) —
  the coding path must stay on subscription auth (RESEARCH §2).
- **Headless block-buffering:** never pipe a coding CLI's stdout raw; use SDK or PTY (RESEARCH §1.1).
- **Codex `-a never` approval failure:** route approvals through `codex_rpc`, not `exec`.
- **Quota approaching cap:** scheduler throttles/backs off; **never** spends overflow/API credits.
- **Two workers, one task:** atomic claim (§8.2) guarantees single ownership.
- **Power loss mid-task:** auto-resume (§21) re-dispatches or resumes/forks.
- **TMS conflict:** log to `task_events` + raise a human task; source-of-truth rule wins (§10).
- **PTY write after exit / client disconnect / runaway output:** swallow `OSError`; skip dead
  sockets; per-session buffer cap with rotation to transcript.
- **Handoff mid-write:** promote/takeover (§25) drains in-flight input, swaps driver + gate policy
  atomically; no double-driver — exactly one of {human/Brain, AutonomyController} owns `write()`.
- **Laptop offline, phone control attempt:** coordinator (§24) serves cached read/monitor and
  **queues** control messages; never exposes the daemon; dispatch resumes on reconnect (§21).
- **Bad/revoked remote token:** coordinator rejects at `authenticate`; no subscription secret or
  coding-worker API key is ever reachable from the remote path (decision-log 26; RESEARCH §2).
- **Peer-bus storm:** per-team `peer_messages` rate cap; broadcast fan-out bounded; lead can mute.
- **Library scraping risk:** curated materials are the **user's own books/PDFs and attached links**
  only — no bulk scraping; links respect robots/access like ingestion (§11; decision-log 2).
- **TTS quota:** narration uses a **local/free** engine only — never a paid API / overflow credit
  (§28; decision-log 31). If no local engine is available, `narrate` degrades to text-only.
- **Stale audio:** reading-view change bumps the content `hash` → `tts_cache` miss → re-synthesize;
  never serve audio that doesn't match the current text (§28).
- **Media cache pressure:** LRU-evict `tts_cache`/reading-views at `ORCH_MEDIA_CACHE_MAX_MB` (§20).

### Test plan (M0–M2 concrete; M8 interfaces stubbed/mocked)
- **Unit:** sentinel parser; `Availability`/posture logic; protocol (de)serialization; WorkStore
  CRUD + atomic claim (temp DB); QuotaScheduler pacing math (simulated windows);
  WorkspaceManager git ops (temp repo).
- **Integration:** spawn a **mock CLI** (tiny script echoing prompts + emitting a sentinel) under
  `PtySession`; assert output/question/answer round-trip over a real WS client; task
  claim→dispatch→verify→handoff loop against mock worker + stub verifier.
- **Manual:** drive real `claude` (PTY + SDK); verify live stream, take-over, clean exit,
  resume-after-restart, and a full autopilot tick within quota.

---

## 30. For approval
1. Module layout & subsystem decomposition (§1) — work/autonomy/intelligence/maintenance/teams/
   management/remote split.
2. Session-kind taxonomy (§2) — SDK / headless / PTY / app-server-RPC / ACP / API coverage, plus
   the `drive_mode` handoff state machine (§2.1, §25).
3. Autonomy Controller + **QuotaScheduler** pacing model (§8) — reservoir/token-bucket approach OK?
4. Work-management schema + TMS bridge rules (§9–10) — standalone-first, TMS-optional.
5. Wire protocol additions (§18) & full SQLite schema (§19) — complete for your UI + autopilot plans,
   incl. teams/management/mobile/handoff/bookmarks frames + tables?
6. Durability / auto-resume + split-topology mechanism (§21); coordinator as the mobile endpoint (§24).
7. Provider-pluggable stance (§12–15) — interfaces concrete, implementations left to the Eval Harness.
8. M8 interfaces (§22–26) — **Agent Teams** (roles/lead/peer bus over Router/Parallel/Pipeline),
   **Overall Management Layer**, **mobile/remote via the authenticated coordinator**, **bidirectional
   live handoff**, **bookmarks** — interface-level and provisional; shape right before build?
9. **M9 Library & Knowledge + Read-or-Listen (§27–28)** — `DocSource`/`LibraryIndex` reusing the
   ingestion pipeline + Context Engine for cited search across all managed-project docs, system docs,
   and curated books/links; **TTSProvider** (local/free-first, harness-picked) + reading-view +
   listen-queue + learning-path, text+audio **coordinator-cached** for offline — interface-level and
   provisional; scope/overlap (task resources ↔ bookmarks) and ToS stance right before build?
→ On approval I build **M0–M2** (Claude+Codex+Gemini+OpenRouter spine, Interaction Layer, SQLite,
work store), with later subsystems (incl. **M8** teams/management/mobile/handoff/bookmarks) landing
at their milestones.
