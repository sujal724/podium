# Spec 003 — Terminal-emulator session pane

**Status:** Approved for build · **Date:** 2026-07-29 · Closes backlog task `t_a968cd`
(registered as a non-goal in spec 001; promoted by the first dogfood run).

## What & why

The session pane appended each PTY chunk to a log. Full-screen CLIs (Claude Code's ink
UI) repaint the whole screen continuously with cursor-positioning escapes, so the pane
showed every repaint stacked — "repeating a lot of messages", unreadable next to the
real terminal. The visibility invariant demands the pane behave like a terminal:
maintain a screen grid, apply the escape codes, render the *current* screen.

## How

- `podium/tui/term.py`: `TerminalEmulator` — pure, testable pyte-backed screen
  (feed/resize/plain_lines/rich_lines with colors + attributes); `TerminalView` — the
  Textual widget rendering it.
- The cockpit keeps one emulator per session (fed for all sessions, rendered for the
  selected one; snapshot backlogs replay through the same path so late attach shows the
  live screen, not history soup).
- Pane resize propagates to the daemon (`resize` frame) so the PTY winsize matches and
  the CLI reflows to fit.

## Acceptance criteria

- **C1:** a screen repainted N times renders once (screen state, not append).
- **C2:** in-place updates (spinner/progress) show the latest value only.
- **C3:** resize reshapes the grid; the daemon receives the matching `resize` frame.
- **C4:** colors/bold survive into the rendered pane (pyte→rich mapping incl. the
  ANSI brown→yellow slot and 256-color hex).

## Rev 2 (2026-07-29, after dogfooding v0.3.2)

Two causes of the remaining breakage, both fixed:
- **Width-mismatch interleave**: PTYs spawned at the config default (120×40); the
  cockpit then resized them to the pane. Full-screen CLIs only repaint their live
  region — transcript already scrolled out stays laid out for the old width, so old-
  and new-width content interleaved into garbage. Now the cockpit reports its pane
  size (`winsize` frame), the daemon remembers it, and **new PTYs spawn at the pane
  size** — no mismatch to interleave. (**C6**)
- **No scrollback**: the pane kept only the visible grid; scrolled-off transcript was
  lost and the pane couldn't scroll. Now pyte `HistoryScreen` keeps 5000 lines of
  scrollback rendered in a scrollable container, anchored to the live bottom unless
  the operator scrolls up. (**C5**)
- Cosmetic: the quota gauge rendered the pending read surface as
  "claude: unavailable", reading like a dead worker; now a dash + one explanatory
  note (ADR-0005).

## Non-goals (registered)

Raw keystroke forwarding (arrows/Ctrl straight into the PTY — takeover input remains
line-based; full key passthrough is a follow-up); reflow of *existing* scrollback on
pane resize after spawn (real terminals reflow, pyte doesn't — new output is laid out
correctly, old lines keep their original width).


## Rev 3 (2026-07-29) — fixed viewport, and prefer the real TUI

Rev 2's scrollback made the pane a *growing document* (content-tall widget rendering
history + live screen on every chunk), so repaints visibly stacked: "the things
rendered before stay and new things render on top". That is not how a terminal works.

- The pane is a **fixed viewport** again: it renders exactly the emulator's current
  screen, never a growing document. **C11** (200 repaints render as one screen).
- Scrollback is **paged through that grid** with PgUp/PgDn using pyte's own
  `prev_page`/`next_page` — the emulator's mechanism, not a second rendering path.

**Emulation is a preview, not the destination.** For full fidelity Podium should hand
the operator the worker's *real* TUI rather than re-render it: `ctrl+o` already
suspends the cockpit and gives the terminal to the actual Claude UI for that session
(its worktree, its conversation), returning to the cockpit on exit. The registered next
increment is running workers inside a terminal multiplexer so an operator can attach to
a live session at full fidelity while the daemon keeps reading the same PTY — no
emulator in the path at all.
