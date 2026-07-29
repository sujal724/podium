# ADR-0004: Commits are authored by the operator's identity only

**Status:** Accepted · **Date:** 2026-07-27

## Context

Agents and the orchestrator produce commits in this repo and in every repo Podium manages. The
operator requires all commits to carry their own name, with no AI/orchestrator attribution.

## Decision

All commits — here and in managed repos — are authored as the operator (`user.name`/`user.email`
from the operator's git config), with **no AI co-author trailers**. Podium enforces this: worker
sessions get the operator's git identity injected, and the pre-merge gate rejects commits with any
other authorship. Internally, Podium still tracks which agent produced what (research decision 39,
authorship provenance) — provenance is orchestrator state, not git metadata.

**Extension (operator ruling, 2026-07-29): all public artifacts, not just commits.** The same rule
covers PR titles and bodies, issues, review comments, changelogs, and release notes — no
"Generated with …" badges, AI signatures, or agent attribution of any kind, here or in any managed
repo. Podium enforces this the same way: its PR/issue templates omit attribution, and the
pre-merge/publish gate rejects artifacts carrying it.

## Consequences

Uniform git history under one identity; agent attribution stays available in Podium's own records.
Records research decision 48.
