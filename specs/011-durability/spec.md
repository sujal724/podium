# Spec 011 — Durability: adopt survivors, shut down cleanly, start at login

**Status:** In progress · **Date:** 2026-07-30 · First item of the agreed order.

## Why

Workers now run in tmux (spec 010), so they **outlive the daemon**. The old boot path
re-queued every in-flight task, which after a restart would start a *second* worker on
a task whose first worker was still running — duplicated work and duplicated quota.

## What

1. **Adopt on boot.** Before re-queuing anything, the daemon lists live `podium-*`
   tmux sessions and re-attaches to those with an open session row: it resumes reading
   the capture file, registers the session, marks the task `running`, and monitors it
   to completion through the normal finish path (so an adopted task still reaches
   review). **J1**
2. **Re-queue only the genuinely dead.** Tasks whose workers are gone are re-queued as
   before — and the `interrupted` event is recorded first, so the retry *continues* the
   worker conversation instead of restarting it. Adopted sessions are never marked
   interrupted. **J2**
3. **Graceful shutdown.** SIGTERM/SIGINT/SIGHUP record an `interrupted` event per
   in-flight task and a `daemon.shutdown` event, close the server and the store, and
   leave the tmux workers running. A clean stop is therefore distinguishable from a
   crash, and a laptop shutdown loses nothing. **J3**
4. **Start at login.** `packaging/podiumd.service` (systemd user unit). With
   `loginctl enable-linger`, both the daemon and its tmux workers survive logout.

## Install

```sh
mkdir -p ~/.config/systemd/user
cp packaging/podiumd.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now podiumd
loginctl enable-linger "$USER"     # survive logout
```

## Acceptance criteria

- **J1** a session whose tmux worker is alive is adopted, its task stays `running`,
  and it is not re-queued.
- **J2** a task with no surviving worker is re-queued with an `interrupted` event so
  the retry resumes; adopted sessions keep `running` status.
- **J3** a signal-driven stop records interrupts and shuts down cleanly.
