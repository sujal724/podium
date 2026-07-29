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

## The full hierarchy (operator question, 2026-07-29)

```
workspace                     organizational scope
└─ project                    a repo (repo_root + base_branch)
   └─ task                    the unit of work; owns ONE git worktree + task/<id> branch
      └─ session              a worker CLI Podium spawned in that worktree (◆)
         ├─ subagent          spawned natively by that session, e.g. Claude's Task
         │                    tool — including Claude-spawning-Claude (└─◇)
         └─ peer              an attempt to invoke another harness (⚠)
```

Isolation is **per task**: one worktree, one branch, one reviewable diff. Sessions are
*within* a task and are not 1:1 with it — a rejected or interrupted task is re-run as a
new session over the same branch (resumed in place when it was interrupted, spec 005).
Subagents and peers live inside a session and inherit its task and worktree.

`podium tree` renders the whole hierarchy; `podium tree <task_id>` renders one task's
actors. **F4** covers the scope view.

### Correlating parallel subagents

A session can run several subagents **at once**, so closing "the latest running one"
pairs them wrongly. Hook payloads carry `tool_use_id` (verified against the real CLI),
so open/close are correlated by that id, with the latest-running close kept only as a
fallback for events that carry no id. **F5**.
