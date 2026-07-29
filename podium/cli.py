"""`podium` — CLI entry. `podium daemon` runs podiumd; `podium tui` opens the cockpit;
everything else is a thin one-shot WS client against the running daemon.
"""

import argparse
import asyncio
import json
import sys

import websockets

from podium import protocol
from podium.config import CONFIG


async def _rpc(frame: dict, wait_types: tuple[str, ...] = ()) -> list[dict]:
    """Send one frame; collect the direct reply (and any wait_types frames)."""
    out = []
    async with websockets.connect(CONFIG.ws_url) as ws:
        # consume hello + snapshot
        await ws.recv()
        await ws.recv()
        await ws.send(protocol.dumps(frame))
        want = 1 + len(wait_types)
        try:
            while len(out) < want:
                reply = protocol.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                broadcast_only = ("output", "session.status", "session.created",
                                  "task.updated", "task.claimed", "task.proposed",
                                  "quota.update")
                if reply["type"] in broadcast_only and reply["type"] not in wait_types:
                    continue
                out.append(reply)
        except TimeoutError:
            pass
    return out


def _print(frames: list[dict]) -> None:
    for f in frames:
        print(json.dumps(f, indent=2))


def cmd_send(args) -> None:
    frame = {"type": args.frame_type}
    if args.json:
        frame |= json.loads(args.json)
    _print(asyncio.run(_rpc(frame)))


def cmd_workspace_add(args) -> None:
    _print(asyncio.run(_rpc({"type": "work.create", "entity": "workspace",
                             "fields": {"name": args.name}})))


def cmd_project_add(args) -> None:
    fields = {"workspace_id": args.workspace, "name": args.name,
              "repo_root": args.repo, "base_branch": args.base}
    _print(asyncio.run(_rpc({"type": "work.create", "entity": "project",
                             "fields": fields})))


def cmd_task_add(args) -> None:
    fields = {"project_id": args.project, "title": args.title,
              "description": args.description or "", "priority": args.priority,
              "status": "ready" if args.ready else "backlog"}
    if args.dep:
        fields["deps"] = args.dep
    _print(asyncio.run(_rpc({"type": "work.create", "entity": "task",
                             "fields": fields})))


def cmd_board(args) -> None:
    frames = asyncio.run(_rpc({"type": "work.list"}))
    for f in frames:
        if f["type"] != "work.snapshot":
            continue
        for t in f.get("tasks", []):
            print(f"{t['id']}  [{t['status']:>9}]  P{t['priority']}  {t['title']}"
                  + (f"  ← {t['assignee']}" if t.get("assignee") else ""))


def cmd_task_run(args) -> None:
    _print(asyncio.run(_rpc({"type": "task.run", "task_id": args.task_id,
                             "worker": args.worker})))


def cmd_review(args) -> None:
    frames = asyncio.run(_rpc({"type": "review.diff", "task_id": args.task_id}))
    for f in frames:
        if f["type"] == "review.ready":
            print(f"# branch {f['branch']}\n{f['diff']}")
        else:
            _print([f])


def cmd_approve(args) -> None:
    _print(asyncio.run(_rpc({"type": "review.approve", "task_id": args.task_id})))


def cmd_reject(args) -> None:
    _print(asyncio.run(_rpc({"type": "review.reject", "task_id": args.task_id,
                             "feedback": args.feedback})))


def cmd_quota(args) -> None:
    _print(asyncio.run(_rpc({"type": "quota.query"})))


def cmd_workers(args) -> None:
    async def get():
        async with websockets.connect(CONFIG.ws_url) as ws:
            return protocol.loads(await ws.recv())  # hello carries posture
    hello = asyncio.run(get())
    for w in hello["workers"]:
        mark = "✓ available" if w["ok"] else f"✗ {w.get('hint', 'unavailable')}"
        extra = f"  [{w['auth']}, tos={w['tos']}]"
        warn = f"\n    ⚠ {w['warning']}" if w.get("warning") else ""
        print(f"{w['name']:<12} {mark}{extra}{warn}")


def cmd_update(args) -> None:
    frames = asyncio.run(_rpc({"type": "update.apply" if args.apply
                               else "update.check"}))
    for f in frames:
        if f["type"] == "update.status":
            if not f.get("available"):
                print(f"self-update unavailable: {f.get('reason')}")
            elif f.get("behind", 0) == 0:
                print(f"up to date (v{f['installed']}, {f.get('head')})")
            else:
                print(f"update available: v{f['installed']} → "
                      f"v{f.get('remote_version')} ({f['behind']} commit(s) behind)\n"
                      f"apply with: podium update --apply")
        else:
            _print([f])


def cmd_answer(args) -> None:
    _print(asyncio.run(_rpc({"type": "answer", "session_id": args.session_id,
                             "question_id": args.question_id or "",
                             "value": args.value})))


def cmd_daemon(args) -> None:
    from podium.gateway import main as daemon_main
    daemon_main()


def cmd_tui(args) -> None:
    from podium.tui.app import run
    run()


