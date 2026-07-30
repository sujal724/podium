"""`podium` — CLI entry. `podium daemon` runs podiumd; `podium tui` opens the cockpit;
everything else is a thin one-shot WS client against the running daemon.
"""

import argparse
import asyncio
import json
import os
import sys

import websockets

from podium import protocol
from podium.config import CONFIG


async def _rpc(frame: dict, wait_types: tuple[str, ...] = ()) -> list[dict]:
    """Send one frame; collect the direct reply (and any wait_types frames)."""
    out = []
    async with websockets.connect(CONFIG.ws_url, max_size=16 * 1024 * 1024) as ws:
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


def cmd_pending(args) -> None:
    frames = asyncio.run(_rpc({"type": "interaction.pending"}))
    for f in frames:
        if f["type"] != "approval.pending":
            _print([f])
            continue
        if not f["requests"]:
            print("no pending approvals")
        for r in f["requests"]:
            opts = " | ".join(o["id"] for o in r["options"])
            print(f"{r['id']}  [{r['session_id']}]  {r['title']}  ({opts})")
            if r.get("detail"):
                print(f"    {r['detail']}")


def cmd_mode(args) -> None:
    _print(asyncio.run(_rpc({"type": "session.mode", "session_id": args.session_id,
                             "mode": args.mode})))


def cmd_workers(args) -> None:
    async def get():
        async with websockets.connect(CONFIG.ws_url, max_size=16 * 1024 * 1024) as ws:
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


def cmd_sessions(args) -> None:
    frames = asyncio.run(_rpc({"type": "sessions.list"}))
    for f in frames:
        if f["type"] != "sessions.snapshot":
            continue
        for s in f["sessions"]:
            line = (f"{s['id']}  [{s['status']:>11}]  {s['worker']:<7} "
                    f"{s.get('title') or s.get('task_id') or '(direct spawn)'}")
            if s.get("resume_key"):
                line += f"\n{'':14}claude --resume {s['resume_key']}"
            print(line)


MARK = {"workspace": "▣", "project": "▸", "task": "•",
        "session": "◆", "subagent": "└─◇", "peer": "⚠"}


def cmd_tree(args) -> None:
    """With a task id: everything working on that task. Without: the whole
    hierarchy — workspace → project → task → session → subagent/peer."""
    if not args.task_id:
        frames = asyncio.run(_rpc({"type": "agents.scope"}))
        for f in frames:
            if f["type"] != "agents.scope":
                _print([f]); continue
            for n in f["nodes"]:
                st = f" [{n['status']}]" if n.get("status") else ""
                print(f"{'  ' * n['depth']}{MARK.get(n['kind'], '·')} "
                      f"{n['label']}{st}")
        return
    frames = asyncio.run(_rpc({"type": "agents.tree", "task_id": args.task_id}))
    for f in frames:
        if f["type"] != "agents.tree":
            _print([f]); continue
        if not f["agents"]:
            print("no agents recorded for this task yet")
        for a in f["agents"]:
            pad = "  " * a["depth"]
            mark = MARK.get(a["kind"], "·")
            detail = (a.get("detail") or "")[:70]
            print(f"{pad}{mark} {a['label']} [{a['status']}] {a['kind']}"
                  f"{'  ' + detail if detail else ''}")


def _age(seconds: int) -> str:
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def cmd_task(args) -> None:
    """Full clarity on one task: stage, base, sessions, what you can do."""
    frames = asyncio.run(_rpc({"type": "task.detail", "task_id": args.task_id}))
    d = next((f for f in frames if f["type"] == "task.detail"), None)
    if d is None:
        _print(frames); return
    t, g = d["task"], d["git"]
    bc = d["breadcrumb"]
    path = " → ".join(filter(None, [
        (bc["workspace"] or {}).get("name"), (bc["project"] or {}).get("name"),
        *[p["title"] for p in bc["parents"]]]))
    print(f"{t['id']}  {t['title']}")
    print(f"  {path}")
    print(f"\n  stage      {t['status']}  (for {_age(d['since'])})"
          f"  P{t['priority']}  origin={t['origin']}"
          + (f"/{t['detector']}" if t.get("detector") else ""))
    if d["reason"]:
        print(f"  why        {d['reason']}")
        print(f"  next       {d['hint']}")
    print(f"  worker     {t.get('assignee') or '—'}  ·  mode "
          f"{d['autonomy']['mode']} ({d['autonomy']['source']})")
    print(f"\n  branch     {g['branch']}")
    print(f"  base       {g['base_ref']}  ← {g['base_reason']}")
    print(f"  merges to  {g['merge_target']}")
    print(f"  commits    {g.get('commits_ahead', 0)} ahead"
          + (f"  ({g['diffstat'].strip()})" if g.get("diffstat") else ""))
    print(f"  worktree   {g['worktree'] or '—'}"
          + ("" if g["worktree_exists"] else "  (gone)"))
    if d["blocked_by"] or d["blocks"] or d["subtasks"]:
        print()
        for dep in d["blocked_by"]:
            print(f"  blocked by {dep['id']} [{dep['status']}] {dep['title']}")
        for dep in d["blocks"]:
            print(f"  blocks     {dep['id']} [{dep['status']}] {dep['title']}")
        for sub in d["subtasks"]:
            print(f"  subtask    {sub['id']} [{sub['status']}] {sub['title']}")
    print(f"\n  resume     {d['resume']['explanation']}")
    for s in d["sessions"][:6]:
        line = f"  session    {s['id']} [{s['status']}] {s['worker']}"
        if s.get("resume_key"):
            line += f"  claude --resume {s['resume_key']}"
        print(line)
    if d["feedback"]:
        print()
        for fb in d["feedback"]:
            print(f"  feedback   {fb.get('feedback', '')[:100]}")
    print("\n  actions")
    for name, a in d["actions"].items():
        mark = "✓" if a["enabled"] else "✗"
        print(f"    {mark} {name:<9} {a['reason']}")


