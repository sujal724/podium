# Development Process

How Podium itself is built. (The SDLC models Podium *offers as product templates* are an ADR —
see `adr/0003-sdlc-templates.md`; this document is about this repository.)

## Method

**Spec-driven development, with a strict document/task split:**

- **Documents live in git.** Each non-trivial feature gets `specs/NNN-<feature>/` containing
  `spec.md` (what & why, acceptance criteria) and `plan.md` (technical approach, contracts,
  risks). These are design artifacts — versioned, reviewed, stable.
- **Tasks live in the task tracker, only there.** When a plan is approved, its task breakdown is
  created as tracker issues (linked to the spec). Status, ordering, dependencies, and assignment
  are tracker state — never duplicated into markdown files that can drift. The tracker is GitHub
  Issues today; it becomes Podium's own work management as soon as that exists (dogfooding), with
  the TMS bridge keeping any external tracker in sync.

Trivial changes (typos, doc fixes) skip the pipeline and commit directly.

## Gates

| Gate | Check |
|---|---|
| spec → plan | Acceptance criteria complete; no unresolved open questions |
| plan → tasks | Plan covers every acceptance criterion; risks listed; approved plan is broken into tracker issues, each with a stated verify command |
| task → done | Its verify command passes; atomic commit references the issue |
| feature → done | All issues closed; acceptance criteria demonstrated; diff reviewed and approved |

## Disciplines & conventions

- **Tests-first** where behavior is specifiable (TDD); otherwise a verification command per task.
- **Commits:** Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, …); authored under the
  operator's git identity only — no AI co-author trailers (ADR-0004). One logical change per
  commit, referencing its issue.
- **Branches:** `main` is always releasable; feature work on `spec/NNN-<feature>` branches;
  worktrees for parallel work.
- **Decisions:** anything that changes architecture, process, or shipped semantics gets an ADR
  (`docs/adr/`). Shipped semantics never change silently (research decision 44).
- **Docs:** updated in the same change that alters behavior — never after.

## CI/CD backbone

Every push runs build + tests + lint (configured with the first code). Releases are tagged from
`main`.
