# Spec 016 — Housekeeping

- **Session GC** (`sessions.gc`, `podium gc --keep N`): dead sessions are archived
  down to the newest N per task, so one task no longer shows nine corpses. Rows are
  kept for history — archived, never deleted. **N1**
- **Pending approvals** (`approvals.pending`, `podium pending`): every question still
  waiting — worker dialogs and peer-call requests alike — with the exact command to
  answer it. A missed dialog is recoverable instead of lost. **N2**
