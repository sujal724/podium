# SDLC Models as Machine-Executable Pipeline Templates

Research for Podium's config-selectable workflow templates (ADR-0003). Each model is described so
it maps to a pipeline template: phases, artifacts, gates, roles, and an agent mapping. Notation:
**[A]** agent-executable · **[H]** human-gated · **[A→H]** agent produces, human approves.
Compiled 2026-07-27 from primary sources cited per model.

## 1. Waterfall
- **Essence:** strictly sequential, document-driven; each phase completes and is verified before
  the next. (Royce 1970 — who warned the pure form "invites failure" and proposed feedback loops.)
- **Phases:** system requirements → software requirements → analysis → program design → coding →
  testing → operations.
- **Artifacts:** SRS, preliminary + detailed design docs, interface docs, code, test plan/report,
  operating instructions.
- **Gates:** phase-exit document review/sign-off; no phase entered until predecessor approved.
- **Roles:** analyst, designer/architect, programmer, tester, PM, customer sign-off.
- **Fits:** fixed, well-understood, regulated/contractual work.
- **Agent mapping:** every phase [A]; every gate [H]. The purest gate-per-phase template.
- **Source:** <https://www.cs.umd.edu/class/spring2003/cmsc838p/Process/waterfall.pdf>

## 2. V-Model
- **Essence:** waterfall bent into a V — every decomposition phase pairs with a verification/
  validation phase, so tests are designed when the spec is written (Forsberg & Mooz 1991).
- **Phases:** concept/requirements → system spec → architecture design → detailed design →
  implementation → unit test → integration test → system test → acceptance test.
- **Artifacts:** each left-side spec + its paired test plan; code; per-level test reports;
  traceability matrix.
- **Gates:** spec review per baseline; validation per level against its paired spec; traceability
  checked at every gate.
- **Roles:** requirements engineer, architect, developer, independent V&V/test, QA, PM.
- **Fits:** safety-critical/regulated systems needing traceability.
- **Agent mapping:** agent writes each spec *and its test plan together* [A]; right-side test
  levels [A]; baselines and acceptance [H]. Traceability is machine-checkable.
- **Source:** <https://incose.onlinelibrary.wiley.com/doi/abs/10.1002/j.2334-5837.1991.tb01484.x>

## 3. Spiral
- **Essence:** Boehm's risk-driven meta-model (1988): repeated cycles, each choosing its approach
  by the biggest current risk; radius = cumulative cost.
- **Phases (per cycle):** objectives/alternatives/constraints → evaluate & resolve risks
  (prototype/simulate) → develop & verify next-level product → plan next cycle.
- **Artifacts:** concept of operations, risk list + resolutions, prototypes, deepening
  requirements/design/code, next-cycle plan.
- **Gates:** end-of-cycle stakeholder review; explicit commitment to proceed per cycle.
- **Roles:** PM/risk owner, stakeholders, developers, prototypers.
- **Fits:** large, novel, high-risk projects.
- **Agent mapping:** risk enumeration + spike prototypes [A]; risk review + commit-to-next-spiral
  [H]. Good template for research-heavy/uncertain work.
- **Source:** <https://www.cse.msu.edu/~cse435/Homework/HW3/boehm.pdf>

## 4. Incremental / Iterative (IID)
- **Essence:** build in small scheduled slices, each adding usable functionality, rework expected
  between slices; predates Agile (Larman & Basili).
- **Phases:** requirements outline → increment planning → per increment: design → build → test →
  integrate/deliver → feedback into next increment.
- **Artifacts:** high-level requirements list, increment plan/roadmap, per-increment design notes,
  working increment, regression results, feedback log.
- **Gates:** increment-completion demo of working software; re-plan point per increment.
- **Roles:** product owner/customer rep, developers, integrator/tester, planner.
- **Fits:** products where value ships in slices and requirements firm up with use.
- **Agent mapping:** slice backlog [A→H ordering]; build/test/integrate [A]; demo-and-replan [H].
  Generic base most agile variants specialize.
