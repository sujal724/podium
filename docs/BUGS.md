# Bug report — dogfooding Podium on itself (2026-07-29/30)

Every defect found by running Podium against its own backlog. Fixed items name the
PR; open items name the next action. Nothing here is theoretical — each was observed.

## Fixed

| # | Bug | Cause | Fix |
|---|---|---|---|
| 1 | Dispatched sessions froze on start | Claude's "do you trust this folder?" dialog in each new worktree | Worker dialogs surfaced as operator questions (#3) |
| 2 | Session pane unreadable | Full-screen CLI repaints appended to a log | Terminal emulator (#4), then deleted entirely for real tmux panes (#11) |
| 3 | Cockpit crashed on Claude output | pyte reports `brightblue`, rich wants `bright_blue`; one unmapped name was fatal | Mapping + degrade-don't-crash rendering (#6) |
| 4 | Text intermittently garbled | PTY reads decoded per-chunk, splitting multibyte glyphs | Incremental UTF-8 decoder (#6) |
| 5 | Self-update deadlocked | `apply` awaited the restart inside the handler the restart waits for | Detached apply + bounded shutdown steps (#5) |
| 6 | `update --apply` said "already up to date" right after a merge | Judged from the last fetch | Fetch before deciding (#9) |
| 7 | v0.4.0 broke a live board | `CREATE TABLE IF NOT EXISTS` never alters an existing table (`tasks.base_ref` missing) | Additive migration on open (#9) |
| 8 | Every client disconnected on connect | A session backlog exceeded the 1 MB frame limit | Client limit raised (#11); daemon now sends a bounded tail (this PR) |
| 9 | Bypass mode ignored the peer-call deny | `--dangerously-skip-permissions` ignores hook denials | Enforcement moved to PATH shims (#8) |
| 10 | Peer calls blocked even in supervised mode | Deny was hardcoded, never consulted the mode | Mode-governed brokering (#10) |
| 11 | Transcripts silently disabled | Workers inherited `CLAUDE_CODE_*` markers | Stripped in `worker_env` (#8) |
| 12 | Retries restarted from scratch | Boot re-queue never logged an `interrupted` event | Interrupt recorded before re-queue (#12/#13) |
| 13 | Restart could duplicate a running worker | tmux workers outlive the daemon; boot re-queued them | Adopt live sessions on boot (#13) |
| 14 | An adopted session captured nothing | `pipe-pane -o` **toggles** — the second call turned capture off | `_ensure_pipe` checks `#{pane_pipe}` (#14) |
| 15 | Early worker output lost | Capture attached after the pane started | Seed from `capture-pane` scrollback (#14) |
| 16 | `podium term` right pane empty | Guessed live sessions from tmux names | Ask the daemon (`sessions.list`) (#12) |
| 17 | Ctrl+O replaced the cockpit pane | It suspended its own app | Target the other pane / detached terminal (#13) |
| 18 | Keyboard input went nowhere | Focus sat on the board tree | Takeover focused on mount; ctrl-keyed actions (#8) |
| 19 | Four tasks dispatched unintentionally | `d` had no feedback and no concurrency guard | Feed confirmation (#13); guard still open (see below) |
| 20 | Process unfindable after self-update | Re-exec came back as `python -m podium.gateway` | Re-exec the same entry point (this PR) |
| 21 | Port clash printed a raw traceback | No pre-bind check | Explains the clash and names the holding PID (this PR) |

## Open

| # | Bug | Next action |
|---|---|---|
| A | Self-update applies the code but the running daemon may keep serving the old version | Instrument the `os.execv` path and assert the version after boot; #20's fix may already resolve it — needs one clean update cycle to confirm |
| B | Dispatch has no concurrency guard: repeated `ctrl+d` starts parallel workers | Confirm dialog when N sessions are live (task `t_04522f`) |
| C | `podium term` / `ctrl+o` pane targeting unverified in a live split | Manual check now that tmux is installed |
| D | Scrollback lost on pane resize after spawn | Registered non-goal (spec 003); revisit only if it bites |
| E | Quota gauge reads `unavailable` | ADR-0005 reader not wired (task `t_bafde9`) |
