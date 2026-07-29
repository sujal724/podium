# Capability Analysis — What the Harnesses Already Ship vs. What We Build

**Status:** Analysis for approval · **Date:** 2026-07-27 · Companion to `DESIGN.md` / `HLD.md` / `RESEARCH.md`

Purpose: ground the "don't reinvent the wheel" principle (DESIGN §7, decision 13/29) in a **verified,
sourced inventory** of what Claude Code, Codex CLI, and Gemini CLI natively ship as of July 2026 —
then render a **delegate / shim / build / de-scope verdict for every component in our tentative
design**. The three per-harness inventories were compiled from official docs only (code.claude.com,
developers.openai.com/codex → learn.chatgpt.com, geminicli.com / github.com/google-gemini). Items the
research pass could not source are marked UNVERIFIED and must be re-probed by the Capability Registry
before being relied on.

> Headline: the harnesses moved **a lot** since RESEARCH.md was written. All three now have hooks,
> skills, subagents, and native review. Codex gained a full programmatic control plane (app-server)
> with machine-readable quota state. Claude Code gained agent teams, script-driven workflows, and
> background-session dispatch. **None of this touches our four unique pillars** (own-subscription
> economics, quota rationing, owned work-management + TMS bridge, learning library) — but it
> **de-scopes several shims we planned** and changes how the worker layer should be driven.

---

## 1. Cross-harness capability matrix (July 2026, sourced)

Legend: ✅ native · ◐ partial/experimental · ❌ absent. Invocation in parentheses.