- **Source:** <https://www.craiglarman.com/wiki/downloads/misc/history-of-iterative-larman-and-basili-ieee-computer.pdf>

## 5. RUP
- **Essence:** IBM Rational's iterative, use-case-driven, architecture-centric framework; four
  phases each ending in a named milestone; nine disciplines across all phases.
- **Phases:** inception → elaboration → construction → transition (iterations inside each).
- **Artifacts:** vision doc, business case, use-case model, risk list; executable architecture
  prototype + SAD; working builds, test results, user docs; release + deployment material.
- **Gates (milestones):** Lifecycle Objectives → Lifecycle Architecture → Initial Operational
  Capability → Product Release.
- **Roles:** analyst, architect, developer, tester, PM, configuration manager, process engineer.
- **Fits:** large architecture-heavy projects, ceremony-tolerant orgs.
- **Agent mapping:** vision/use-cases [A→H]; architecture prototype [A] with the LCA gate [H]
  ("is the architecture proven?"); construction iterations [A]; release [H].
- **Source:** <https://public.dhe.ibm.com/software/rational/web/whitepapers/2003/rup_bestpractices.pdf>

## 6. Agile / Scrum
- **Essence:** small cross-functional team delivers a usable Increment every Sprint (≤1 month),
  inspecting/adapting via defined events (2020 Scrum Guide; Agile Manifesto values).
- **Cycle:** backlog refinement → sprint planning → daily development + daily scrum → sprint
  review → retrospective → next sprint.
- **Artifacts (commitments):** Product Backlog (Product Goal), Sprint Backlog (Sprint Goal),
  Increment (Definition of Done).
- **Gates/ceremonies:** planning ≤8h, daily 15min, review ≤4h, retro ≤3h; DoD is the quality gate.
- **Roles:** Product Owner, Scrum Master, Developers.
- **Fits:** evolving product with an engaged owner.
- **Agent mapping:** backlog drafting [A→H prioritization]; sprint execution [A]; DoD checks [A,
  automated]; review [H]; retro = agent self-eval + config tuning. Timeboxes = scheduling quanta.
- **Sources:** <https://scrumguides.org/scrum-guide.html> · <https://agilemanifesto.org/principles.html>

## 7. Kanban
- **Essence:** evolutionary flow management: visualize current process, limit WIP, optimize flow;
  no timeboxes or prescribed phases.
- **"Phases":** user-defined board columns, e.g. backlog/options → ready → in-progress
  (analyze/build/test) → review → done.
- **Artifacts:** board, work items, explicit policies (pull criteria, per-column DoD), flow
  metrics (lead time, throughput, CFD).
- **Gates:** per-column pull policies + WIP limits; cadences (replenishment, daily Kanban,
  delivery/ops/strategy reviews).
- **Roles:** minimal (optional Service Delivery / Request managers).
- **Fits:** continuous intake — maintenance, ops, solo streams.
- **Agent mapping:** best solo-orchestrator default — columns = pipeline stages, WIP limits =
  agent concurrency caps, policies = machine-checkable exit criteria [A], replenishment [H]. Flow
  metrics come free from telemetry.
- **Source:** <https://kanban.university/kanban-guide/>

## 8. XP (Extreme Programming)
- **Essence:** engineering-practice-centric agile (Beck): short releases with continuous testing,
  pairing, TDD, refactoring, on-site customer.
- **Cycle:** release planning → iteration planning (1–3wk) → test-first development → continuous
  integration → small release; spikes for unknowns.
- **Artifacts:** user stories with customer acceptance tests, release/iteration plans, unit-test
  suite, CI-integrated codebase, spike solutions.
- **Gates:** all unit tests pass before integration; acceptance tests define "done"; coding
  standard + collective ownership as continuous checks.
- **Roles:** Customer, Programmer (pairs), Tester, Tracker, Coach.
- **Fits:** small teams, volatile requirements, high quality bar.
- **Agent mapping:** XP is the *agent execution discipline* — test-first, CI, refactoring as
  automated gates [A]; story writing/priorities and acceptance approval [H]. Pairing = agent +
  reviewer-agent.
