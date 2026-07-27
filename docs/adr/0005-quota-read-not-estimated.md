# ADR-0005: Quota is read from real usage surfaces, never estimated

**Status:** Accepted · **Date:** 2026-07-27

## Context

Scheduling revolves around per-service quota (Claude 5h/weekly windows; Gemini requests/day + RPM;
Codex 5h/weekly + credits). The operator can see real numbers on every service's own surfaces, so
estimated gauges are unnecessary and unwanted.

## Decision

Podium reads actual quota state from the authenticated, own-account surfaces backing what the
operator sees:

- **Claude:** the CLI's `/usage` (PTY-parse) and the account's OAuth usage endpoint behind it;
- **Gemini:** `/stats` plus exact client-side request/day + RPM counting;
- **Codex:** `/status` and app-server rate-limit/credit methods (machine-readable).

The metering ledger (every request logged with tokens/timestamps) remains for attribution,
history, and reconciliation — not as a substitute. If a read surface breaks, the gauge shows
`unavailable` (never a fabricated number) until the capability registry re-probes the surface.

## Consequences

Exact numbers drive admit/defer pacing; a per-worker "usage reader" becomes a required adapter
capability. Records research decision 49, superseding earlier "estimated gauge" language.