| Capability | Claude Code | Codex CLI | Gemini CLI |
|---|---|---|---|
| **Headless one-shot** | ✅ `claude -p` | ✅ `codex exec` | ✅ `gemini -p` (or any non-TTY) |
| **Structured event stream** | ✅ `--output-format stream-json` (+ stream-json input) | ✅ `codex exec --json` (JSONL events) | ✅ `--output-format stream-json` |
| **Schema-validated output** | ✅ `--json-schema` | ✅ `--output-schema` (not on `exec resume` — [#22998](https://github.com/openai/codex/issues/22998)) | ❌ (JSON envelope only) |
| **Session resume** | ✅ `--resume/--continue` | ✅ `codex resume`, `codex exec resume --last/<id>` | ✅ `--resume`, `/resume` |
| **Session fork** | ✅ `--fork-session`, `/branch` | ✅ `codex fork`, `thread/fork` | ❌ |
| **Bidirectional programmatic control** | ✅ Agent SDK (Py/TS) over stream-json; `can_use_tool` | ✅ **app-server JSON-RPC** (`thread/*`, `turn/start`, **`turn/steer`**, `turn/interrupt`, approval callbacks, WS/unix transports, per-version schema codegen) + TS SDK `@openai/codex-sdk` | ✅ ACP (`gemini --acp`): `newSession/loadSession/prompt/cancel/setSessionMode`, mid-session model switch |
| **Acts as MCP server** | ◐ `claude mcp serve` (UNVERIFIED in current docs) | ✅ `codex mcp-server` (`codex`/`codex-reply` tools) | ❌ |
| **MCP client** | ✅ stdio/HTTP/SSE + OAuth + deferred tool search | ✅ stdio/streamable-HTTP + OAuth + tool filtering/approval modes | ✅ stdio/SSE/HTTP + OAuth + `includeTools`/`trust` |
| **Hooks (lifecycle)** | ✅ 15+ types (PreToolUse/PostToolUse/PreCompact/TaskCreated/TeammateIdle/Worktree\*…) | ✅ 11 events incl. SubagentStart/Stop, PermissionRequest; JSON in/out, block/inject-context | ✅ 11 events (BeforeTool/AfterModel/BeforeToolSelection…); allow/deny/rewrite input/inject context/filter toolset |
| **Native subagents** | ✅ parallel + background, per-agent model/tools/memory | ✅ **parallel, on by default** (Multi-agent V2); TOML agent defs, per-agent model/sandbox/MCP | ◐ **sequential only**; Markdown defs; + **remote A2A agents** |
| **Multi-session teams** | ◐ Agent Teams (experimental): shared task list, mailboxes, tmux panes | ◐ collab feature flag; desktop "mission control" app | ❌ |
| **Scripted multi-agent orchestration** | ✅ Workflows (JS pipeline/parallel, budgets) | ❌ | ❌ |
| **Background session dispatch** | ✅ `claude --bg`, agent view (`claude agents`), auto-worktrees | ◐ Codex Cloud (`codex cloud`, `codex apply`) | ◐ background shells; Jules extension (cloud) |
| **Skills** | ✅ `.claude/skills` + plugins + marketplace | ✅ `.agents/skills` open standard, `$skill`, plugins | ✅ `.gemini/skills` + extensions + gallery |
| **Memory files** | ✅ CLAUDE.md hierarchy + rules + imports | ✅ AGENTS.md hierarchy (32 KiB cap, overrides) | ✅ GEMINI.md hierarchy; `context.fileName` accepts `["AGENTS.md",…]` |
| **Learned/auto memory** | ✅ auto-memory (MEMORY.md per project) | ◐ "project memories" (via setup import; thin docs) | ◐ `save_memory` tool; `experimental.autoMemory` |
| **Checkpoint / rewind** | ✅ auto checkpoints, `/rewind` (code and/or convo) | ◐ thread fork/resume (no file snapshots) | ✅ shadow-git snapshots + `/restore` (settings-only; `--checkpointing` flag **removed** v0.11.0) |
| **Context compaction** | ✅ `/compact` + auto + microcompact | ✅ (PreCompact/PostCompact hook events) | ✅ `/compress` + `compressionThreshold` + PreCompress hook |
| **Permission/approval system** | ✅ modes incl. AI-classifier `auto`; allow/ask/deny rules | ✅ `untrusted/on-failure/on-request/never`; "writes" mode for MCP tools | ✅ `default/auto_edit/yolo/plan` + **TOML policy engine** (per-subagent rules) |
| **OS sandboxing** | ✅ fs+network isolation, credential masking | ✅ Seatbelt/Landlock+seccomp/Windows; standalone `codex sandbox <cmd>` | ✅ Seatbelt/Docker/Podman/gVisor/LXC |
| **Web search / fetch** | ✅ WebSearch + WebFetch tools | ✅ `--search` / `tools.web_search` | ✅ `google_web_search` + `web_fetch` |
| **Native code review** | ✅ `/code-review`, `/security-review`, ultrareview | ✅ `codex review` + `/review` + GitHub PR review (separately metered) | ◐ GitHub Action PR-review workflow |
| **Best-of-N attempts** | ❌ local (composable via Workflows) | ◐ cloud-only ("attempts") | ❌ |
| **Model routing / fallback** | ◐ `--model/--effort/--fallback-model` (manual) | ◐ per-subagent model config | ✅ **Auto routing** (complexity classifier) + quota-fallback chains |
| **Scheduling / cron** | ✅ `/loop`, CronCreate, `/schedule` routines | ❌ (notify hook only) | ❌ |
| **Quota visibility** | ✅ `/usage`, `/status`, OTEL, `--max-budget-usd` | ✅ `/status` + **app-server rate-limit methods** + in-product credit state (v0.144.0) | ◐ `/stats` (session/model/tools) |
| **OTEL telemetry** | ✅ | ✅ `[otel]` | ✅ `telemetry.*` |
| **Worktree isolation** | ✅ `--worktree`, auto for bg sessions | ❌ (cloud envs instead) | ◐ `experimental.worktrees` |
| **Subscription-headless auth** | ✅ documented (`setup-token`, OAuth creds) | ✅ **now documented** (device-auth beta, auth.json seeding "advanced", enterprise tokens; API key still the stated CI default) | ⚠️ automation docs push API key / Vertex; OAuth automation still gray (`GEMINI_CLI_TRUST_WORKSPACE` exists) |
| **Completion notification** | ✅ hooks (Stop/Notification) | ✅ `notify` program + hooks | ✅ Notification hook |
| **Custom tools without MCP** | ❌ (MCP is the seam) | ❌ (MCP is the seam) | ✅ `tools.discoveryCommand`/`callCommand` |

Primary sources: Claude — <https://code.claude.com/docs/en/cli-reference.md>, `agent-teams.md`,
`workflows.md`, `agent-view.md`, `hooks.md`, `checkpointing.md`, `sandboxing.md`,
`monitoring-usage.md`, `authentication.md`. Codex — <https://developers.openai.com/codex/app-server>,
`/codex/subagents`, `/codex/hooks`, `/codex/skills`, `/codex/noninteractive`, `/codex/auth`,
`/codex/pricing`, <https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md>,
<https://openai.com/index/unlocking-the-codex-harness/>. Gemini — <https://geminicli.com/docs/>
(`cli/headless`, `cli/acp-mode`, `core/subagents`, `core/remote-agents`, `hooks/`,
`reference/policy-engine`, `cli/checkpointing`, `resources/quota-and-pricing`),
<https://github.com/google-github-actions/run-gemini-cli>.

---

## 2. Verdicts — every tentative component, delegate vs build

Verdict key: **DELEGATE** = use the native feature, ours is a thin uniform wrapper ·
**DELEGATE+SHIM** = native where present, shim the gap, retire per evolution principle ·
**BUILD** = no harness has it; genuine value-add · **DE-SCOPE** = we planned it, natives cover it —
cut or shrink · **KEEP-REVISED** = build, but design changes given new native facts.

### 2.1 Worker & session plane

| Our component (HLD §3/§5) | Verdict | Grounding |
|---|---|---|
| Worker adapters / six session kinds | **DELEGATE** (contract stays) | All three now have first-class bidirectional drivers: Claude Agent SDK; Codex **app-server** (not `codex mcp` — update HLD §5: app-server is the blessed surface, with TS SDK and per-version schema codegen); Gemini ACP. Gemini also has stream-json headless now. |
| PTY live terminal + human takeover | **BUILD** | No harness lets you watch + seize *another vendor's* running session; Claude agent view is Claude-only. This remains our substrate. |
| Live session handoff (promote/take-over) | **BUILD** (easier now) | Codex `turn/steer` injects input mid-turn without interrupt; ACP `setSessionMode` flips approval level mid-session — use these as the native halves of the driver-swap. |
| Workspace Manager (worktrees) | **DELEGATE+SHIM** | Claude does worktrees natively (auto for bg sessions); Gemini experimental; Codex none → we manage worktrees only for Codex + cross-worker coordination. |
| Handoff Bus / co-editing / Edit-Diff Sync | **BUILD** | No native equivalent anywhere. Injection half can ride native hooks (`additionalContext` in all three) instead of prompt-splicing. |
| Interaction Layer | **DELEGATE, shrink the shim** | Native approval callbacks exist on **all three** drivers now (can_use_tool / app-server approvals + `approvalsReviewer` / ACP). The `<<ASK>>` sentinel was designed for `codex exec`'s missing answer channel — **app-server closes that gap**; keep sentinel+clarifier only as a degraded-mode fallback. |
| Sandboxing / permissions | **DELEGATE** (never build) | All three ship OS sandboxes + policy engines. Bonus: standalone `codex sandbox <cmd>` can sandbox *our own* verifier/ingestion commands. |

### 2.2 Orchestration & autonomy

| Our component | Verdict | Grounding |
|---|---|---|
| Router / Parallel / Pipeline modes | **KEEP-REVISED** | Cross-vendor composition is still ours. But **intra-worker fan-out should delegate**: Claude Workflows (scripted parallel/pipeline + budgets) and Codex parallel subagents do single-vendor fan-out natively — our engine should *invoke* those rather than spawning N top-level sessions when the fan-out is within one vendor. |
| Agent Teams layer | **KEEP-REVISED** | Claude Agent Teams (shared task list, mailboxes) is experimental and Claude-only; Codex collab is a flag; Gemini has none. Our cross-vendor teams stay, but the design should treat native teams/subagents as *one team-member implementation* (a "composite worker") the Registry can select. |
| Autonomy Controller (pull→run→verify→replenish) | **BUILD** | Nothing native runs a task queue against quota. Claude cron/`/loop` is per-session scheduling, not fleet autopilot. |
| Quota Scheduler (reservoir/rationing) | **BUILD** (confirmed unique) — ingestion now **DELEGATE** | Feed it from native surfaces instead of log-scraping: Codex **app-server rate-limit methods** + `/status` + credit/reset surfacing (v0.144.0); Claude `/usage`+OTEL; Gemini `/stats`. **New requirement: multi-regime.** Claude+Codex = 5h rolling (+weekly); Gemini = requests/day+RPM (1,000/day free OAuth, 1,500 AI Pro, 2,000 Ultra). One controller, per-worker regime models. **Codex credits are purchasable overflow — hard-block spending them** (extends decision 20's "no overflow"). |
| Watchdog (self-healing) | **DELEGATE+SHIM** | Auto-compaction is native in all three (+ PreCompact/PreCompress hooks to observe it). Keep only: resume-on-quota-reset, idle re-prompt, crash restart. |
| Verifier / Critic | **KEEP-REVISED** | First-pass review delegates to native: `codex review` (headless!), Claude `/code-review`/`/security-review`. Our layer keeps what natives lack: *independent cross-vendor* verification (Codex reviewing Claude's diff), verify→repair loop, best-of-N gating, RLAIF signal. Matches RESEARCH §7's "verifier is table stakes" correction. |
| Best-of-N + checkpoint merge | **BUILD** (local) | Codex attempts are cloud-only; nothing local/cross-vendor. |
| Capability Registry + Updater/Watcher | **BUILD — validated hard** | The deltas in this very doc (Codex subagents/hooks/skills in ~6 months; Gemini flag removals; docs-host migrations) are the proof. Codex `generate-json-schema` gives per-version machine-readable probing for free. |
| MoE-over-agents routing | **BUILD** (differentiator confirmed) | Only intra-vendor *model* routing exists (Gemini Auto, Cursor/Factory per RESEARCH §7). Cross-vendor task→agent routing by capability/quota/track-record remains empty ground. |
| Scheduling / cron | **DELEGATE+SHIM** | Claude has native cron/routines; Codex/Gemini don't → our controller owns fleet scheduling, may delegate Claude-only recurring jobs. |

### 2.3 Intelligence layer

| Our component | Verdict | Grounding |
|---|---|---|
| Context Engine (graph + deep RAG + rerank) | **BUILD** (core value, unchanged) | No harness ships a repo graph or RAG index. MCP-injection seam confirmed: all three are MCP clients; Gemini even offers non-MCP custom tools (`discoveryCommand`) as an alternative injection path. |
| Memory Store (cross-run, cross-fleet) | **KEEP-REVISED** | Each CLI now has *private* memory (auto-memory / save_memory / project memories). Ours is the **shared cross-vendor** store; distribute via the common denominator **AGENTS.md** (Codex native; Gemini via `context.fileName: ["AGENTS.md",…]`; Claude via CLAUDE.md import `@AGENTS.md`). Decide policy: native per-CLI memories stay on (harness-local learning) but our store is authoritative for shared facts. |
| Guidelines Engine → AGENTS.md | **BUILD** (distribution delegates) | As designed — no native "self-proposing additive rules" exists anywhere. |
| Reasoning Provider | **BUILD** | Unchanged; CLI-as-LLM paths all still exist (`claude -p`, `codex exec`, `gemini -p`). |
| Fresh-context resets | **DELEGATE** | Native session lifecycle + fork covers it (Claude fork/`--no-session-persistence`; Codex `--ephemeral`, thread fork; Gemini sessions). |
| Metrics / observability | **DELEGATE ingestion, BUILD analysis** | All three export **OTEL** — consume their exporters as the primary feed (cost attribution per model/skill/agent on Claude) instead of only parsing transcripts; our rework-rate/efficiency analytics stay ours. |
| Small ML/RL (bandits, predictors, RLAIF) | **BUILD** | No overlap with any harness. |

### 2.4 Work management & coordination

| Our component | Verdict | Grounding |
|---|---|---|
| Native work management (workspaces/projects/tasks), approval inbox | **BUILD** (differentiator confirmed) | No harness owns a PM layer. (Claude teams' task list is a coordination scratchpad, not work management.) |
| TMS Bridge | **BUILD** | Unchanged; target the three third-party-agent protocols in RESEARCH §7.3. Codex Cloud's Linear/Slack triggers are vendor-cloud-side, not a local bridge. |
| Resource Ingestion | **BUILD**, delegate fetching *inside sessions* | Agents' own browsing delegates to native web tools; ingestion-for-indexing (into RAG, offline library) stays ours. |
| Library & Knowledge / TTS / mobile / learning companion / learner ML | **BUILD** | Zero overlap with any harness (RESEARCH §7.4 stands). |
| Coordination & Learning, user-state model, provenance | **BUILD** | No overlap. Provenance gets native help: all three hooks report tool/session identity; Codex JSONL + Claude stream-json tag message origins. |

---

## 3. Required corrections to RESEARCH.md / HLD.md (facts that moved)

1. **Codex programmatic surface** (RESEARCH §1.2, HLD §5): the blessed driver is **`codex app-server`**
   (JSON-RPC 2.0, stdio/WS/unix, `turn/steer`, approvals, hosted-auth injection, schema codegen), per
   OpenAI's "Unlocking the Codex harness". `codex mcp-server` exists separately (Codex *as* MCP
   tool). An **official TS SDK** (`@openai/codex-sdk`) wraps threads/structured output. `codex exec
   resume` exists; `--output-schema` doesn't work with resume yet.
2. **Codex native features** (HLD §6 assumption "Codex lacks…"): Codex now has **hooks (11 events),
   skills (open standard), plugins, parallel subagents on by default, profiles-as-overlay-files,
   `codex review`**. The Interaction-Layer sentinel shim for Codex is a fallback, not the plan.
3. **Codex subscription-headless** (RESEARCH §2 matrix): no longer purely gray — device-auth (beta),
   auth.json seeding, and enterprise tokens are **officially documented**; API key remains the
   *recommended* CI path. Posture upgrade: ⚠️→◐ (documented-but-not-preferred). Registry should
   record this per-version.
4. **Codex quota** (RESEARCH §3): 5h windows **shared across local + cloud**; weekly caps exist;
   **credits are purchasable overflow** (12-month validity) — must be excluded by the no-overflow
   rule; machine-readable limit/credit state via app-server + `/status`.
5. **Gemini** (RESEARCH §1.3): `--checkpointing` flag **removed** (settings-only); flag is `--acp`;
   subagents are **sequential** (no local parallel fan-out — parallelism only via background shells,
   Jules, or Actions); hooks + policy engine + skills + extensions shipped; quota is
   **requests/day+RPM**, not 5h windows (free OAuth 1,000/day·60 RPM; AI Pro 1,500/day; Ultra
   2,000/day; free API key is 250/day Flash-only).
6. **⚠️ Gemini CLI replacement risk**: official Gemini 3 docs state unpaid-tier / Google One users'
   "Gemini CLI will be replaced by **Antigravity CLI** on June 18th"
   (<https://geminicli.com/docs/get-started/gemini-3/>). We dropped Antigravity as a GUI IDE
   (DESIGN §2) — but an Antigravity **CLI** would be a new, different fact. **Registry/Updater must
   verify what actually happened** and whether the `gemini` binary path survives for paid tiers.
7. **Claude Code** (RESEARCH §5): add to the native-surfaces list: **Agent Teams (experimental),
   Workflows (scripted JS orchestration), agent view/`--bg` background dispatch with auto-worktrees,
   cron/`/loop`/routines, auto-memory, checkpoint/rewind, `--max-budget-usd`, sandbox network/fs
   isolation, tool search**. `claude mcp serve` is UNVERIFIED in current docs — probe, don't assume.
8. **Docs hosts moved**: Codex → `learn.chatgpt.com/docs` (developers.openai.com 308s);
   Gemini → `geminicli.com/docs`. Update RESEARCH links; Updater/Watcher should track redirects.
10. **Installed-state update (RESEARCH §0, verified 2026-07-27)**: `gemini` is now **installed
   (v0.52.0)**; the operator holds a **Gemini subscription** in addition to Claude — one interactive
   `gemini` "Login with Google" completes worker enrollment. `codex` remains not installed. "Max
   utilization" therefore spans **Claude + Gemini** once login is done, with Gemini rationed to its
   request/day + RPM regime (§2.2).
9. **Convergence note for §7**: Codex v0.145.0 ships **setup import** that migrates Claude Code
   settings/MCP/sessions/memories — vendors are actively absorbing each other's config surfaces;
   favors our common-denominator AGENTS.md strategy.

---

## 4. System design — revised build surface

What remains after delegation is exactly the layer no vendor occupies (each bullet = something we
build; everything else in HLD §3 becomes a wrapper over native features):

```
        ┌──────────────────────────────────────────────────────────────────┐
        │  BUILD (no vendor overlap)                                        │
        │  · Work management + approval inbox + TMS bridge                  │
        │  · Quota Scheduler (multi-regime: 5h/weekly · req-day/RPM)        │
        │  · Autonomy Controller (fleet autopilot over the queue)           │
        │  · MoE routing across vendors (bandit; track-record)              │
        │  · Cross-vendor teams / best-of-N / independent cross-review      │
        │  · Context Engine (graph + RAG) exposed once via MCP              │
        │  · Shared cross-fleet Memory + Guidelines → AGENTS.md             │
        │  · PTY watch/takeover + live handoff + co-editing/diff-sync       │
        │  · Library/learning/TTS/mobile · learner & user-state ML          │
        │  · Capability Registry + Updater/Watcher (probe, delegate, retire)│
        ├──────────────────────────────────────────────────────────────────┤
        │  DELEGATE (native, uniform-wrapped)                               │
        │  · Drivers: Agent SDK / app-server+SDK / ACP + stream-json ×3     │
        │  · Approvals & permissions & sandboxing ×3 · hooks ×3             │
        │  · Subagents & intra-vendor fan-out (Workflows, Multi-agent V2)   │
        │  · Compaction ×3 · checkpoints (Claude, Gemini) · sessions ×3     │
        │  · Web search/fetch ×3 · skills/memory files ×3 · OTEL ×3         │
        │  · First-pass review (codex review, /code-review)                 │
        ├──────────────────────────────────────────────────────────────────┤
        │  SHIM (temporary; Registry retires when native lands)             │
        │  · Worktrees for Codex · file-checkpoints for Codex               │
        │  · Sentinel/clarifier for degraded drivers · Claude-only cron gap │
        │  · Gemini parallel fan-out (sequential subagents) via N sessions  │
        └──────────────────────────────────────────────────────────────────┘
```

## 5. Runtime debugging & maintenance (proposed decision 42)

**Operator requirement (2026-07-27):** the system is not just a coding agent — it must be a **full
debugger and maintainer**: when the application/program is running somewhere, agents connect to the
infra, the database, the logs — everything needed to debug correctly against the *live* system.

**Verdict: mostly DELEGATE.** The connect-and-inspect substrate already exists in all three
harnesses; what we add is scoping, safety, and the autonomous maintain-loop:

| Piece | Verdict | Grounding |
|---|---|---|
| Reaching infra/DB/logs from an agent | **DELEGATE** | Native shell tools run `kubectl`/`psql`/`ssh`/`docker`/cloud CLIs with the operator's credentials; long-running attach via Gemini background shells / Claude background Bash. |
| Structured, scoped access | **DELEGATE (MCP)** | Databases, Kubernetes, Grafana/Prometheus, Sentry, cloud providers all have MCP servers (<https://github.com/modelcontextprotocol/servers>); all three CLIs are MCP clients — connection is configuration. Same seam as our intelligence injection (HLD §5). |
| Access guardrails | **DELEGATE + policy** | Claude sandbox network allowlists + credential masking; Gemini policy engine; Codex approval modes / `codex sandbox`. |
| **Environment & connection registry** | **BUILD** | Per project: environments (dev/staging/prod), endpoints, DB/observability connections, credentials, and an access policy per environment. Extends the Capability Registry + per-task resources; feeds worker sessions as MCP config. No vendor has it. |
| **Environment-scoped autonomy gates** | **BUILD** | Decision-7 gates extended to environments: prod = read-only by default; any mutation (migration, restart, config change) becomes a human-approval task. Live-system actions are never "just tools". |
| **Maintainer loop (observe → propose → fix → verify)** | **BUILD** | Runtime signals (alerts, error logs, failing checks) auto-detected → **proposed tasks in the approval inbox** (decision 35) → diagnose/fix → verify against the live system. This is the Self-Diagnostic agent (decision 22) generalized from the orchestrator to managed applications, as decision 22 already anticipated. |
| Breakpoint-level debugging (DAP) | **SHIM/candidate** | No harness drives the Debug Adapter Protocol (<https://microsoft.github.io/debug-adapter-protocol/>); community DAP-over-MCP servers are candidates for the Comparison/Eval Harness. Log/inspect-style debugging covers most agent workflows meanwhile. |

Roadmap fit: the environment registry + gates slot into M3 (work management: environments hang off
projects; connections are task resources) and M6 (Autonomy Controller enforces environment gates);
the maintainer loop is the M6+ generalization of the self-maintenance agents.

## 6. Cross-project dependency awareness (proposed decision 43)

**Operator requirement (2026-07-27):** working across **multiple projects — some dependent, some
independent — with tasks across or within them** is where the operator struggles most: the
dependency web and the many simultaneous considerations exceed what a person should hold in their
head. The system must see the dependencies and do that considering.

This extends the work model (WORKFLOW §2 has task-level `depends_on` *within* a project) to a
**portfolio-wide dependency graph**, and it is a load the *system* carries — the human-variance
principle (decision 36) and anti-clutter (decision 41) applied to multi-project work:

- **Dependencies are first-class at every level, across boundaries.** Task↔task within a project,
  **task↔task across projects** ("app-A's migration waits on lib-B's release task"), and
  **project↔project relations** (A *consumes* B, A *shares infra with* B, A *independent of* B).
  One DAG spanning all workspaces; `ready` = all upstream deps done, regardless of which project
  they live in.
- **The scheduler walks the DAG, not per-project queues.** The Autonomy Controller's "pull ready
  task" (WORKFLOW §5) becomes portfolio-global: unblocking work in lib-B that gates three app-A
  tasks outranks a local nice-to-have (a critical-path heuristic; later a learned priority model —
  the MoE/prioritization ML, decision 40, gets exactly this as a feature).
- **Code-level deps inform work-level deps.** The Codebase Graph goes **multi-repo**: cross-repo
  blast-radius ("this lib change affects these two consumer projects") auto-*proposes* dependency
  edges and follow-up tasks — which land in the **approval inbox** (decision 35), never auto-run.
- **Visibility (the operator's standing rule):** a dependency/board view that shows *why* anything
  is blocked, what unblocks the most, and what's on the critical path — one thing at a time,
  uncluttered (decision 41): "what should happen next and why" beats a wall of edges.
- **Verdict vs harnesses: BUILD.** No harness has any cross-project notion — their world ends at
  one repo/session. TMS bridges map it where the tracker supports it (Linear/Jira issue links);
  the native store owns it otherwise.

**Dependency authority (operator ruling, 2026-07-27): both may create, the operator decides.**
- **Operator edges are authoritative.** The system never reorders, removes, or overrides a
  human-set dependency; agents cannot mutate the DAG at all.
- **System edges are proposals.** Auto-detected dependencies (blast-radius, task text, failures)
  land in the **approval inbox** (decision 35) and have no scheduling effect until accepted.
- **The system validates, it doesn't judge.** Cycles, contradictions, and impossible orders are
  *flagged* with a resolution question to the operator — never silently "fixed".

**V1 hook (see V1.md):** the flat v1 queue carries a `depends_on` list from day one and the board
shows `blocked(by …)` state — trivially cheap now, structural later; the full cross-project graph,
relations, and critical-path scheduling land with M3 (work management) and the multi-repo graph
with I1.

## 7. Consistency & no-churn (proposed decision 44)

**Operator ruling (2026-07-27):** *don't make confusing features that change later; keep it
consistent; if something has not been made, keep it blocked — don't change.* As a build principle
for this product:

- **Shipped semantics are stable — from the moment a surface is explicitly declared "shipped"**
  (operator ruling 2026-07-27: the declaration is the start line; pre-v1 and experimental surfaces
  may still churn freely). Once declared, a command, state name, or behavior does not flip meaning
  later. Changing a shipped surface is a deliberate, logged migration decision — not
  an iteration detail. (Corollary for the roadmap: v1's names/states — task states, `depends_on`,
  board semantics — are chosen as the *final* shapes from WORKFLOW.md, just partially filled in.)
- **Unbuilt = explicitly `blocked`, never faked.** A feature that isn't implemented appears as a
  visible "not built yet" state (board, UI, CLI errors) rather than a temporary stand-in behavior
  that would later be replaced. No mock flows, no placeholder semantics that train the operator
  wrong. This is the UX twin of the shim-retirement rule (HLD §6): shims emulate a *missing
  harness* capability behind the scenes, but our *own* product surfaces never present provisional
  behavior as real.
- **Additive evolution.** New capability arrives as new states/commands/panes that were previously
  `blocked`, not as mutations of existing ones — the same additive philosophy as the Guidelines
  Engine (INTELLIGENCE §3.3), applied to product surface area.

## 8. Review-load policy (decision 45, operator ruling 2026-07-27)

The fleet is **never throttled below quota by the operator's review backlog**: finished work queues
**unbounded** in `review/gate` and the operator catches up on their own rhythm (decisions 20 + 36).
In exchange the system maintains a **risk profile** per finished diff — size, blast-radius class
(multi-repo graph, decision 43), verifier signal, task class — and **triages** the review queue:
highest-leverage first, low-risk batched. For low-risk items it may raise a **one-tap auto-merge
proposal** in the approval inbox — **never a silent merge**. Full lifecycle rule in `WORKFLOW.md §3`.

## 9. Model Capability Matrix (decision 46, operator addition 2026-07-27)

An explicit, **visible and human-editable** matrix scoring every available **model tier** (Claude
Opus/Sonnet/Haiku/Fable · Codex GPT tiers · Gemini Pro/Flash — all three CLIs expose model/effort
flags, `§1`) against **task types × task stages** (plan/implement/review/test/docs…) on capability,
cost, and quota draw. **Seeded** from cited public benchmarks/model cards; **learned** from our own
outcomes — verifier scores + operator accept/reject/rework as the preference signal (the
RLAIF/reward-model loop, `INTELLIGENCE §7, §11`). Trains the **matrix/routing policy, not the
LLMs** (decision 15 stands — "RLHF" here = preference-learning for routing). Consumed by the MoE
gate to pick worker **and** model-within-worker per stage; the operator can pin/override any cell.
Verdict vs harnesses: **BUILD** — Gemini's Auto routing is intra-vendor and complexity-based only;
no harness scores models per task-stage or learns from your outcomes (`RESEARCH §7.5`).

**Formalization (operator confirmation 2026-07-27): this is a contextual-bandit problem** — the
same framing as the MoE gate (decision 14, `INTELLIGENCE §6`); the matrix *is* the bandit's value
estimates and the router is its policy. **Arms** = (worker, model tier, effort) per stage; **context**
= task features (type, stage, size, language, blast-radius, history); **reward** = verifier score +
operator accept/reject/rework; benchmark seeding = **informative priors** (don't spend quota
exploring what public data already answers). Three deviations from the textbook problem shape the
algorithm choice (candidates for the Eval Harness, per decision 19):
1. **Budgeted** (bandits-with-knapsacks): optimize **reward per unit of perishable quota** under
   the reservoir budget (decision 20), not raw reward — else the policy degenerates to "always the
   biggest model."
2. **Non-stationary:** vendors churn models/CLIs; a Capability-Registry version bump must decay
   estimates and re-trigger exploration for affected arms.
3. **Delayed, sparse reward:** verify/review lags routing and per-cell samples are tiny → cells
   share structure (factored/linear models across similar stages and model families), not
   independent counters.

**Net effect on the roadmap (DESIGN §10):** M0–M2 shrink (drivers and the Interaction Layer are
mostly delegation now — the sentinel work in M2 drops to a fallback); M1 gains "probe app-server +
ACP + import quota-state surfaces"; M6's Quota Scheduler gains the multi-regime requirement and the
no-credits guard for Codex; M8's teams layer is re-founded on "native teams as composite workers".
The four unique pillars and the intelligence track are untouched — they were already aimed at the
empty ground, and this inventory confirms the ground is still empty.

## 10. Peer harness calls — cross-vendor at any level (proposed decision 50, operator ruling 2026-07-29)

**Operator requirement (2026-07-29):** cross-vendor mixing and routing may happen **at any level** —
not only Podium assigning top-level workers, but a *running* harness session calling on another
vendor's harness mid-task when one is available and needed (e.g., a Claude session pulling in Gemini
for a huge-context read; Codex asking for a Claude review of its diff).

**Mechanism: brokered, never direct.** Podium exposes a **`peer` tool over MCP** (the seam all three
CLIs already speak, §1) to every worker session: `peer.request(need, constraints) → result`. The
request expresses a **need, not a vendor choice**; it enters the **routing gate**
(`INTELLIGENCE §6`) like any task, and the gate satisfies it with whichever expert fits — any vendor,
an internal capability agent, or **abstain/escalate**. This extends peer routing (`INTELLIGENCE
§6.1`) downward: peers can now *originate* requests, with the gate still in charge. Native halves the
Registry should prefer as satisfier transports where they fit: `codex mcp-server` (Codex as a
callable tool, §1) and Gemini **remote A2A agents**.

**Direct harness→harness calls are blocked by policy** (an agent shelling out `gemini -p` from
inside a Claude session) via the native sandbox/permission rules ×3 (§1). That naive version is
where the operator's "will there be problems?" answer lives — each of these is real if calls bypass
the broker, and each is closed by brokering:

1. **Invisible quota burn.** A nested call drains a *different vendor's* reservoir with no
   attribution; the Quota Scheduler (decision 20) must meter every call against the right
   per-vendor regime (§2.2). Brokered calls are ordinary scheduled sessions — fully metered.
2. **Routing authority conflict.** Two routers fighting — the harness's "I want Gemini" vs. our MoE
   gate. Ruling: the inner request is a need; **the gate picks the satisfier** (which may not be the
   vendor asked for, or any vendor at all).
3. **Recursion/cycles.** A calls B calls A. Brokered requests carry **lineage + a call-depth cap**
   (default depth 1; deeper is config), same validation posture as the dependency DAG (§6).
4. **Observability holes.** Direct sub-calls bypass PTY watch/takeover, OTEL, and transcripts.
   Brokered satisfiers are normal sessions with full live visibility.
5. **Provenance laundering.** Peer output re-entering the caller's context must carry authorship
   provenance (decision 39) so no agent mistakes another agent's output for ground truth.
6. **Reward mis-attribution.** The capability matrix / bandit (decision 46) must credit the arm
   that actually did the work; the broker records (caller, satisfier, outcome) separately.
7. **Permission laundering.** The satisfier runs under **its own** policy for the task's
   environment (decision 42 gates), never inheriting the caller's approvals.

**Intra-vendor subagents are monitored too (operator addition 2026-07-29).** The same visibility
requirement applies when a harness fans out *within itself* — Claude spawning Claude subagents or
Workflows, Codex Multi-agent V2, Gemini's sequential subagents. These stay native (the delegation
verdict of §2.2 stands) and their quota burn already lands on the caller's own reservoir, but Podium
**observes inside the composite worker**: subagent lifecycle via native hooks (SubagentStart/Stop on
Codex, Claude's 15+ hook set, Gemini's 11 events, §1) plus OTEL and the structured event streams feed
a live **subagent tree** in mission-control (decision 25). Every subagent is provenance-tagged
(decision 39; hooks report tool/session identity, §2.4) and metered, so the capability matrix
attributes outcomes to the composite arm while the operator can still see — and intervene at — any
level. No nested agent activity, cross-vendor or intra-vendor, is a black box.

**Verdict vs harnesses: BUILD** (the broker + gate integration + subagent tree view); ingestion of
subagent lifecycle **DELEGATEs** to native hooks/OTEL ×3, and the injection seam **DELEGATEs** to
the native MCP clients ×3. Roadmap fit: the `peer` tool ships with the MCP intelligence seam (I-track);
depth>1 and team-originated fan-out land with M8.

## 11. Transparent operator models — visibility & the no-ceiling rule (proposed decision 51, operator ruling 2026-07-29)

**Operator requirement (2026-07-29):** full visibility into "what the system thinks about me," and a
guarantee that a low estimate can never limit the operator's learning — the operator can raise their
effort at any time, and the system must respond to that, not to a stale label.

- **Inspectable.** A **"what the system believes about me" panel** (TUI first, per decision 18):
  every learner-model estimate (knowledge tracing, `INTELLIGENCE §11`) and user-state reading is
  visible, each with its **evidence** — which first-party signals produced it (provenance,
  decision 39). No hidden scores anywhere in the system.
- **Editable, pinnable, resettable.** The operator can correct, pin, or wipe any estimate; operator
  edits are **authoritative** (the same authority pattern as dependency edges, §6: the system
  proposes, the human rules).
- **The no-ceiling rule.** Model estimates **sequence and scaffold — they never gate.** A low
  estimate *expands support* (more introductory steps offered on the learning path); it never
  hides, locks, or withholds material, tasks, or ambition. Every learning path is skippable and
  overridable — an **effort override** ("show me the expert path anyway") is always one action
  away, and taking it is itself first-party signal: demonstrated effort raises estimates. The
  models exist to serve the operator's stated ambition, not to cap it.
- **Scope.** Extends decision 38's "human-controlled, transparent, not surveillance" from the
  user-state model to **all** operator-modeling (learner model included), consistent with additive
  evolution (§7) and the human-variance principle (decision 36).

**Verdict vs harnesses: BUILD** — no harness models the operator at all (§2.3); transparency
surfaces ride the existing TUI/mission-control planes (decisions 18, 25).
