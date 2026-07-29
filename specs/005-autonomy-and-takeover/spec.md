# Spec 005 — Autonomy modes, resume-in-place, one-key takeover

**Status:** Approved for build · **Date:** 2026-07-29 · From dogfooding v0.3.3.

## Why

Three operator findings in one run:
1. **"Why is it asking me at all?"** Sessions launched with `--permission-mode
   acceptEdits`: edits auto-accepted, *commands* still prompted. A half-autonomy
   default that was never surfaced — the mode has to be a choice, and visible.
2. **"My input doesn't work."** Keyboard focus sat on the board tree, so keys typed
   at the cockpit reached nothing; the takeover input had to be clicked first.
3. **"Resuming must continue, not restart."** After an update-interrupt the task was
   re-dispatched from scratch, discarding the worker's context and completed work.

## What

- **Autonomy modes** (Stage-A slice of decision 7's spectrum), per task -> project ->
  daemon default: `supervised` (edits auto, commands ask — Podium surfaces the
  dialog) and `autonomous` (tools auto-accepted). The **peer-call deny (decision 50)
  applies in both** — deny beats allow, so autonomy never widens into other harnesses.
- **Mode always visible**: session status bar in the cockpit (`SUPERVISED · asks you
  before running commands` / `AUTONOMOUS · runs tools without asking`, plus `resumed`),
  a feed line per dispatch, and `podium sessions`.
- **Approval prompts as real dialogs**: the interaction layer reads the worker's *own*
  numbered options off the screen and offers exactly those; answering writes that
  option's number. Re-arms per prompt (each approval is its own decision).
- **Keyboard**: takeover input focused on mount; control actions moved to ctrl-keys so
  typing never triggers them; `esc` forwards to the session (interrupt).
- **Resume in place**: a task interrupted (update/restart/crash) re-dispatches with
  `claude --resume <session-id>`, continuing the same conversation. A *rejected* task
  starts fresh — feedback should reshape the work, not extend a wrong path.
- **One-key takeover**: `ctrl+o` suspends the cockpit and hands the terminal to the
  real Claude UI for that session (its worktree, its conversation); exiting returns to
  the cockpit with the session still running.

## Acceptance criteria

- **E1** autonomous settings carry the allow-list *and* keep the peer deny; supervised
  carries no allow-list.
- **E2** cockpit focuses the takeover input on mount; mode bar shows mode + resumed.
- **E3** a permission prompt yields a question whose choices are the worker's own; the
  answer maps to that option's number; it re-arms for the next prompt.
- **E4** an interrupted task re-dispatches with the resume key; a rejected one doesn't.