- **Source:** <http://www.extremeprogramming.org/rules.html>

## 9. Lean Software Development
- **Essence:** Poppendiecks' translation of Toyota lean: maximize value, minimize waste;
  principles + tools, not a phased lifecycle.
- **Principles (as pipeline constraints):** eliminate waste · amplify learning · decide late ·
  deliver fast · empower the team · build quality in · optimize the whole.
- **Artifacts:** value-stream map, minimal marketable features, waste log (7 wastes), pull queues.
- **Gates:** none formal; flow/queue metrics trigger intervention.
- **Fits:** overlay philosophy for any method.
- **Agent mapping:** implement as *policies*, not a template — "no partially-done work" → kill
  stale branches; "build quality in" → mandatory CI gates; "eliminate waste" → don't generate
  artifacts nothing consumes. Pairs naturally with Kanban.
- **Source:** Poppendieck & Poppendieck, *Lean Software Development* (2003);
  <https://www.netsolutions.com/insights/7-principles-of-lean-software-development/>

## 10. SAFe (brief)
- **Essence:** scales agile via an Agile Release Train (50–125 people) planning on a fixed
  Planning Interval cadence (8–12 weeks).
- **Cycle:** PI planning → 4–5 two-week iterations → iteration/system demos → IP iteration →
  inspect & adapt.
- **Gates:** PI planning with confidence vote; ART syncs; system demos; portfolio Kanban for epics.
- **Fits:** multi-team enterprises — **not solo work**; interesting only as a multi-agent-fleet
  pattern (PI planning = batch dependency resolution across agent teams).
- **Source:** <https://framework.scaledagile.com/>

## 11. Shape Up (Basecamp)
- **Essence:** fixed-time, variable-scope: shape rough solutions, bet a 6-week cycle on a pitch,
  team builds autonomously; unfinished work gets no default extension (circuit breaker).
- **Phases:** shaping → pitch → betting table → 6-week build (hill charts, scope hammering) →
  ship → 2-week cool-down.
- **Artifacts:** pitch (problem, appetite, solution, rabbit holes, no-gos), bet list, hill
  charts, scope map.
- **Gates:** the betting table (single big gate); appetite as hard budget; circuit breaker at
  cycle end.
- **Roles:** shapers, betting-table stakeholders, autonomous build team.
- **Fits:** self-contained feature work by senior/autonomous builders.
- **Agent mapping:** pitch [A→H]; bet = the single [H] commitment; build cycle [A, autonomous
  with fixed budget]; hill chart = agent progress self-report; **circuit breaker = hard budget
  kill-switch; appetite maps directly to quota budgeting.**
- **Source:** <https://basecamp.com/shapeup>

## 12. DevOps / CI-CD lifecycle
- **Essence:** continuous loop unifying dev and ops; DORA ties throughput/stability to specific
  capabilities.
- **Stages:** plan → code → build → test (CI) → release → deploy (CD) → operate → monitor →
  feedback to plan.
- **Artifacts:** version-controlled everything, build artifacts/containers, test reports, release
  manifests, dashboards/alerts, postmortems.
- **Gates:** automated — green build/tests, security/quality scans, staged deploys
  (canary/blue-green) with rollback; optional manual production approval.
- **Agent mapping:** stages 2–8 fully [A]; production approval the typical single [H]. For Podium
  this is the *always-on backbone* under every template, not a selectable template.
- **Source:** <https://dora.dev/>

## 13. TDD / BDD (disciplines, not lifecycles)
- **TDD (Beck):** red → green → refactor; test must fail before implementation; all green before
  commit.
- **BDD (North):** behavior specs in Given-When-Then business language; scenario pass = feature
  done; "three amigos" co-authoring.
- **Agent mapping:** ideal guardrails — "tests written and failing before implementation commit"
  is mechanically checkable [A]; BDD scenarios are the [H]-approved contract the agent satisfies.
  Configured as a per-template flag (`discipline: tdd|bdd|none`).
- **Sources:** <https://martinfowler.com/bliki/TestDrivenDevelopment.html> ·
  <https://dannorth.net/introducing-bdd/>