def cmd_queue(args) -> None:
    """Everything awaiting your review, with where each would land."""
    frames = asyncio.run(_rpc({"type": "review.queue"}))
    for f in frames:
        if f["type"] != "review.snapshot":
            _print([f]); continue
        if not f["tasks"]:
            print("nothing awaiting review")
        for t in f["tasks"]:
            print(f"{t['id']}  P{t['priority']}  {t['title']}\n"
                  f"{'':12}approve merges {t['branch']} → {t['merge_target']}")


def cmd_pending(args) -> None:
    """Approvals still waiting on you (a missed dialog is not lost)."""
    frames = asyncio.run(_rpc({"type": "approvals.pending"}))
    for f in frames:
        if f["type"] != "approvals.pending":
            _print([f]); continue
        if not f["questions"]:
            print("no pending approvals")
        for q in f["questions"]:
            print(f"{q['id']}  [{q['kind']}]  {q['q']}")
            for i, c in enumerate(q["choices"], 1):
                print(f"{'':4}{i}. {c}")
            print(f"{'':4}answer: podium answer {q['session_id']} <n> "
                  f"--question-id {q['id']}")


def cmd_gc(args) -> None:
    _print(asyncio.run(_rpc({"type": "sessions.gc", "keep": args.keep})))


def cmd_attach(args) -> None:
    """Attach to a session's REAL terminal — the other tmux pane when inside
    tmux, otherwise this terminal."""
    import os
    import subprocess
    frames = asyncio.run(_rpc({"type": "session.target",
                               "session_id": args.session, "task_id": args.task}))
    tgt = next((f for f in frames if f["type"] == "session.target"), None)
    if tgt is None:
        _print(frames); sys.exit(1)
    if not tgt["live"]:
        print(f"session {tgt['session_id']} has no live terminal; resuming instead")
        os.chdir(tgt["cwd"]); os.execvp(tgt["resume"][0], tgt["resume"])
    if os.environ.get("TMUX"):
        me = os.environ.get("TMUX_PANE", "")
        panes = subprocess.run(["tmux", "list-panes", "-F", "#{pane_id}"],
                               capture_output=True, text=True).stdout.split()
        other = next((p for p in panes if p != me), None)
        if other:
            subprocess.run(["tmux", "join-pane", "-h", "-s", f"{tgt['tmux']}:",
                            "-t", other], check=False)
            print(f"session {tgt['session_id']} is now in pane {other}")
            return
    os.execvp("tmux", tgt["attach"])


def cmd_open(args) -> None:
    """Drop into a task's worktree and resume its exact Claude session in THIS
    terminal (falls back to --continue when the session id isn't captured yet)."""
    frames = asyncio.run(_rpc({"type": "work.list"}))
    task = next((t for f in frames if f["type"] == "work.snapshot"
                 for t in f.get("tasks", []) if t["id"] == args.task_id), None)
    if task is None:
        sys.exit(f"no task {args.task_id}")
    wt = task.get("worktree")
    if not wt or not os.path.isdir(wt):
        sys.exit(f"task {args.task_id} has no live worktree "
                 "(only running/review tasks keep one)")
    sframes = asyncio.run(_rpc({"type": "sessions.list"}))
    key = next((s["resume_key"] for f in sframes if f["type"] == "sessions.snapshot"
                for s in f["sessions"]
                if s.get("task_id") == args.task_id and s.get("resume_key")), None)
    argv = ["claude", "--resume", key] if key else ["claude", "--continue"]
    print(f"→ {wt} ({' '.join(argv)})")
    os.chdir(wt)
    os.execvp("claude", argv)


