# Grounding Research & Prior-Art Positioning

**Status:** Living reference · **Date:** 2026-07-24 · Companion to `DESIGN.md`.

This doc records the *verified* facts the design rests on, with primary sources, so every
claim is checkable (no hallucination). It covers: (1) how to correctly drive each coding CLI,
(2) subscription-auth-for-automation and the ToS matrix, (3) the quota / utilization model,
(4) the prior-art survey and what we reuse vs. build, (5) tools already installed to leverage.

> Sourcing rule: prefer primary sources (official docs/repos). Blog/secondary links are marked.
> Live-state facts (quota regimes, versions) are dated and may change — re-verify before relying.

---

## 0. What is actually installed here (verified 2026-07-24)

| Tool | State | Notes |
|---|---|---|
| `claude` | ✅ v2.1.218 at `~/.local/bin/claude` | Subscription auth via `~/.claude/.credentials.json` (no API key). |
| `codex` | ❌ not installed | Needs `npm i -g @openai/codex` + `codex login`. |
| `gemini` | ✅ v0.52.0 (installed 2026-07-27) | Operator holds a **Gemini subscription**; one-time "Login with Google" OAuth still pending → then a real worker. |
| `codegraph` | ✅ v1.1.0 (global npm) | Code-intelligence graph; daemon + MCP server; supports Claude/Codex/Gemini. |
| `scip-python`, `scip-typescript` | ✅ (global npm) | Precise SCIP code indexers. |
| API keys | ❌ none set | `ANTHROPIC_/OPENAI_/OPENROUTER_/GEMINI_/GOOGLE_` all unset — **subscription/login auth only**. |

**Consequence:** "max utilization" = **Claude now, + Gemini after its one-time login**; Codex still
requires install + login. Full July-2026 verified harness inventory: `CAPABILITIES.md`.

---

## 1. Driving the coding CLIs correctly (not the built-in `claude agents`, not raw API)

There are effectively **four ways** to drive an agent CLI (from the prior-art survey):
1. **tmux + real interactive CLI** — spawn the normal binary in a pane, ANSI-strip the output,
   automate confirmations with `send-keys`. Enables true human takeover (attach/detach).
2. **Headless structured stream** — `claude -p --output-format stream-json` etc.; parse events.
3. **Official SDK** — the Claude Agent SDK (bundles + shells out to the binary).
4. **Own agent loop** — skip CLIs, call model APIs (we do NOT do this — API is out per ToS/cost).

We use **(3) SDK / (2) headless for autonomous/structured runs** and **(1) PTY for interactive
watch/takeover** — both drive the *same* `claude` binary under the *same* subscription auth.

### 1.1 Claude — the current default worker (cleanest headless-subscription path)
- **Claude Agent SDK** (renamed from "Claude Code SDK" late 2025). Python `claude-agent-sdk`
  (`ClaudeAgentOptions`, not the old `ClaudeCodeOptions`); TS `@anthropic-ai/claude-agent-sdk`.
  It **bundles and shells out to the `claude` binary** over stream-json, so it *is* driving the
  CLI correctly, with the permission/control channel implemented for you.
  - Repos: <https://github.com/anthropics/claude-agent-sdk-python> ·
    <https://github.com/anthropics/claude-agent-sdk-typescript> ·
    action <https://github.com/anthropics/claude-code-action>
  - Docs (moved to `code.claude.com`): <https://code.claude.com/docs/en/agent-sdk/overview> ·
    headless <https://code.claude.com/docs/en/headless> · CLI ref
    <https://code.claude.com/docs/en/cli-reference>
  - Modes: `query(prompt, options)` (one-shot / stateless) vs **`ClaudeSDKClient`** (persistent,
    bidirectional — interrupt, queue, live permission requests; **recommended**).
  - Autonomy/control: `permission_mode` (`default`/`acceptEdits`/`plan`/`auto`/`dontAsk`/
    `bypassPermissions`), `allowed_tools`/`disallowed_tools`, **`can_use_tool`** callback and
    **`PreToolUse` hooks** that can return `deny` — this is our programmatic "answer the prompt".
  - Intelligence injection: in-process MCP via `@tool` + `create_sdk_mcp_server(...)`.
  - Utilization: `startup()` pre-warms a subprocess pool; `--worktree` isolates parallel runs;
    session `resume`/`fork` + pluggable `SessionStore`.
