# Spec 009 — Peer calls are brokered by mode, not blocked

**Status:** Approved for build · **Date:** 2026-07-29 · Operator ruling: *"Podium
should be able to launch everything — why was it not able to launch claude? Subagent
launch should not be blocked, it should just ask for approval or not based on the mode
Podium is working in."*

## What was wrong

The PATH shims refused **unconditionally**, ignoring the autonomy mode entirely — a
hardcoded rule that predates modes existing. It blocked Podium from building its own
quota reader, and in supervised mode it should have asked rather than refused.

Blanket deny was the wrong primitive: spawning a peer harness is a privileged action
like any other, and Podium already has the mechanism for those — the **mode**.

## What it is now

The shim brokers instead of deciding. It files a request with full provenance
(session, task, command, depth) and waits; the daemon answers by the requesting
session's autonomy mode:

| mode | behavior |
|---|---|
| `supervised` | raises the **same approval dialog** as any other decision; the operator's answer decides |
| `autonomous` / `bypass` | auto-approved |

On approval the shim `exec`s the real binary, so its output flows through the worker's
own PTY and its work is attributed to the requesting session. Every request is recorded
in the agent tree either way (`allowed` / `blocked`) — bypass cannot hide one, because
the shim is on `PATH`, not in the permission system.

## The one unconditional rule

A **depth cap** (`PODIUM_PEER_MAX_DEPTH`, default 1): a brokered harness may not spawn
further harnesses. That is a runaway-cost guard, not a permission question, so it holds
in every mode.

## Acceptance criteria

- **H1** autonomous/bypass approve a peer request with no prompt; supervised emits a
  `question` carrying the command, and the operator's answer writes the decision.
- **H2** both outcomes land in the agent tree as `peer` nodes under the requesting
  session.
- **H3** the depth cap refuses beyond the limit and records it, in any mode.
- **H4** a request with no daemon answering times out into a refusal, still recorded.