def cmd_term(args) -> None:
    """One page, two REAL terminals (spec 010): the cockpit in one pane and the
    worker's actual CLI in the other — rendered by your terminal, not emulated."""
    import shutil
    import subprocess
    if not shutil.which("tmux"):
        sys.exit("tmux is not installed — `podium term` needs it for real terminal "
                 "panes.\nInstall tmux, or use `podium tui` (emulated pane) / "
                 "`podium open <task>` (hand the whole terminal to one session).")
    window = "podium"
    live = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"],
                          capture_output=True, text=True).stdout.split()
    if window in live:
        subprocess.run(["tmux", "kill-session", "-t", window])
    subprocess.run(["tmux", "new-session", "-d", "-s", window, "podium tui"],
                   check=True)
    worker = args.session or next(
        (s for s in live if s.startswith("podium-s_")), None)
    if worker:
        # move the worker's own pane into this window: two real terminals, one page
        subprocess.run(["tmux", "join-pane", "-h", "-s", f"{worker}:",
                        "-t", f"{window}:0"])
    else:
        subprocess.run(["tmux", "split-window", "-h", "-t", f"{window}:0",
                        "podium sessions; echo; echo 'no live worker session — "
                        "dispatch one (ctrl+d in the cockpit), then rerun "
                        "podium term'; exec ${SHELL:-sh}"])
    subprocess.run(["tmux", "select-pane", "-t", f"{window}:0.0"])
    os.execvp("tmux", ["tmux", "attach", "-t", window])


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

    s = sub.add_parser("term", help="cockpit + the worker's REAL terminal, side by side")
    s.add_argument("--session", help="tmux session name of a specific worker")
    s.set_defaults(fn=cmd_term)

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

    s = sub.add_parser("task", help="full detail for one task (stage, base, sessions)")
    s.add_argument("task_id"); s.set_defaults(fn=cmd_task)

    s = sub.add_parser("task-run")
    s.add_argument("task_id"); s.add_argument("--worker")
    s.set_defaults(fn=cmd_task_run)

    s = sub.add_parser("open", help="resume a task's claude session in this terminal")
    s.add_argument("task_id"); s.set_defaults(fn=cmd_open)

    sub.add_parser("sessions", help="list sessions with claude resume commands"
                   ).set_defaults(fn=cmd_sessions)

    s = sub.add_parser("tree", help="hierarchy: workspace→project→task→session→agents")
    s.add_argument("task_id", nargs="?"); s.set_defaults(fn=cmd_tree)

    s = sub.add_parser("review"); s.add_argument("task_id"); s.set_defaults(fn=cmd_review)
    s = sub.add_parser("approve"); s.add_argument("task_id"); s.set_defaults(fn=cmd_approve)
    s = sub.add_parser("reject")
    s.add_argument("task_id"); s.add_argument("feedback")
    s.set_defaults(fn=cmd_reject)

    sub.add_parser("quota").set_defaults(fn=cmd_quota)
    sub.add_parser("queue", help="tasks awaiting your review").set_defaults(fn=cmd_queue)
    sub.add_parser("pending", help="approvals waiting on you").set_defaults(
        fn=cmd_pending)

    s = sub.add_parser("gc", help="archive old dead sessions")
    s.add_argument("--keep", type=int, default=3); s.set_defaults(fn=cmd_gc)

    s = sub.add_parser("attach", help="attach to a session's real terminal")
    s.add_argument("--session"); s.add_argument("--task")
    s.set_defaults(fn=cmd_attach)
    sub.add_parser("workers", help="worker availability + posture").set_defaults(
        fn=cmd_workers)

    s = sub.add_parser("update", help="check for / apply a Podium update")
    s.add_argument("--apply", action="store_true")
    s.set_defaults(fn=cmd_update)

    s = sub.add_parser("answer", help="answer a worker question or a pending "
                       "native approval (one uniform answer path)")
    s.add_argument("session_id"); s.add_argument("value")
    s.add_argument("--question-id", "--request-id", dest="question_id")
    s.set_defaults(fn=cmd_answer)

    sub.add_parser("pending", help="list pending native approval prompts")\
        .set_defaults(fn=cmd_pending)

    s = sub.add_parser("mode", help="set a session's approval mode (SDK "
                                    "permission mode / ACP setSessionMode)")
    s.add_argument("session_id"); s.add_argument("mode")
    s.set_defaults(fn=cmd_mode)

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
