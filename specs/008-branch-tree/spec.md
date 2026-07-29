# Spec 008 — The branch tree: worktrees stack on any base

**Status:** Approved for build · **Date:** 2026-07-29 · Operator ruling: *"every
worktree starts from a base branch which need not be main — it's a tree; base branch A
can have children B and C, and B has subtasks too."*

## Base resolution (first match wins)

1. the task's explicit **`base_ref`** (any branch: `release/2.0`, a colleague's branch…)
2. its **parent task's branch** (`task/<parent>`), created on demand if the parent has
   not run yet — so a child can start before its parent does
3. the **project's base branch**

So `main → task/A → task/B → task/B1` stacks arbitrarily deep, and siblings B and C
branch off A independently.

A subtask's **diff is against its own base**, so review shows only its own work, not
its parent's. Approve merges it back into that base — a subtask lands in its parent's
branch, and only the top-level task ever touches the project base.

## The two ordering questions (operator, this conversation)

**Do subtasks have to finish before the parent merges? — Yes, and it is enforced.**
Approving deletes the merged branch, and children are stacked on it, so landing a
parent with live children would orphan their base. `approve` refuses with the list of
unfinished children (`done`/`discarded` clears them). This also matches the work model:
a parent *is* its decomposition. **G1**

**Does a subtask's base change when something lands in it? — Yes, and it is announced.**
When sibling B merges into `task/A`, sibling C's base has advanced. C's diff stays
correct (three-dot against the base ref), but C is working on an older commit, so
Podium emits a `base advanced` note on every affected sibling. Picking the change up is
a re-run or a merge; automatic rebasing of live stacks is **not** done — it would
rewrite a running session's history. Registered as a later increment.

## Merging without a checkout dance

`approve` now runs the merge in whichever worktree already has the base ref checked out,
or borrows a scratch worktree when none does — git forbids the same branch in two
worktrees, and with stacked branches the base is usually checked out in the parent's
worktree. Approving no longer depends on where the operator's own checkout sits
(supersedes the earlier "refuse unless on base branch" rule).

## Acceptance criteria

- **G1** a parent in `review` with an unfinished child refuses to merge, naming it;
  once children are `done`/`discarded` it merges.
- **G2** base resolution: explicit `base_ref` > parent branch > project base, at any
  depth; the parent branch is created on demand.
- **G3** siblings branch off the parent independently; a child's diff excludes the
  parent's work; approving a child lands it in the parent branch, leaving the project
  base untouched.
- **G4** approve works regardless of the operator checkout's current branch.
