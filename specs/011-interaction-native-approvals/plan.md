# Plan 011 — Interaction layer: native approval callbacks

## Approach

The layer is a small pending-request registry between two worlds:

```
native callback (SDK can_use_tool | ACP session/request_permission)
    → InteractionLayer.ask(session_id, title, detail, options)      [awaits]
    → sink: approval.request {request:{id, session_id, kind, title,
                                       detail, options:[{id,label,kind}], meta}}
    → any client: answer.native {session_id, request_id, value}
    → InteractionLayer.answer → future resolves → sink: approval.resolved
    → adapter maps the option id back into its driver's idiom
        sdk: PermissionResultAllow() / PermissionResultDeny(message=…)
        acp: {"outcome":{"outcome":"selected","optionId":…}} (or cancelled)
```

Options carried in the prompt are the driver's own (ACP sends its optionId/name/kind
set verbatim; the SDK gets a synthesized allow/deny pair tagged with ACP-style kinds)
so clients can render one shape and map generic allow/deny onto whatever the driver
offered. `cancel_session` resolves pending prompts with a `CANCELLED` sentinel that
each adapter translates (SDK → deny, ACP → cancelled outcome); the daemon can never
hold a prompt for a dead session (decision 44 — no fake surfaces).

## Contracts touched

- `Worker.make_session` gains `interaction=None`; new `Worker.check_kind` refuses
  unregistered kinds instead of silently falling back to PTY.
- `Session.set_mode(mode)` joins `resize` as an optional capability; the base
  raises `NotImplementedError` (honest error over the wire for PTY kinds).
- New wire frames recorded here per the spec-001 convention: server→client
  `approval.request`, `approval.resolved`, `approval.pending`, `answer.ack`,
  `mode.set`; client→server `answer.native`, `interaction.pending`, `session.mode`.
  Stage-A `answer` is now native-first (matching pending request id) with the PTY
  write as fallback — LLD §18's "one uniform answer path".

## Deviations from LLD (recorded)

- LLD §2.2 names the kind `gemini_acp` / class `GeminiAcpSession`. It lands as a
  generic **`AcpSession` / `kind="acp"`** because the Agent Client Protocol is
  agent-agnostic — the same class drives Gemini (`gemini --acp`) and the scripted
  mock agent, and can drive any future ACP speaker.
- LLD §7 sketches `InteractionLayer.on_output` (sentinel scan + block detect).
  Those are the degraded fallbacks, explicitly out of this slice; the class ships
  with the native path only and the surface note records the increments.

## Testing (B-criteria, no quota, no network)

- Layer semantics: pure asyncio against a bare `Sink`.
- SDK: injected fake client factory (the session exposes the factory seam; the
  default factory is the only place `claude_agent_sdk` is imported).
- ACP: `podium/workers/mock_acp_agent.py` — a scripted stdin/stdout ACP agent
  registered under the mock worker's `acp` kind; `[[ask]]` triggers a permission
  request and echoes the decision, `session/set_mode` round-trips. The same agent
  proves the wire path end-to-end through a real gateway (spawn → approval.request
  → answer.native → decision echo).

## Risks

- SDK API drift (`claude-agent-sdk` is young): confined to the default factory +
  `_permission_result`; optional extra keeps the core dependency-free.
- ACP version skew (protocolVersion negotiation): we send v1 and read whatever the
  agent returns; Gemini flag drift (`--acp`) is a worker-adapter one-liner.
- `can_use_tool` requires the SDK's streaming (persistent-client) mode — already
  the chosen mode (`ClaudeSDKClient.connect()`), per RESEARCH §1.1.