## 14. Spec-Driven Development (AI-era, 2025–2026)
- **Essence:** the versioned spec is the primary artifact; agents mechanically derive plan, tasks,
  code. Canonicalized by GitHub Spec Kit: constitution → specify → clarify → plan → tasks →
  implement → verify/analyze.
- **Artifacts:** constitution; spec (user stories, acceptance criteria, edge cases); plan (+ data
  model, contracts); ordered task list with `[P]` parallel markers; code + tests.
- **Gates:** constitution-derived pre-implementation gates; spec-completeness checklist; human
  approval between artifacts; drift detection post-implement.
- **Agent mapping:** 1:1 by construction — every phase [A], every boundary [A→H]; `[P]` tasks fan
  out to parallel agents; gates are machine-evaluated checklists. **The native template shape for
  an AI orchestrator.** (Podium adaptation per ADR-0003: task breakdown goes to the
  work-management layer, not a `tasks.md` file.)
- **Sources:** <https://github.com/github/spec-kit/blob/main/spec-driven.md> ·
  <https://github.blog/ai-and-ml/generative-ai/spec-driven-development-with-ai-get-started-with-a-new-open-source-toolkit/>

## 15. ISO/IEC/IEEE 12207:2017 (standards umbrella)
- **Essence:** the international standard defining vocabulary + process framework for the software
  life cycle: 30 processes in 4 groups (Agreement, Organizational Project-Enabling, Technical
  Management, Technical), each with purposes/outcomes/activities — not a sequence.
- **Gates:** none prescribed; organizations define stages + decision gates; QA/V&V processes
  supply checking machinery.
- **Agent mapping:** Podium's *schema layer* — every template phase declares which 12207
  process(es) it realizes → cross-template comparability, audit vocabulary. Not itself runnable.
- **Sources:** <https://www.iso.org/standard/63712.html> ·
  <https://quality.arc42.org/standards/iso12207>

## Comparison

| Model | Phases | Gate style | Best fit |
|---|---|---|---|
| Waterfall | 7 sequential | Document sign-off per phase (all human) | Fixed requirements, contractual |
| V-Model | 9 paired | Paired V&V + traceability | Safety-critical, regulated |
| Spiral | 4 quadrants × N | Risk review + commitment per cycle | Novel, high-risk |
| Incremental | 2 + 5/increment | Working-increment demo/replan | Sliceable products |
| RUP | 4 (N iterations) | 4 named milestones | Large, architecture-heavy |
| Scrum | 5-event cycle | Timeboxed ceremonies + DoD | Evolving product, engaged PO |
| Kanban | user-defined columns | Pull policies + WIP limits | Continuous intake, solo flow |
| XP | 5-step cycle | Automated tests + acceptance | Small teams, volatile reqs |
| Lean | 7 principles | Flow metrics + policies | Overlay on any method |
| SAFe | 5-stage PI | PI planning + demos | Multi-team enterprise only |
| Shape Up | 6 | Single betting gate + circuit breaker | Autonomous feature work |
| DevOps/CI-CD | 8-stage loop | Fully automated pipeline gates | Always-on backbone |
| TDD/BDD | 3–4 inner loop | Failing-test-first | Discipline flag |
| SDD | 7 | Machine checklists + approve-per-artifact | AI orchestration (native) |
| ISO 12207 | 30 processes | Org-defined | Schema/vocabulary |

## Recommendation (adopted in ADR-0003)

Implement first: **SDD** (native agent-pipeline shape), **Kanban** (default operating mode; WIP =
concurrency/quota caps), **Shape Up** (appetite = quota budget; circuit breaker = kill-switch),
**V-Model lightweight** (high-assurance). TDD/BDD as an orthogonal discipline flag; DevOps/CI-CD
as the always-on backbone; ISO 12207 process IDs as the tagging vocabulary. Defer Waterfall/RUP
(subsumed by V-Model + SDD gates), Spiral (fold risk-spikes into SDD clarify), Scrum/SAFe (little
value for one operator), Lean (encode as policies).
