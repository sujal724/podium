# Spec 010 — Real terminal panes (no preview, no emulation)

**Status:** Approved for build (next task) · **Date:** 2026-07-29
**Operator ruling:** *"Actually show no preview — open a new subterminal which is just
rendered there. One page with two terminals."*

Supersedes the emulated session pane (spec 003) as the primary view. The emulator
stays only as a headless reader for metering/scanning, not as something the operator
looks at.

## Shape

One window, two **real** terminals, side by side — rendered by the operator's own
terminal emulator, not by Podium:

```
┌───────────────────────┬────────────────────────────────────────┐
│ podium tui            │ the worker's ACTUAL terminal           │
│ board · agents · feed │ claude, rendered natively, full        │
│ review · quota        │ fidelity, real keyboard input          │
└───────────────────────┴────────────────────────────────────────┘
```

Mechanism: a terminal multiplexer (tmux — candidate, per decision 19 the eval harness
may compare alternatives). `podium term` creates a session with the cockpit in the left
pane and the selected worker in the right pane. Switching tasks retargets the right
pane; there is never an emulated redraw of a worker's UI.

## How Podium keeps its visibility

The daemon must still see everything it sees today (board, metering, rate-limit
scanning, interaction prompts, agent tree), so the worker's output is captured
*alongside* the display rather than instead of it:

- workers run inside the multiplexer; `pipe-pane` streams a copy of the pane's bytes to
  the transcript the daemon already tails
- takeover from the cockpit sends input via `send-keys`; the operator can equally type
  directly into the pane — same PTY, both work
- hooks, peer brokering, metering and the agent tree are unchanged: they read the
  transcript and the `.podium/` files, not the display path

## Acceptance criteria

- **I1** `podium term` opens one window with the cockpit and a live worker terminal
  side by side; the worker pane is the real CLI (its own rendering, its own input).
- **I2** everything the daemon derives today still works while displayed this way:
  board updates, rate-limit backoff, approval dialogs, agent tree, review.
- **I3** selecting a different task retargets the worker pane without restarting the
  daemon or losing the session.
- **I4** with no multiplexer installed, `podium term` says so plainly and the emulated
  pane remains available as a fallback (decision 44: never a fake, always a stated
  degradation).

## Why this replaces chasing emulator parity

Two programs cannot own the same terminal region, so the only ways to show a worker's
UI are: emulate it (a preview that will never be pixel-perfect) or give it a real
terminal. A multiplexer gives it a real terminal *without* taking over the whole screen
— which is exactly the operator's request, and removes an entire class of rendering
bugs (stacking repaints, wide-glyph drift, colour mapping) rather than fixing them one
at a time.

## Rev 2 (2026-07-29) — the emulator is deleted, tmux is the way

Operator ruling: *"Remove the viewport that was replicating the Claude terminal — use
tmux always, only that is the way."*

- `podium/tui/term.py` (the pyte emulator and its pane) is **deleted**, along with its
  tests and the `pyte` dependency. Podium no longer re-renders a worker's UI anywhere.
- Coding workers (`claude`, `gemini`, `codex`) **always** run in a real terminal
  (`TmuxSession`). There is no emulated fallback and no backend switch: a missing
  multiplexer is a stated error, not a silent downgrade (decision 44).
- The cockpit's session tab shows what the session is and how to reach its real
  terminal (`podium term`, `tmux attach -t podium-<sid>`, `ctrl+o`) — never a redraw.
- The daemon's visibility is unchanged: it reads the captured pane bytes, so board,
  metering, prompts, hooks, peer brokering and the agent tree all work as before.

The in-process `PtySession` remains only as the test harness transport for the mock
worker; no operator-facing surface renders a terminal.
