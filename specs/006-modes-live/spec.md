# Spec 006 — Bypass mode, OS-level peer guard, live mode switching

**Status:** Approved for build · **Date:** 2026-07-29 · Operator requests + one
security finding from verifying them.

## 1. A third autonomy mode: `bypass`

`supervised` (asks before commands) · `autonomous` (tools auto-accepted) ·
**`bypass`** — `--dangerously-skip-permissions`: asks nothing, edits code, runs
commands, searches the web on its own. The operator's machine, the operator's call;
the mode is always labelled in the cockpit (`BYPASS · asks nothing`).

Bypass has the CLI's own one-time risk acceptance ("you accept all responsibility").
Podium **surfaces it as a dialog and never answers it** — same rule as the trust
dialog.

## 2. The finding: policy-layer deny is not enough (decision 50)

Verified against the real CLI: under `--dangerously-skip-permissions` the worker
**ignores hook denials**, so the PreToolUse guard did not stop `claude --version`.
A permission-layer deny cannot enforce a policy that outlives permission modes.

**Fix — enforce below the policy layer:** each worker session gets `.podium/bin/`
first on its `PATH`, holding shims named for every peer binary that refuse with
exit 126. No permission setting can switch that off. The hook guard stays as
defense in depth and for logging. **C7**: shims block in every mode; **C8**: the
hook still blocks in non-bypass modes.

## 3. The other finding: inherited env broke session persistence

Worker sessions inherited `CLAUDE_CODE_*` markers from the launching environment,
which silently **disabled the CLI's transcript saving** ("Transcript saving is off —
inherited CLAUDE_CODE_CHILD_SESSION marker") — breaking session visibility (spec 004)
and resume (spec 005). `policy.worker_env()` now strips those markers along with API
keys. **C9**.

## 4. Modes are not fixed at spawn

A session's permission mode can change **while it runs** — Podium drives the CLI's own
shift+tab cycle until its status line reports the target mode (`session.mode` frame,
`ctrl+p` in the cockpit). Supervised work can go bypass mid-flight and back, matching
the drive-mode handoff philosophy (LLD §25; the interactive⇄autonomous *driver* swap
remains the Stage C surface). **C10**.
