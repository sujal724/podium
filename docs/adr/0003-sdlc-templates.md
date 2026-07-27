# ADR-0003: SDLC — repo process and product template set

**Status:** Accepted · **Date:** 2026-07-27

## Context

Podium executes work through workflow templates, and standardized SDLC models should be selectable
in config rather than one hardcoded process. `research/sdlc-models.md` maps 15 standardized models
(Waterfall … Spec-Driven Development, ISO/IEC/IEEE 12207) to agent-executable pipelines and
recommends an initial set. This repo also needs its own process.

## Decision

1. **This repository** follows spec-driven development (`docs/SDLC.md`), with the strict split:
   documents (spec/plan) in git, tasks and their state in the task tracker only.
2. **Podium (the product)** exposes SDLC models as config-selectable workflow templates.
   Initial template set, per the research recommendation:
   - **Spec-Driven Development** — default for feature work (native shape for agent pipelines);
   - **Kanban** — default operating mode for continuous flow (WIP limits map to agent
     concurrency and quota caps);
   - **Shape Up** — fixed-appetite autonomous cycles (appetite maps to quota budgets, circuit
     breaker maps to hard budget kill-switch);
   - **V-Model (lightweight)** — high-assurance work (paired spec + test-plan, traceability).
3. **TDD/BDD** are orthogonal discipline flags (`discipline: tdd|bdd|none`) usable inside any
   template; **CI/CD** is the always-on execution backbone, not a template; each template phase is
   tagged with the **ISO/IEC/IEEE 12207** process it realizes, giving cross-template vocabulary.
4. In every template, phase *documents* are artifacts in the repo while task breakdown and status
   are work-management state (native store / bridged TMS) — no template stores task state in files.
5. Deferred (implementable later as additional templates): Waterfall, RUP, Spiral, Scrum, SAFe,
   Lean-as-policies.

## Consequences

Template registry and config schema must model: phases, artifacts, gates (machine-checkable vs
human), roles, discipline flags, and 12207 tags. Adding a model later is additive (research
decision 44) — new template, no changes to existing ones.
