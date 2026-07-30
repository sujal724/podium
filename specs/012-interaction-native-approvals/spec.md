# Spec 012 — Interaction layer: native approval callbacks (Stage B)

**Status:** Approved for build (first Stage-B slice) · **Date:** 2026-07-29
**Sources:** `research/V1.md` (Stage B, interaction row), `research/HLD.md` §7,
`research/LLD.md` §2.2, §7, §18, `research/RESEARCH.md` §1.1/§1.3,
`research/CAPABILITIES.md` §2.1, spec 001 (the spine this lands through).

## What

One uniform question/approval experience across drivers, native channels first: a
worker session's own approval callback — Claude Agent SDK **`can_use_tool`** (session
kind `sdk`) and ACP **`session/request_permission`** plus **`session/set_mode`**
(setSessionMode; session kind `acp`, Gemini via `gemini --acp`) — parks on the
daemon's **InteractionLayer** and goes out as a single `approval.request` wire frame.
The answer (TUI keypress, `podium answer`, any client via `answer.native`) resolves
the waiting native callback in its driver's own idiom. Approval mode is adjustable
mid-session through one `session.mode` frame on both kinds.

## Why

HLD §7: even a headless autonomous CLI should behave like a full interactive agent
without reinventing an approval UX per CLI. As of mid-2026 native approval callbacks
exist on all three drivers (CAPABILITIES §2.1) — delegation, not shimming, is the
plan of record; the sentinel/clarifier paths are degraded fallbacks and *later*
increments. This is the first Stage-B surface to come alive, delivered through the
Stage-A loop (dogfooding).

## Scope (in)

1. **InteractionLayer** (`podium/interaction.py`): pending-approval registry;
   `ask()` emits the uniform `approval.request` frame and awaits the answer;
   `answer()` validates against the prompt's option ids and emits
   `approval.resolved`; `cancel_session()` resolves everything pending for an
   ended session as `cancelled` (no dangling prompts — decision 44).
2. **`kind="sdk"`** (`podium/sessions/sdk.py`): persistent `ClaudeSDKClient`;
   `can_use_tool` → uniform prompt → `PermissionResultAllow/Deny`; stream
   normalized to `output` frames; `set_mode` → `set_permission_mode`. The SDK
   package is an optional extra (`pip install 'podium[sdk]'`); requesting the kind
   without it is an honest `WorkerUnavailable` with the install hint.
3. **`kind="acp"`** (`podium/sessions/acp.py`): generic ACP client (JSON-RPC 2.0
   over stdio) — initialize → session/new → prompt turns; `session/request_permission`
   → uniform prompt → `selected`/`cancelled` outcome; `session/update` notifications
   normalized to `output`; `session/set_mode` + `current_mode_update` for modes.
   Wired to Gemini (`gemini --acp`) and to a scripted mock agent for tests/demo.
4. **Wire frames**: out `approval.request` / `approval.resolved`; in `answer.native`,
   `interaction.pending` (snapshot for late-attaching clients), `session.mode`.
   Stage-A `answer` now resolves a matching pending native approval first, PTY
   write otherwise — one uniform answer path.
5. **Surfaces**: `interaction` flips live (was `blocked (B)`); its note records the
   remaining registered increments (sentinel, clarifier).
6. **TUI**: an approvals pane listing pending prompts; `y`/`n` answer the oldest by
   mapping onto the prompt's native options; feed lines for request/resolution.
7. **CLI**: `podium pending`, `podium answer <session> <request> <option>`,
   `podium mode <session> <mode>`.

## Scope (out — registered increments)

Sentinel `<<ASK>>` fallback and clarifier agent (degraded paths, HLD §7); Codex
app-server approval callbacks (`codex_rpc` kind — Codex posture is still
blocked-until-login from Stage A); Brain auto-answering per autonomy gates
(Stage C); dispatching the task loop over `sdk`/`acp` kinds by default (the loop
stays on the PTY substrate; structured kinds are spawned per session).

## Acceptance criteria

- **B1 (uniform layer):** `ask` emits `approval.request` carrying id, session,
  title, detail, options; `answer` resolves the awaited value, validates option
  ids, rejects unknown request ids; `cancel_session` resolves pending prompts as
  `cancelled` and empties the registry. All observable as sink frames.
- **B2 (SDK native):** a `kind="sdk"` session surfaces `can_use_tool` as the
  uniform prompt and maps allow/deny answers to the SDK's PermissionResult shape;
  assistant/result messages land as `output`; requesting the kind without the SDK
  package fails with the install hint (proven with an injected fake client — no
  quota, no network).
- **B3 (ACP native):** against a scripted ACP agent: `session/request_permission`
  becomes the uniform prompt; the chosen option id reaches the agent as the
  `selected` outcome; `session/set_mode` round-trips and `current_mode_update` is
  reflected; session end with a prompt still pending yields the `cancelled`
  outcome, not a hang.
- **B4 (one prompt over the wire):** through the WebSocket gateway: spawn an `acp`
  session → `approval.request` arrives; `answer.native` resolves it (ack +
  `approval.resolved` + the agent's decision echo); `interaction.pending` lists and
  then clears; `session.mode` answers `mode.set`; the `surfaces` frame reports
  `interaction` live; `answer.native` for an unknown id answers `error`, never
  `blocked`.
- **B5 (honest degradation):** unknown session kinds are refused with the worker's
  kind list; `session.mode` on a PTY session answers an error (no silent no-op);
  Stage-A PTY `answer` behavior is unchanged.

## Non-goals of this iteration

Autonomy-gated auto-answers, per-tool allowlists riding the prompt (arrives with
the policy/autonomy work), persistence of pending prompts across daemon restarts
(a restart cancels them via session teardown — auto-resume re-queues the task).