- **Raw headless** (what the SDK does under the hood): `claude -p --output-format stream-json
  --input-format stream-json --verbose [--json-schema …] [--permission-mode …]`. `stream-json`
  needs `--verbose`; the final `result` line is terminal. **Gotcha:** piped (non-TTY) stdout is
  **block-buffered** → looks like a hang; use the SDK or allocate a **PTY**. (Issue
  <https://github.com/anthropics/claude-code/issues/25670>; stream-json input under-documented
  <https://github.com/anthropics/claude-code/issues/24594>.)
- **Model/effort:** `--model opus|sonnet|haiku|fable|<id>`, `--effort low|…|max`,
  `--fallback-model` → the substrate for "whatever is best per task".

### 1.2 Codex CLI (OpenAI)
- Install `npm i -g @openai/codex` (binary `codex`, Node 22+). Repo
  <https://github.com/openai/codex>; docs <https://learn.chatgpt.com/docs>.
- Headless: `codex exec "…" --json -a never -s workspace-write`; `--output-schema` structured
  (not yet supported on `exec resume` — [#22998](https://github.com/openai/codex/issues/22998));
  `codex exec resume --last/<id>` continues sessions; `--ephemeral` for stateless runs;
  `-c model_reasoning_effort=…`. `exec` still has no interactive answer channel — use app-server.
- Bidirectional control (**corrected mid-2026**): **`codex app-server`** is the blessed public
  integration surface — JSON-RPC 2.0 over stdio (WS/unix experimental), `thread/*` lifecycle,
  `turn/start|steer|interrupt`, approval callbacks, rate-limit queries, per-version schema codegen
  (<https://developers.openai.com/codex/app-server>,
  <https://openai.com/index/unlocking-the-codex-harness/>). The official **TS SDK**
  (`@openai/codex-sdk`) wraps it. `codex mcp-server` separately exposes Codex *as* an MCP tool.
- Native since late-2025/2026 (**don't shim**): parallel **subagents on by default** (Multi-agent
  V2), **hooks** (11 events), **skills** (open `SKILL.md` standard), **plugins**, overlay-file
  **profiles**, `codex review` — inventory in `CAPABILITIES.md §1`.
- MCP client: `codex mcp add …` (stdio + streamable HTTP + OAuth).

### 1.3 Gemini CLI (Google)
- Install `npm i -g @google/gemini-cli` (binary `gemini`, Node 20+). Repo
  <https://github.com/google-gemini/gemini-cli>.
- Headless: `gemini -p "…" --output-format json|stream-json --approval-mode
  default|auto_edit|yolo|plan`. Exit codes: 0 ok, 1 error, 42 input, 53 turn-limit.
- Bidirectional control: **`--acp`** (Agent Client Protocol, JSON-RPC over stdio) — Gemini's
  stream-json analog with approval callbacks (`setSessionMode` adjusts the approval level
  mid-session). Shadow-git checkpointing is **settings-only now** — the `--checkpointing` flag was
  removed in v0.11.0.
- Native since 2026 (**don't shim**): **hooks** (11 events), **TOML policy engine** (per-subagent
  rules), **skills**, **extensions + gallery**, **subagents** (sequential-only locally; remote A2A
  agents), **Auto model routing** with quota-fallback chains — inventory in `CAPABILITIES.md §1`.
  ⚠️ Official docs state unpaid-tier Gemini CLI "will be replaced by Antigravity CLI"
  (<https://geminicli.com/docs/get-started/gemini-3/>) — the Registry/Updater must verify actual
  impact on paid tiers.
- MCP client: `mcpServers` in settings / `gemini mcp add …` (Gemini cannot act as an MCP server).

### 1.4 Universal tool-injection = MCP
All three load **MCP** servers, so our intelligence layer (codebase graph, RAG, memory,
guidelines) is exposed once as MCP tools and pulled by each agent. `codegraph` confirms
Claude Code + Codex CLI + Gemini CLI as MCP hosts:
<https://colbymchenry.github.io/codegraph/reference/integrations/>.

---

## 2. Subscription auth for automation & the ToS matrix

The user's rule: **official CLIs, own accounts/machine, subscription (not API key), one operator,
ToS-clean.** How clean each vendor's *headless-on-subscription* path is:

| Worker | Subscription-headless mechanism | ToS posture | Verdict |
|---|---|---|---|
| **Claude** | `claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN` (~1-yr OAuth), or existing `/login` creds in `~/.claude/.credentials.json`. **Unset `ANTHROPIC_API_KEY`** or it overrides subscription. | Anthropic **officially documents** this for CI/scripts; `claude-code-action` supports `claude_code_oauth_token`. | ✅ **Clean** — the one we auto-saturate. |
| **Codex** | `codex login` (ChatGPT Plus/Pro) or `--device-auth` (beta); reuse/seed `~/.codex/auth.json` (documented "advanced" path); Enterprise gets non-interactive tokens. | OpenAI still **recommends API keys for automation**, but device-auth + auth.json seeding are now **officially documented** headless-subscription paths → documented-but-not-preferred (`CAPABILITIES.md §3.3`). | ◐ **Documented**, user opts in: both modes, **rationed to message/rate limits**. |
| **Gemini** | "Login with Google" OAuth (AI Pro/Ultra/Code Assist), cached in `~/.gemini`. `GOOGLE_GENAI_USE_GCA=true`. | Google **recommends API key / Vertex service account** for CI; personal OAuth automation risks caps + intended-use. | ⚠️ Gray per vendor; **operator confirmed 2026-07-27: full unattended participation, rationed** — a conscious gray-area acceptance on their own account. |

**Design rule:** the **Capability Registry** records each worker's auth + ToS posture. Per the
user's decision, **all three workers run in *both* interactive and headless modes**, each
**rationed to its own message/rate limits** — Claude has the cleanest headless-subscription path;
Codex/Gemini are the user's own accounts/risk and are rate-optimised to stay within intended use.
The Autonomy Controller saturates each worker up to — never beyond — its limits, and never spends
overflow/API credits. (Note: the **main-workhorse role is worker-agnostic and configurable** — any
worker can fill it, chosen by availability + capability + quota + user config (later the MoE router).
Claude is merely the *current* default because it's the only installed worker with a sanctioned
headless-subscription path — **not** an architectural privilege. Codex/Gemini participation is a
per-worker setting the user controls. See DESIGN decision 34.)
Sources: Anthropic auth <https://code.claude.com/docs/en/authentication>; Codex auth
<https://learn.chatgpt.com/docs/auth>; Gemini auth
<https://github.com/google-gemini/gemini-cli/blob/main/docs/get-started/authentication.mdx>.

---

## 3. Quota & utilization model (the heart of "max usage")

**Verified current state (2026-07-24 — live, re-verify):**

- Claude subscriptions enforce **two independent interactive limits**: a **rolling 5-hour window**
  (counter starts on your first prompt, resets 5h later) and a **weekly cap** (7-day rolling; some
  community monitoring reports an effective ~72h reset). Each window grants a **fresh allocation
  regardless of prior use → unused capacity does NOT roll over ("use it or lose it").**
  ([morphllm](https://www.morphllm.com/claude-code-usage-limits),
  [TrueFoundry](https://www.truefoundry.com/blog/claude-code-limits-explained))
- A **separate monthly Agent-SDK credit** (Pro $20 / Max 5x $100 / Max 20x $200) that would carve
  `claude -p` / Agent SDK / GitHub Actions / third-party-app usage *out of* the interactive limits
  was **announced for 2026-06-15 and then PAUSED by Anthropic on 06-15/16 — it is NOT in effect.**
  So headless currently draws from the **same** 5h + weekly limits as interactive.
  ([DigitalApplied: the pause](https://www.digitalapplied.com/blog/anthropic-claude-credit-overhaul-june-15-2026),
  [claudefa.st](https://claudefa.st/blog/guide/development/agent-sdk-credit),
  [Anthropic support](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan))

**What this means for the orchestrator:**
- The perishable **5h + weekly** limits are the target. Whenever nothing drives Claude, that
  capacity evaporates. Always-on autonomous work (interactive-PTY and/or headless-SDK — both hit
  the same pool today) soaks it up to the cap.
- **Ration + fully utilise (the pacing objective).** Treat each **5-hour window** as
  burn-it-or-lose-it — finish as much of every 5h cycle as possible — **but pace weekly
  consumption** so the **weekly cap lasts the whole week and drains *almost completely just before*
  it resets**, never front-loaded and starved mid-week. Formally: a reservoir / token-bucket
  controller that maximizes 5h utilization subject to a weekly budget smoothed across 7 days.
  Holds whether interactive+headless are one bucket (today) or two (if the split un-pauses).
- **Never spend overflow / API-rate credits** (hard user rule). Back off as caps approach.
- **Regime-aware:** if the SDK-credit split un-pauses, interactive (5h+weekly) and a separate
  headless monthly credit become two pools to soak; the scheduler detects the regime and adapts.
- **Multi-regime across workers (2026-07 update, `CAPABILITIES.md §2.2`):** the reservoir controller
  must model **per-worker regimes**, not one shape. **Codex:** 5h rolling windows **shared across
  local + cloud** + weekly caps; **credits are purchasable overflow (never spend — decision 20)**;
  limit + credit state is machine-readable via `/status` and app-server rate-limit methods
  (<https://developers.openai.com/codex/pricing>). **Gemini:** a **requests/day + RPM** regime, not
  5h windows — free OAuth 1,000/day · 60 RPM; AI Pro 1,500/day; Ultra 2,000/day; `/stats` for
  visibility (<https://geminicli.com/docs/resources/quota-and-pricing/>).

**Reading remaining balance:** official remaining is behind account auth — use **`/usage`** in
interactive Claude Code, or the billing page. No CLI subcommand exposes it. Community tool
**[ccusage](https://github.com/ryoppippi/ccusage)** (`npx ccusage`) reports *consumption* from
`~/.claude` logs (its `$` is API-equivalent estimate, not your bill). Our own metrics layer will
track a live utilization gauge from session telemetry.

---

## 4. Prior-art survey — what we reuse vs. what we build

Indexes: <https://github.com/andyrewlee/awesome-agent-orchestrators> ·
<https://github.com/bradAGI/awesome-cli-coding-agents>.

| Project | Drive mechanism | Isolation | Multi-CLI | Always-on | Best-of-N | Human takeover |
|---|---|---|---|---|---|---|
| [claude-squad](https://github.com/smtg-ai/claude-squad) | real CLIs in **tmux** | worktrees | Claude/Codex/Gemini/Aider/OpenCode/Amp | detached, yolo | manual | **tmux attach** |
| [uzi](https://github.com/devflowinc/uzi) *(active)* | tmux + auto-Enter | worktrees + ports | Claude/Codex/Cursor/Aider | `uzi auto` | manual¹ | attach + broadcast |
| [vibe-kanban](https://github.com/BloopAI/vibe-kanban) *(sunsetting)* | headless spawn | worktrees | 10+ | — | — | web kanban diff/PR |
| [coder/mux](https://github.com/coder/mux) | **own loop**, APIs | worktrees + SSH remote | multi-model | yes | **A/B** | in-app review |
| [amux](https://github.com/mixpeek/amux) | **tmux**, ANSI scrape | session | Claude/Codex/Gemini | **self-healing watchdog** | — | approval flow + **phone** |
| [ruflo / claude-flow](https://github.com/ruvnet/ruflo) | **MCP plugin** on Claude Code | topology + memory ns | Claude/Codex | **timer workers**, federation | swarm | dashboard |
| [bernstein](https://github.com/chernistry/bernstein) | headless, **deterministic** | worktrees | Claude/Codex/Gemini | loop | parallel | **verifier gate** |
| [ccpm](https://github.com/automazeio/ccpm) | PM layer (bash + harness) | worktree/epic | agnostic | — | parallel tasks | GitHub Issues |
| [Backlog.md](https://github.com/MrLesk/Backlog.md) | PM layer | — | agnostic | — | — | spec/plan/code gates |

Runtimes we drive (not orchestrators): Claude Agent SDK, [opencode](https://github.com/sst/opencode)
(REST/SSE target), [aider](https://github.com/Aider-AI/aider).

**What we reuse (don't reinvent):**
- **tmux/PTY + git-worktree + port-allocation** as the execution substrate (claude-squad, uzi).
- **Self-healing watchdog** — auto-compact on high context, rate-limit handling, idle re-prompt,
  auto-resume on limit reset (amux, Codeman) — this *is* our always-on utilization engine.
- **SQLite kanban with atomic task-claiming** so idle agents pull work (amux).
- **Best-of-N + checkpoint-merge** (uzi, coder/mux) and **deterministic planner + pre-merge
  verifier gate** (bernstein) — throw-away minimization.
- **GitHub-Issues-as-truth + deterministic-bash ops** (ccpm), **spec/plan/code review checkpoints**
  (Backlog.md) — the work-management half + TMS bridge pattern.
- **Agent SDK primitives** — `session_id` resume/**fork**, subagents, `PreToolUse`/`Stop` hooks,
  `permission_mode`.

**What we build (the field is thin here — our differentiators):**
1. **Capability / MoE routing** — nobody routes a task to the *right* agent/model by task-type,
   cost, quota, or track record. Everyone runs a fixed default or naive best-of-N.
2. **Codebase-graph-aware decomposition & semantic conflict avoidance** — worktrees isolate files
   but agents still collide semantically; a real graph can *partition* work to prevent conflicts.
3. **Shared RAG / memory across the fleet** — most agents start cold every task; only ruflo/Swarm
   attempt cross-agent memory.
4. **A real verifier in the loop** — most tools merge on green CI or eyeball; a dedicated
   verifier/critic scoring runs (and gating best-of-N selection) is underexplored.
5. **Utilization/scheduling intelligence** — "keep the best agent busy within perishable quota,
   never overflow" is essentially unsolved beyond atomic-claim + auto-resume.

¹ uzi's "best-of-N" is a **manual** `checkpoint` merge (human-selected), not automated.

> **Mid-2026 correction (from the §7 landscape scan).** Two of the five "thin areas" have been
> **overtaken** and must be softened: **(4) verifier-in-the-loop is now common** (Cursor Bugbot,
> bernstein Janitor, kodo, Qodo judge, Amp oracle) — table stakes, not a gap; **mobile is
> widespread**, not a differentiator. **(3) shared RAG/memory** is filling in fastest (Linear
> `agent-memory` beta, Devin DeepWiki, OpenHands microagents). The genuinely-still-empty ground is
> **(5) quota-aware rationing** (unique), **capability/MoE routing** (1), **graph-based semantic
> conflict-avoidance** (2, *"untouched by everyone"*), and **tracker-agnostic native
> work-management + bidirectional bridge**. See §7.

Net: clone the well-solved plumbing; concentrate our effort on the **intelligence + work-management
+ quota-aware utilization** layers. Details in `INTELLIGENCE.md` and `WORKFLOW.md`.

---

## 5. Tools already installed to leverage

> **No premature component decisions.** Every provider (models, codebase-graph, embedders,
> rerankers, routing policies, retrieval strategies) sits behind an interface and is **swappable**.
> A first-class **Comparison / Eval Harness** runs comprehensive head-to-head benchmarks and picks
> winners **empirically, later** — nothing below locks an implementation.

- **`codegraph`** (v1.1.0) — a code-intelligence knowledge-graph **candidate** (daemon + MCP server;
  `init/index/sync/query/explore/callers/callees/impact/affected`, supports Claude/Codex/Gemini).
  It is **one option, not a decision** — the codebase-graph provider is pluggable and the choice
  (codegraph vs. SCIP-based vs. our own tree-sitter graph vs. others) is made by the Comparison/Eval
  Harness. Repo <https://github.com/colbymchenry/codegraph>. May or may not win; **do not assume**.
- **`scip-python` / `scip-typescript`** — precise SCIP indexers for exact symbol resolution.
- **Claude Code native surfaces** we build *our own runner* on top of (not depend on as the loop):
  `--worktree`/`--tmux` isolation, `mcp` management, `--agents` custom subagents, `plugin`/skills,
  `setup-token` auth, `-p`/stream-json headless, model/effort/fallback selection.

---

## 6. Open items / regime-awareness / self-maintenance

- **Quota split may un-pause** → keep the scheduler regime-aware (one-pool vs two-pool).
- **Codex/Gemini run in both modes** (user opted in), each rate-optimised to its message/limit
  budget; Claude stays the safest default for unattended saturation.
- **CLIs evolve fast** → the Capability Registry probes versions and delegates to native features
  first (memory, MCP, subagents, structured output), shimming only gaps.
- **Component choices are decided by comparison, not upfront** → the Comparison/Eval Harness (§5)
  benchmarks candidates (models, graph, embedders, rerankers, routing) head-to-head and selects.
- **Periodic self-maintenance agents (internal capability agents):**
  - **Updater / Watcher** — periodically checks for CLI / model / tool updates and capability
    changes, feeding the Capability Registry so the system tracks the evolving underlying CLIs.
  - **Self-Diagnostic** — periodically reads the system's own internal error logs and debugs when
    something isn't working.
  Built for **this orchestration system first (dogfood)**; **generalizable later** to the external
  codebases this system edits/manages.

---

## 7. Competitive landscape (mid-2026)

Grounded scan of ~50 systems across three clusters: commercial autonomous SWE agents, orchestration
/ work-management control planes, and learn-while-building tools. Primary-source links inline. Live
facts (pricing, versions, model names) move weekly — re-verify. "Nobody does X" is **inference** from
this survey; a stealth startup could exist without a web footprint. Bleeding-edge model *names* (e.g.
"GPT-5.6 Sol") are treated as unverified.

### 7.1 Structural shifts to know first
- **Cognition owns both Devin and Windsurf** — Windsurf rebranded **"Devin Desktop"** (2026-06-02);
  `windsurf.com` → `devin.ai`. ([rebrand](https://devin.ai/blog/windsurf-is-now-devin-desktop/))
- **Amp spun out of Sourcegraph** (independent, Dec 2025); **Sourcegraph Cody sunset**.
  ([spin-out](https://sourcegraph.com/blog/why-sourcegraph-and-amp-are-becoming-independent-companies))
- **GitHub Copilot Workspace folded** into the coding agent + **Agent HQ** — a "mission control"
  that orchestrates *third-party* agents (Anthropic/OpenAI/Google/Cognition/xAI) across web/IDE/
  mobile/CLI. ([Agent HQ](https://github.blog/news-insights/company-news/welcome-home-agents/))
- New model-lab agent-IDEs: **Google Antigravity**, **Amazon Kiro** (GA), **Claude Code on the web**,
  **Warp Oz** (open-sourced its core ADE), **IBM Bob**. **Qodo pivoted** to review/governance.
- **Roo Code shut down (2026-05-15)**; **vibe-kanban sunsetting**; **Cline** grew into a full
  orchestration platform (kanban + cron agents + teams).
- The whole field **converged off flat seats onto metered/credit billing** in 2026.

### 7.2 Commercial autonomous SWE agents — components (leaders)

Legend: ● strong · ◐ partial · ○ none.

| Product | Repo graph/RAG | X-session memory | Multi-agent | Verifier | Routing | Openness | Auth/billing |
|---|---|---|---|---|---|---|---|
| **Devin** (Cognition) | ● DeepWiki | ● Knowledge | ● +Swarm | ● | ○ | Closed | Sub + credits |
| **Factory (Droids)** | ● AutoWiki+Chroma | ◐ | ● Missions | ● | ● Router+BYOK | Closed | Sub + usage |
| **Cursor** | ● index | ◐ Memories | ● swarms/BoN | ● Bugbot | ● Router | Closed | Sub + metered |
| **GitHub Copilot / Agent HQ** | ● +AGENTS.md | ◐ Memory (preview) | ● 3rd-party fleet | ● self+scan | ◐ picker | Closed / open conv | Sub + AI Credits |
| **OpenAI Codex** | ◐ agentic | ◐ Skills | ● +review agent | ● auto-review | ○ | **CLI/SDK OSS** | Sub credits *or* API |
| **Google Jules** | ◐ | ● repo memory | ● (3–60 concur.) | ● Planning Critic | ○ | Agent closed; API/CLI open | Google AI sub |
| **Claude Code (web)** | ◐ | ◐ | ● parallel | ● auto-fix PR | ○ | Closed svc | Pro/Max sub |
| **Augment** | ● Context Engine | ● Memories | ● Remote fleet | ● | ◐ | Closed | $100 floor + usage |
| **Amp** (independent) | ● Librarian | ◐ Threads | ● subagents+oracle | ◐ | ◐ 4 modes | Closed | Credits, no markup |
| **Qodo** (review/gov) | ● Context Engine | ● Rule Miner | ● review+judge | ● | ○ | PR-Agent OSS | $30 + credits |

Sources: [Devin](https://docs.devin.ai/) · [Factory](https://factory.ai/) · [Cursor cloud agents](https://cursor.com/blog/cloud-agents) ·
[Copilot coding agent](https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent) ·
[Codex cloud](https://learn.chatgpt.com/docs/cloud) · [Jules](https://jules.google/docs) ·
[Claude Code web](https://code.claude.com/docs/en/claude-code-on-the-web) ·
[Augment Context Engine](https://www.augmentcode.com/context-engine) · [Amp manual](https://ampcode.com/manual) ·
[Qodo](https://docs.qodo.ai/code-review). (Also surveyed: Antigravity, Kiro, Replit Agent 3, Trae⚠, Junie,
Zencoder, Warp Oz, IBM Bob, Tabnine, Charlie, Potpie, Amazon Q.)

**Table stakes (mid-2026, not differentiating):** cloud sandbox/ephemeral VM; plan-then-execute;
prompt-to-PR with human PR review; async/background/parallel "fleets"; some repo context (AGENTS.md +
index); a self-review/verifier pass; MCP; Slack + issue-tracker triggers (esp. **Linear as delegation
surface**); usage/credit metering; a ~$20 Pro / ~$100–200 Max spine.

**Genuine differentiators (only some have them):** auto-generated repo **knowledge graph/wiki**
(DeepWiki, AutoWiki, Augment); **cross-session learned memory**; a **distinct verifier/critic** (vs
self-review); **best-of-N** multi-model competition (Cursor); **task→model routing** (Cursor/Factory
Router — but *model* routing by cost, not capability/quota/track-record); **persistent compute**
(Factory Droid Computers, Amp Orbs); **native mobile app**; **BYOK/BYO-machine**; **cross-vendor agent
orchestration** (Agent HQ, Warp Oz, Devin Desktop ACP — the newest frontier); **self-waking
schedulers** (Amp Schedules, ~unique).

⚠ **Trae (ByteDance)** carries substantiated 2025 telemetry/privacy concerns
([OECD.AI incident](https://oecd.ai/en/incidents/2025-07-28-25b9)) — flag for IP-sensitive use.

### 7.3 Orchestration control planes + work-management + TMS

Three work-management patterns dominate:
1. **Issues-as-truth + bridge** (mainstream). Tracker owns state; agent gets an issue, returns a
   draft PR. The 2026 refinement: a **typed agent-session lifecycle posted back to the ticket**.
   Three real **third-party-agent protocols** now exist and our TMS Bridge should target them:
   **[Linear](https://linear.app/developers/agent-interaction)** (OAuth `actor=app` + `AgentSessionEvent`
   webhooks + `agentActivityCreate`), **[GitHub Agent Tasks API](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/use-cloud-agent-via-the-api)**,
   **[Atlassian Forge `agentConnector`](https://developer.atlassian.com/platform/forge/remote-agents-in-jira/)** (JSON-RPC/SSE). All with a **human-stays-accountable** guardrail.
2. **Native kanban board** (orchestrator owns the queue) — [amux](https://github.com/mixpeek/amux)
   (SQLite board + gates + atomic claiming), [Backlog.md](https://github.com/MrLesk/Backlog.md),
   [5dive](https://github.com/5dive-ai/5dive), Cline's kanban mode.
3. **Ephemeral parallel** (no persistent board) — claude-squad, uzi, Sculptor, Crystal.

**Quota-aware rationing = confirmed unique open gap.** Nearest: **amux** (reactive failover + a daily
budget cap — reacts to the wall, doesn't pace to avoid it); **Claudexor** (reads real 5h/7d headroom
from the vendor `oauth/usage` endpoint but uses it to **rotate across multiple accounts** — which we
won't do, ToS); **cc-router** (sequential exhaustion + round-robin). Even vendors exposing the same
perishable structure (Antigravity, Claude Code web) **impose** caps without pacing. Nobody schedules
to keep the best agent maximally busy within one perishable budget, smoothing the weekly cap to drain
just before reset.

### 7.4 Learn-while-building — the combination is novel

No mid-2026 product combines all four: (1) an autonomous coding **orchestrator**, (2) a personalized,
project-driven **CS/SWE tutor** (theory + the exact libraries in use, from plain-language intent),
(3) a **read-or-listen** curated library, (4) a **learner-model ML** trained on the user's own
project/behavior/agent data. Nearest neighbors cover *half*:
[Codecademy AI Builder](https://www.codecademy.com/resources/blog/why-the-future-of-learning-starts-with-building)
(project→learning; no orchestrator/listen/learner-ML), [Educative Personalized Paths](https://www.educative.io/blog/learn-to-code-personalized-learning-plans)
(curated path + gap model; decoupled from real project), [Boot.dev "Boots"](https://www.boot.dev/blog/news/bootdev-beat-2026-01)
(Socratic tutor + mastery; fixed curriculum), [NotebookLM](https://www.xda-developers.com/notebooklm-audio-overview/)
(listen-to-sources; no coding/learner dimension). The learner model has a proven technique —
**Knowledge Tracing (BKT / DKT / [Code-DKT](https://arxiv.org/pdf/2112.08273))** — our twist: train it
on **first-party signal** (edits, questions, agent traces), not quiz answers. Market wedge is named:
"[vibe coding is compressing the learning loop until learning disappears](https://www.frontendmentor.io/articles/vibe-coding)"
vs Anthropic's "[developer as orchestrator of agent teams](https://resources.anthropic.com/hubfs/2026%20Agentic%20Coding%20Trends%20Report.pdf)."

### 7.5 Where we differ — honest pillar matrix

| Our pillar | Verdict | Why |
|---|---|---|
| **Free meta-orchestrator on the user's OWN CLI subscriptions** (no API keys/credits) | **UNIQUE** | Every commercial product bills its *own* inference; only OSS hobby tools drive your CLIs, and they lack the rest. Closest (Agent HQ, Warp Oz, Devin Desktop ACP) orchestrate on the **vendor's** billing. |
| **Quota-rationing max-utilization scheduler** | **UNIQUE** | §7.3 — nobody paces one perishable subscription budget. |
| **Native in-product work-management + tracker-agnostic bidirectional bridge** | **UNIQUE** | Others *integrate with* Linear/Jira/GitHub; none **owns** the PM layer *and* bridges to any/none. |
| **Personalized CS/SWE learning library (read-or-listen)** | **UNIQUE** | §7.4 — the combination is unbuilt. |
| **Durable auto-resume across quota resets** | **NEAR-UNIQUE** | Sessions persist elsewhere; resume-on-reset is an OSS watchdog pattern, not commercial. |
| **MoE / capability routing** (cost, quota, track-record) | **PARTIAL** | Cursor/Factory Router = *model* routing by cost only. |
| **Graph-aware decomposition + semantic conflict-avoidance** | **PARTIAL** | Graphs exist for *retrieval* (DeepWiki/AutoWiki); using them to **partition parallel work** is untouched. |
| **Shared RAG/memory across the fleet** | **PARTIAL** | Per-product memory exists; cross-CLI shared layer does not. |
| **Verifier/critic gating best-of-N** | **PARTIAL** | Verifiers common; a *scored best-of-N gate* is thin. |
| **Additive guidelines · agent teams · mobile · basic verification** | **TABLE STAKES** | Necessary to be credible; won't differentiate. |

**Strategic takeaway:** four pillars are genuinely unique — **free-on-own-subscription economics**,
**quota-maximization**, **owned work-management (+ tracker-agnostic bridge)**, and the **learning
library** — plus near-unique durable auto-resume. Verification, mobile, teams, and guidelines are
table stakes now. The convergence risk is **Agent HQ / Warp Oz / ACP** ("orchestrate any agent"), so
our durable moat rests on the **economics + quota-maximization + owned PM + learning**, not on
orchestration alone.
