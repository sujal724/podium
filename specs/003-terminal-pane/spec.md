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

## Non-goals (registered)

Raw keystroke forwarding (arrows/Ctrl straight into the PTY — takeover input remains
line-based; full key passthrough is a follow-up), scrollback history in the pane
(transcripts hold full history).
