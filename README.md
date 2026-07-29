# Podium

Orchestrator for AI coding agents — drives Claude Code, Gemini CLI, and Codex through their
official CLIs, with native work management, full live visibility, quota-aware scheduling, and
verification. An intelligence layer makes the fleet smarter than the agents alone: a codebase
graph and deep-RAG context engine, cross-run memory and self-maintaining guidelines, an
independent verifier, and capability-aware model routing that learns from outcomes. A learning
layer teaches the operator alongside the work: a searchable library of project docs and curated
reading material (read or listen), personalized to what you're building.

## Layout

- `research/` — design docs and research corpus (vision, architecture, capability analyses, SDLC research)
- `docs/` — development process (`SDLC.md`) and architecture decision records (`adr/`)
- `specs/` — feature specs and plans (spec-driven development; tasks live in the issue tracker, not here)
- `podium/` — the implementation (`podiumd` daemon + `podium` CLI/TUI)
- `tests/` — test suite (acceptance criteria are test-encoded per spec)

## Quick start

```sh
pip install -e ".[dev]"   # Python 3.12+
podiumd                    # the daemon (localhost WebSocket, SQLite state)
podium tui                 # the cockpit
podium --help              # work management + review from the shell
pytest                     # the whole Stage-A acceptance suite
```

## Status

Stage A of V1 ("the loop lives" — `research/V1.md`): task loop with per-task git
worktrees, live PTY sessions with takeover, full work model (hierarchy + dependency DAG +
approval inbox), review gate, metering ledger with an honest quota gauge, and every
later-stage surface present as an explicit `blocked` pane (research decision 44).
