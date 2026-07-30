# Spec 002 — Self-update + first-dogfood fixes

**Status:** Approved for build · **Date:** 2026-07-29
**Origin:** first real dogfood run (operator request). Two parts: the bugs that blocked
the first dispatch, and the self-update loop the operator asked for.

## Part 1 — fixes from the first dispatch

**Bug: dispatched Claude sessions freeze at the trust dialog.** Every task worktree is a
path Claude Code has never seen, so each session stops at "Do you trust this folder?"
and the loop never starts (observed: 4 sessions stuck, tasks parked in `running`; trust
is per-path in Claude's config with no inheritance — verified empirically).

**Fix — surface the dialog, don't swallow it (operator ruling, this conversation):**
Podium has power over the workers, not the other way around — but the *decision* is the
operator's, so Podium must **show** it, never answer it itself or pre-write trust into a
worker's config. This is the Stage-A slice of the interaction layer (LLD §7, pulled
forward): known worker prompts (trust dialog, login screen) are detected in the PTY
stream (ANSI-robust pattern match), raised as a uniform `question` frame, rendered as a
real dialog box in the cockpit (and answerable via `podium answer`), and the operator's
choice is translated to the exact keystrokes the worker's own dialog expects. One
question per prompt per session; unknown prompts still reachable via takeover. Native
approval callbacks (SDK/ACP) remain the Stage B surface.

**Gap: worker posture invisible in the cockpit.** The daemon reports auth/ToS posture in
`hello`, but no pane rendered it — an unavailable worker was indistinguishable from a
broken one. Fix: posture line in the cockpit (✓ / ✗ + hint per worker) and a
`podium workers` CLI command.

## Part 2 — self-update (operator-approved, work-preserving)

When a new version lands on `origin/main` (a release per SDLC — merges to main), the
running daemon should *know*, *tell*, and — only on operator approval — *update itself
and keep things running*.

Mechanism (editable install; the repo is the deployment):
1. **Watch:** the daemon periodically (`PODIUM_UPDATE_CHECK_S`, default hourly; 0
   disables) fetches `origin/main` of its own repo and compares to the running HEAD.
2. **Tell:** if behind, emit `update.available {installed, remote_version, behind}` —
   feed line + banner in the cockpit, `podium update` in the CLI. Never auto-applies.
3. **Approve & apply** (`update.apply`): stop live sessions gracefully (their tasks stay
   `running` in the DB), fast-forward the repo to `origin/main`, reinstall into the
   daemon's own venv (dependency changes included), then re-exec the daemon process.
4. **Keep things running:** on boot the existing auto-resume seed (decision 23) re-queues
   the interrupted tasks; clients (TUI/CLI) reconnect to the same address.

If Podium wasn't installed from a git checkout, the updater reports `unavailable` with
the reason — never a silent no-op (decision 44).

Stage note: the full Updater/Watcher (CLI capability probing, Self-Diagnostic) remains a
Stage C surface (`selfmaint`, still `blocked`). This spec pulls forward only the
*self-version* watch/apply slice, at the operator's explicit request — recorded here so
the stage table stays honest.

## Acceptance criteria

- **B1:** a session emitting a known worker prompt (trust dialog rendered with
  interleaved ANSI escapes) produces exactly one `question` frame; answering it over
  the wire delivers the dialog's expected keystrokes to the PTY and the session
  proceeds (end-to-end with the mock CLI); Podium never writes into a worker's own
  config.
- **B2:** cockpit shows per-worker posture from `hello`; `podium workers` prints it.
- **B3:** with a remote ahead, `update.check` reports `behind > 0` + the remote version,
  and emits `update.available`; up-to-date reports `behind == 0` and emits nothing.
- **B4:** `update.apply` fast-forwards the repo, runs the install step, and invokes the
  restart step (steps injectable in tests); refuses when already up to date.
- **B5:** a non-git install reports `available: false` with a reason over the wire.
- **B6:** running sessions are stopped before restart; their tasks re-queue via
  auto-resume on next boot (existing A6 path).
