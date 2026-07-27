# ADR-0001: Record architecture decisions

**Status:** Accepted · **Date:** 2026-07-27

## Context

Podium's design was researched extensively before this repository existed; the corpus (including
49 numbered design decisions) lives in `research/`. Ongoing decisions need a lightweight,
versioned record in the repo itself.

## Decision

Use Architecture Decision Records (Nygard format) in `docs/adr/`, numbered sequentially. The
`research/DESIGN.md` decision log (1–49) is the imported baseline; new decisions start here as
ADRs. An ADR that alters a research-corpus decision must cite the decision number it supersedes.

## Consequences

Decisions are reviewable in git history; the research corpus stays frozen as source material
rather than being edited in place.
