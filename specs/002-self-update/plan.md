# Plan 002 — Self-update + first-dogfood fixes

**Status:** Approved for build · **Date:** 2026-07-29 · Companion to `spec.md`.

## Changes

- `podium/interaction.py` — `InteractionLayer` (Stage-A PTY slice of LLD §7):
  `canonicalize` (ANSI-strip + whitespace-collapse) over a per-session tail buffer;
  `PromptPattern` registry (claude trust dialog, claude login screen); `scan()` emits
  one uniform `question` frame per prompt per session; `answer_bytes()` maps the
  operator's choice (index or label) to the keystrokes the worker dialog expects.
  Wired as a daemon sink tap; `answer` frames route through it. Podium never writes a
  worker's config — the decision is shown, the operator decides.
- `podium/update.py` — `SelfUpdater(sink, repo?, install_cmd?, restart_fn?)`:
  - repo autodetected as the package's parent checkout (`podium/../.git` present);
    otherwise `available: false` + reason. `PODIUM_SELF_REPO` overrides.
  - `status()` (no network), `check()` (fetch + compare + maybe emit
    `update.available`), `apply()` (ff-merge → install → restart hooks).
  - remote version parsed from `git show origin/main:pyproject.toml`.
  - default install step: `[sys.executable, -m, pip, install, -e, repo]` (the daemon's
    own venv); default restart: stop sessions → close WS server → `os.execv` of
    `python -m podium.gateway` (sockets are non-inheritable, so the port frees).
- `podium/gateway.py` — frames `update.status` / `update.check` / `update.apply`;
  periodic check task when `PODIUM_UPDATE_CHECK_S > 0` and the updater is available.
- `podium/surfaces.py` — `update` surface, live, with the stage-note; `selfmaint` stays
  blocked.
- `podium/config.py` — `PODIUM_SELF_REPO`, `PODIUM_UPDATE_CHECK_S` (default 3600).
- `podium/tui/app.py` — `QuestionDialog` modal (real dialog box with buttons) for
  `question` and `update.available` frames; posture panel on `hello`.
- `podium/cli.py` — `podium workers`, `podium update [--apply]`, `podium answer`.
- Version bump → 0.2.0 (this PR is the first thing the updater will announce).

## Risks

- **Prompt-text drift** — the trust/login patterns match observed CLI output, not an
  API. Mitigation: ANSI-robust canonical matching, one pattern id per session, and the
  dialog is always still visible in the raw session pane + answerable via takeover if
  detection misses. Revisit when the capability registry (Stage C) probes versions.
- **Re-exec while sessions run** — sessions are stopped first; tasks re-queue via the
  A6 auto-resume path. A mid-apply crash leaves the repo updated but the process old —
  the next boot runs the new code.

## Verify

`pytest` — B1–B5 test-encoded (`tests/test_interaction.py`, `tests/test_update.py`,
gateway wire tests); B1/B6 additionally verified manually against the real CLI.