def cmd_seed_backlog(args) -> None:
    """Dogfooding bootstrap: seed Podium's own remaining Stage A+ backlog into the
    work store (specs/001 plan — tasks live in the tracker; Podium is the tracker now)."""

    async def seed() -> None:
        ws = (await _rpc({"type": "work.create", "entity": "workspace",
                          "fields": {"name": "podium"}}))[0]["id"]
        proj = (await _rpc({"type": "work.create", "entity": "project",
                            "fields": {"workspace_id": ws, "name": "podium",
                                       "repo_root": args.repo,
                                       "base_branch": args.base}}))[0]["id"]
        items = [
            ("Wire the Claude /usage quota read surface (ADR-0005)",
             "PTY-parse /usage + the OAuth usage endpoint behind it; gauge flips from "
             "`unavailable` to real numbers. Surface: quota.read.", 1),
            ("Interaction layer: native approval callbacks (Stage B)",
             "SDK can_use_tool + ACP setSessionMode surfaced as one uniform prompt. "
             "Surface: interaction.", 1),
            ("Resource ingestion pipeline (Stage B)",
             "httpx fetch → readability/markdown → chunks → FTS5, provenance kept. "
             "Surface: ingestion.", 2),
            ("GitHub Issues TMS adapter (Stage B)",
             "Adapter interface + two-way sync; TMS is source of truth when connected. "
             "Surface: tms.", 2),
            ("PWA read/monitor via authenticated tunnel (Stage B)",
             "Responsive PWA bound to the coordinator; board/sessions/gauge from the "
             "phone. Surface: mobile.", 2),
            ("Seeded model matrix (Stage B)",
             "Matrix table from cited benchmarks + operator pins, visible/editable. "
             "Surface: matrix.", 2),
            ("Quota pacing controller (Stage B)",
             "Admit/defer before dispatch against the read reservoirs. Surface: "
             "pacing.", 2),
            ("Terminal-emulator-grade session pane",
             "Replace best-effort ANSI rendering with a real emulator widget "
             "(spec 001 non-goal, registered increment).", 3),
        ]
        for title, desc, prio in items:
            await _rpc({"type": "work.create", "entity": "task",
                        "fields": {"project_id": proj, "title": title,
                                   "description": desc, "priority": prio,
                                   "status": "ready"}})
        print(f"seeded workspace {ws}, project {proj}, {len(items)} tasks")

    asyncio.run(seed())


def main() -> None:
    p = argparse.ArgumentParser(prog="podium")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("daemon", help="run the daemon (podiumd)").set_defaults(fn=cmd_daemon)
    sub.add_parser("tui", help="open the cockpit").set_defaults(fn=cmd_tui)

    s = sub.add_parser("workspace-add"); s.add_argument("name")
    s.set_defaults(fn=cmd_workspace_add)

    s = sub.add_parser("project-add")
    s.add_argument("name"); s.add_argument("--workspace", required=True)
    s.add_argument("--repo", required=True); s.add_argument("--base", default="main")
    s.set_defaults(fn=cmd_project_add)

    s = sub.add_parser("task-add")
    s.add_argument("title"); s.add_argument("--project", required=True)
    s.add_argument("--description"); s.add_argument("--priority", type=int, default=2)
    s.add_argument("--ready", action="store_true")
    s.add_argument("--dep", action="append", help="task id this task is blocked by")
    s.set_defaults(fn=cmd_task_add)

    sub.add_parser("board").set_defaults(fn=cmd_board)

    s = sub.add_parser("task-run")
    s.add_argument("task_id"); s.add_argument("--worker")
    s.set_defaults(fn=cmd_task_run)

    s = sub.add_parser("review"); s.add_argument("task_id"); s.set_defaults(fn=cmd_review)
    s = sub.add_parser("approve"); s.add_argument("task_id"); s.set_defaults(fn=cmd_approve)
    s = sub.add_parser("reject")
    s.add_argument("task_id"); s.add_argument("feedback")
    s.set_defaults(fn=cmd_reject)

    sub.add_parser("quota").set_defaults(fn=cmd_quota)
    sub.add_parser("workers", help="worker availability + posture").set_defaults(
        fn=cmd_workers)

    s = sub.add_parser("update", help="check for / apply a Podium update")
    s.add_argument("--apply", action="store_true")
    s.set_defaults(fn=cmd_update)

    s = sub.add_parser("answer", help="answer a worker question")
    s.add_argument("session_id"); s.add_argument("value")
    s.add_argument("--question-id", dest="question_id")
    s.set_defaults(fn=cmd_answer)

    s = sub.add_parser("send", help="send a raw wire frame")
    s.add_argument("frame_type"); s.add_argument("--json")
    s.set_defaults(fn=cmd_send)

    s = sub.add_parser("seed-backlog", help="seed Podium's own backlog (dogfooding)")
    s.add_argument("--repo", required=True); s.add_argument("--base", default="main")
    s.set_defaults(fn=cmd_seed_backlog)

    args = p.parse_args()
    try:
        args.fn(args)
    except (ConnectionRefusedError, OSError) as e:
        print(f"cannot reach podiumd at {CONFIG.ws_url} — is it running? ({e})",
              file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
