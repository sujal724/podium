# Spec 014 — Session actions: target the other pane, never your own

`Ctrl+O` (cockpit) and `podium attach` no longer suspend or steal the cockpit's own
terminal. Inside tmux they drive the **other pane** (`respawn-pane` / `join-pane`
against `podium-<sid>`); outside tmux they open a **detached terminal window**, and
only fall back to instructions when neither exists. **L1**

The daemon answers `session.target` with where a session actually lives — its tmux
name, whether that terminal is still alive, its cwd, and the exact attach/resume
commands — so any client can point a pane at it instead of guessing. A session with
no live terminal resumes via `claude --resume <key>` rather than pretending. **L2**
