# Spec 007 — The agent tree: Podium knows every actor

**Status:** Approved for build · **Date:** 2026-07-29 · Operator ruling: *"Podium must
know everything — for a task we can see all the sessions, agents, subagents clearly,"*
and *"even if claude bypasses and spawns gemini it should be visible in Podium."*

## The rule

Nothing works on a task without a node in its tree. Three kinds:

| kind | what it is | source |
|---|---|---|
| `session` | a worker session Podium spawned | dispatcher/manager |
| `subagent` | a native subagent that session spawned (Claude `Task` tool) | hooks |
| `peer` | an attempt to invoke another harness | PATH shims |

## Why the shims are the answer for peers

Bypass mode consults no permission machinery at all (verified: hook denials are
ignored), so *policy* cannot observe a peer call there. The PATH shims sit below every
mode: they **report first, refuse second** — writing `{binary, session, task, command,
outcome}` to `.podium/peer.jsonl`, which the dispatcher tails into the tree. Bypass
therefore cannot hide a peer invocation; it makes it *more* visible, not less.

When the brokered `peer` tool ships (Stage C), the same shim path becomes the broker:
outcome flips from `blocked` to a real child session — the tree shape does not change,
so shipped semantics stay stable (decision 44).

## Surfaces

- `agents.tree {task_id}` wire frame + live `agents.updated` broadcasts.
- `podium tree <task_id>` — indented tree with kind, status, and detail.
- Cockpit **agents pane** (replaces the blocked subagent-tree placeholder).

## Acceptance criteria

- **F1** a dispatched task's session appears as a `session` node; subagent hook events
  create `subagent` children (closed on `SubagentStop`); a shim report creates a `peer`
  child attributed to the spawning session.
- **F2** peer reports carry the command and the session that made them.
- **F3** the tree renders parent-before-child with depth, orphans included.
