"""The Podium cockpit (Textual) — the visibility invariant as a screen.

Panes: board (full hierarchy), live session (PTY stream + takeover input), review
(diff + approve/reject), approval inbox, native approvals (the uniform prompt —
y=allow / n=deny the oldest), quota gauge, event feed — and one explicit `blocked`
pane per not-yet-live surface (decision 44), stage-labelled, never hidden.

Connects to podiumd over the wire protocol like any other client.
"""

import asyncio
import shutil
import subprocess

import websockets
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (Button, Footer, Header, Input, Label, RichLog, Static,
                             TabbedContent, TabPane, Tree)

from podium import protocol
from podium.config import CONFIG
from podium.surfaces import blocked_surfaces


class QuestionDialog(ModalScreen):
    """A worker (or Podium itself) is asking the operator something — shown as a real
    dialog; the chosen option is returned to the app, which relays the answer."""

    CSS = """
    QuestionDialog { align: center middle; }
    #qbox { width: 70; max-width: 90%; padding: 1 2; border: thick $primary;
            background: $surface; }
    #qbox Button { margin: 1 1 0 0; }
    """

    def __init__(self, title: str, question: str, choices: list[str]) -> None:
        super().__init__()
        self._title = title
        self._question = question
        self._choices = choices

    def compose(self) -> ComposeResult:
        with Vertical(id="qbox"):
            yield Label(f"[b]{self._title}[/b]")
            yield Static(self._question)
            for i, label in enumerate(self._choices, start=1):
                yield Button(f"{i}. {label}", id=f"choice-{i}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(int(event.button.id.split("-")[1]))


class Cockpit(App):
    TITLE = "Podium"
    CSS = """
    #board { width: 34%; border: solid $primary; }
    #main  { width: 66%; }
    #review-log, #feed-log { border: solid $primary; height: 1fr; }
    #session-log { border: solid $primary; height: 1fr; }
    #session-bar { height: 1; background: $primary-darken-2; color: $text; }
    #workers { height: 4; border: solid $secondary; }
    #quota { height: 3; border: solid $warning; }
    #takeover { dock: bottom; }
    .blocked { color: $text-muted; padding: 1 2; }
    """
    BINDINGS = [
        # Letter bindings would swallow typing, so control actions are ctrl-keyed
        # and the keyboard belongs to the live session by default.
        Binding("ctrl+a", "approve", "approve diff"),
        Binding("ctrl+r", "reject", "reject diff"),
        Binding("ctrl+y", "allow", "allow prompt"),
        Binding("ctrl+n", "deny", "deny prompt"),
        Binding("ctrl+d", "dispatch", "run next"),
        Binding("ctrl+t", "focus_takeover", "takeover"),
        Binding("ctrl+o", "open_in_claude", "open in claude"),
        Binding("ctrl+p", "change_mode", "change mode"),
        Binding("ctrl+u", "requeue", "re-queue task"),
        Binding("ctrl+b", "focus_board", "board"),
        Binding("ctrl+q", "quit", "quit"),
        Binding("escape", "back", "back"),
        Binding("ctrl+c", "send_escape", "interrupt session"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.ws = None
        self.selected_session: str | None = None
        self.selected_task: str | None = None
        self.review_task: str | None = None
        self.pending_approvals: list[dict] = []
        self.session_info: dict[str, dict] = {}

    def _update_session_bar(self) -> None:
        """The operator must always know which session they're watching and what
        permission mode it runs under — never implicit (dogfood ruling)."""
        bar = self.query_one("#session-bar", Static)
        sid = self.selected_session
        if sid is None:
            bar.update("no session — ctrl+d dispatches the next ready task")
            return
        info = self.session_info.get(sid, {})
        mode = info.get("autonomy", "supervised")
        mode_txt = {
            "bypass": "BYPASS · asks nothing (peer-call guard still on)",
            "autonomous": "AUTONOMOUS · runs tools without asking",
        }.get(mode, "SUPERVISED · asks you before running commands")
        resumed = " · resumed" if info.get("resumed") else ""
        bar.update(f" {sid} · {info.get('label', '?')} · {mode_txt}{resumed}"
                   f" · ctrl+o open in claude · ctrl+t takeover")

    def _select_session(self, sid: str) -> None:
        self.selected_session = sid
        self._update_session_bar()
        log = self.query_one("#session-log", RichLog)
        log.clear()
        info = self.session_info.get(sid, {})
        log.write(f"session {sid} ({info.get('label', '?')}) runs in a REAL terminal.")
        log.write("")
        log.write("  podium term              cockpit + this session, side by side")
        log.write(f"  tmux attach -t podium-{sid}   attach to it directly")
        log.write("  ctrl+o                   hand this terminal to the session")
        log.write("")
        log.write("Podium is not re-rendering the worker's UI (spec 010): its output "
                  "is captured for the board, metering, prompts and the agent tree, "
                  "but the terminal itself is yours to attach to.")

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="board"):
                yield Label("work board")
                yield Tree("workspaces", id="board-tree")
                yield Static("workers: …", id="workers")
                yield Static("quota: …", id="quota")
            with Vertical(id="main"):
                with TabbedContent():
                    with TabPane("session", id="tab-session"):
                        yield Static("no session", id="session-bar")
                        yield RichLog(id="session-log", wrap=True)
                        yield Input(placeholder="takeover — text goes to the live "
                                                "session (Enter sends)", id="takeover")
                    with TabPane("review", id="tab-review"):
                        yield RichLog(id="review-log", highlight=True, wrap=False)
                    with TabPane("task", id="tab-task"):
                        yield RichLog(id="task-log", wrap=True)
                    with TabPane("agents", id="tab-agents"):
                        yield RichLog(id="agents-log", wrap=True)
                    with TabPane("inbox", id="tab-inbox"):
                        yield RichLog(id="inbox-log", wrap=True)
                    with TabPane("approvals", id="tab-approvals"):
                        yield RichLog(id="approvals-log", wrap=True)
                    with TabPane("feed", id="tab-feed"):
                        yield RichLog(id="feed-log", wrap=True)
                    for s in blocked_surfaces():
                        with TabPane(f"🔒 {s.key}", id=f"tab-{s.key.replace('.', '-')}"):
                            yield Static(
                                f"[b]{s.title}[/b]\n\n"
                                f"blocked — comes alive in Stage {s.stage}."
                                + (f"\n\n{s.note}" if s.note else ""),
                                classes="blocked")
        yield Footer()

    async def on_mount(self) -> None:
        # Focus the takeover input immediately: keys typed at the cockpit must
        # reach the live session, not the board tree (dogfood: answers to a
        # worker's approval prompt went nowhere).
        self.query_one("#takeover", Input).focus()
        self.run_worker(self._connect(), exclusive=True)

    async def _connect(self) -> None:
        feed = self.query_one("#feed-log", RichLog)
        try:
            self.ws = await websockets.connect(CONFIG.ws_url, max_size=16 * 1024 * 1024)
        except OSError as e:
            feed.write(f"cannot reach podiumd at {CONFIG.ws_url}: {e}")
            return
        await self._send({"type": "work.list"})
        await self._send({"type": "quota.query"})
        await self._send({"type": "task.inbox"})
        await self._send({"type": "interaction.pending"})
        async for raw in self.ws:
            try:
                self._on_frame(protocol.loads(raw))
            except Exception as e:
                # One odd frame (or a frame racing mount/teardown) must never
                # take down the cockpit — the stream continues.
                try:
                    feed.write(f"[frame dropped: {type(e).__name__}: {e}]")
                except Exception:
                    pass

    async def _send(self, frame: dict) -> None:
        if self.ws:
            await self.ws.send(protocol.dumps(frame))

    # --- incoming frames ----------------------------------------------------

    def _on_frame(self, f: dict) -> None:
        t = f["type"]
        feed = self.query_one("#feed-log", RichLog)
        if t == "hello":
            lines = []
            for w in f.get("workers", []):
                mark = "✓" if w["ok"] else f"✗ {w.get('hint', 'unavailable')}"
                lines.append(f"{w['name']}: {mark}")
                if not w["ok"]:
                    feed.write(f"worker {w['name']} unavailable — {w.get('hint', '')}")
                if w.get("warning"):
                    feed.write(f"worker {w['name']}: {w['warning']}")
            self.query_one("#workers", Static).update("\n".join(lines))
        elif t == "question":
            q = f["question"]
            feed.write(f"❓ [{f['session_id']}] {q['q']}")

            def answered(choice: int | None, qid=q["id"], sid=f["session_id"]):
                if choice is not None:
                    asyncio.create_task(self._send(
                        {"type": "answer", "session_id": sid,
                         "question_id": qid, "value": str(choice)}))

            self.push_screen(
                QuestionDialog(f"session {f['session_id']}", q["q"],
                               q.get("choices", [])), answered)
        elif t == "update.available":
            feed.write(f"⬆ update available: {f['installed']} → "
                       f"{f['remote_version']} ({f['behind']} commit(s))")

            def apply_update(choice: int | None):
                if choice == 1:
                    asyncio.create_task(self._send({"type": "update.apply"}))

            self.push_screen(
                QuestionDialog(
                    "Podium update",
                    f"Version {f['remote_version']} is on main (you run "
                    f"{f['installed']}). Apply now? Live sessions are stopped and "
                    "their tasks re-queued; the daemon restarts in place.",
                    ["Update now", "Not now"]), apply_update)
        elif t == "output":
            if self.selected_session is None:
                self._select_session(f["session_id"])
        elif t == "snapshot":
            for info in f.get("sessions", []):
                self.session_info[info["id"]] = info
                self._select_session(info["id"])
        elif t == "work.snapshot":
            self._render_board(f)
        elif t == "task.updated":
            asyncio.create_task(self._send({"type": "work.list"}))
            feed.write(f"task {f['task']['id']} → {f['task']['status']}")
        elif t == "review.ready":
            self.review_task = f["task_id"]
            log = self.query_one("#review-log", RichLog)
            log.clear()
            log.write(f"task {f['task_id']} on {f['branch']} — a=approve r=reject\n")
            log.write(Text(f["diff"]))
            feed.write(f"review ready: {f['task_id']}")
        elif t == "task.detail":
            log = self.query_one("#task-log", RichLog)
            log.clear()
            task, g = f["task"], f["git"]
            log.write(f"[b]{task['id']}  {task['title']}[/b]")
            log.write(f"stage: {task['status']}  ·  P{task['priority']}  ·  "
                      f"mode {f['autonomy']['mode']} ({f['autonomy']['source']})")
            if f.get("reason"):
                log.write(f"[red]why:[/red] {f['reason']}")
                log.write(f"next: {f['hint']}")
            log.write("")
            log.write(f"branch     {g['branch']}")
            log.write(f"base       {g['base_ref']}  ← {g['base_reason']}")
            log.write(f"merges to  {g['merge_target']}")
            log.write(f"commits    {g.get('commits_ahead', 0)} ahead "
                      f"{g.get('diffstat', '')}")
            for dep in f.get("blocked_by", []):
                log.write(f"blocked by {dep['id']} [{dep['status']}] {dep['title']}")
            for sub in f.get("subtasks", []):
                log.write(f"subtask    {sub['id']} [{sub['status']}] {sub['title']}")
            log.write("")
            log.write(f"resume     {f['resume']['explanation']}")
            for sess in f.get("sessions", [])[:6]:
                extra = (f"  claude --resume {sess['resume_key']}"
                         if sess.get("resume_key") else "")
                log.write(f"session    {sess['id']} [{sess['status']}]{extra}")
            log.write("")
            for name, a in f.get("actions", {}).items():
                log.write(f"{'✓' if a['enabled'] else '✗'} {name:<9} {a['reason']}")
        elif t in ("agents.tree", "agents.updated"):
            log = self.query_one("#agents-log", RichLog)
            log.clear()
            log.write(f"agents for task {f['task_id']}:")
            for a in f.get("agents", []):
                mark = {"session": "◆", "subagent": "├─◇", "peer": "⚠"}.get(
                    a["kind"], "·")
                log.write(f"{'  ' * a['depth']}{mark} {a['label']} "
                          f"[{a['status']}] {a['kind']} {(a.get('detail') or '')[:60]}")
        elif t == "task.inbox":
            log = self.query_one("#inbox-log", RichLog)
            log.clear()
            if not f["tasks"]:
                log.write("inbox empty — detectors come alive in Stage C; "
                          "proposed tasks land here")
            for task in f["tasks"]:
                log.write(f"{task['id']}  [{task.get('detector')}]  {task['title']}")
        elif t == "approval.request":
            self.pending_approvals.append(f["request"])
            self._render_approvals()
            feed.write(f"❓ {f['request']['title']} — approvals tab, ctrl+y/ctrl+n")
        elif t == "approval.resolved":
            self.pending_approvals = [r for r in self.pending_approvals
                                      if r["id"] != f["request_id"]]
            self._render_approvals()
            feed.write(f"✓ approval {f['request_id']} → {f['value']} ({f['actor']})")
        elif t == "approval.pending":
            self.pending_approvals = list(f["requests"])
            self._render_approvals()
        elif t in ("quota.update", "quota.snapshot"):
            self._render_quota(f)
        elif t == "narration":
            feed.write(f"• {f['text']}")
        elif t == "session.created":
            info = f["session"]
            self.session_info[info["id"]] = info
            self._select_session(info["id"])
            feed.write(f"session {info['id']} ({info['label']}) started · mode "
                       f"{info.get('autonomy', 'supervised')}")
        elif t == "session.status":
            feed.write(f"session {f['session_id']}: {f['status']}")
        elif t == "blocked":
            feed.write(f"🔒 {f['surface']} is blocked until Stage {f['stage']}")
        elif t == "error":
            feed.write(f"[red]error:[/red] {f['message']} {f.get('detail', '')}")

    def _render_board(self, snap: dict) -> None:
        tree = self.query_one("#board-tree", Tree)
        tree.clear()
        projects: dict[str, list] = {}
        for task in snap.get("tasks", []):
            projects.setdefault(task["project_id"], []).append(task)
        proj_names = {p["id"]: p["name"] for p in snap.get("projects", [])}
        for pid, tasks in projects.items():
            node = tree.root.add(proj_names.get(pid, pid or "(no project)"),
                                 expand=True)
            for task in tasks:
                label = f"[{task['status']}] P{task['priority']} {task['title']}"
                leaf = node.add_leaf(label)
                leaf.data = task["id"]
        tree.root.expand()

    def _render_approvals(self) -> None:
        log = self.query_one("#approvals-log", RichLog)
        log.clear()
        if not self.pending_approvals:
            log.write("no pending approvals — native prompts (SDK can_use_tool, "
                      "ACP request_permission) land here; ctrl+y allows, ctrl+n "
                      "denies the oldest")
            return
        for r in self.pending_approvals:
            opts = " / ".join(o["id"] for o in r["options"])
            log.write(f"{r['id']}  [{r['session_id']}]  {r['title']}  ({opts})")
            if r.get("detail"):
                log.write(f"    {r['detail']}")

    @staticmethod
    def _pick_option(request: dict, allow: bool) -> str:
        """The uniform prompt carries driver-native options; map y/n onto them by
        their ACP-style kind, falling back to first (allow) / last (deny)."""
        prefix = "allow" if allow else "reject"
        for o in request["options"]:
            if o.get("kind", "").startswith(prefix):
                return o["id"]
        return request["options"][0 if allow else -1]["id"]

    async def _answer_oldest(self, allow: bool) -> None:
        if not self.pending_approvals:
            return
        request = self.pending_approvals[0]
        await self._send({"type": "answer.native", "session_id": request["session_id"],
                          "request_id": request["id"],
                          "value": self._pick_option(request, allow)})

    def _render_quota(self, f: dict) -> None:
        # "unavailable" here means the READ SURFACE isn't wired (ADR-0005), not
        # that the worker is down — render it as a dash, never as scary text.
        gauge = self.query_one("#quota", Static)
        if f["type"] == "quota.snapshot":
            parts, any_unread = [], False
            for worker, st in f["workers"].items():
                if st.get("limited"):
                    mark = "LIMITED"
                elif st.get("window_5h") == "unavailable":
                    mark, any_unread = "–", True
                else:
                    mark = str(st.get("window_5h"))
                parts.append(f"{worker} {mark}")
            note = "  (– = usage reader pending, ADR-0005)" if any_unread else ""
            gauge.update("quota  " + "  ".join(parts) + note)
        elif f.get("limited"):
            gauge.update(f"quota  {f['worker']}: LIMITED (backoff)")

    # --- actions ------------------------------------------------------------

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        if getattr(event.node, "data", None):
            self.selected_task = event.node.data
            # selecting a task pulls its full detail (spec 012)
            asyncio.create_task(self._send(
                {"type": "task.detail", "task_id": event.node.data}))
            asyncio.create_task(self._send(
                {"type": "agents.tree", "task_id": event.node.data}))

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "takeover" and self.selected_session:
            await self._send({"type": "agent.send",
                              "session_id": self.selected_session,
                              "text": event.value + "\r"})
            event.input.value = ""

    async def action_approve(self) -> None:
        if self.review_task:
            await self._send({"type": "review.approve", "task_id": self.review_task})
            self.review_task = None

    async def action_reject(self) -> None:
        if self.review_task:
            await self._send({"type": "review.reject", "task_id": self.review_task,
                              "feedback": "rejected from cockpit (add detail via CLI "
                                          "`podium reject <id> <feedback>`)"})
            self.review_task = None

    async def action_allow(self) -> None:
        await self._answer_oldest(True)

    async def action_deny(self) -> None:
        await self._answer_oldest(False)

    async def action_dispatch(self) -> None:
        await self._send({"type": "run.next"})
        self.query_one("#feed-log", RichLog).write("dispatch requested (run next)")

    async def action_requeue(self) -> None:
        """Put the selected blocked task back in the queue (its retry resumes)."""
        if self.selected_task:
            await self._send({"type": "task.requeue",
                              "task_id": self.selected_task})
            await self._send({"type": "task.detail",
                              "task_id": self.selected_task})

    def action_focus_takeover(self) -> None:
        self.query_one("#takeover", Input).focus()

    def _tmux_other_pane(self) -> str | None:
        """The pane that is NOT the cockpit's, when running inside tmux."""
        import os
        import subprocess
        if not os.environ.get("TMUX"):
            return None
        me = os.environ.get("TMUX_PANE", "")
        panes = subprocess.run(
            ["tmux", "list-panes", "-F", "#{pane_id}"],
            capture_output=True, text=True).stdout.split()
        others = [p for p in panes if p != me]
        return others[0] if others else None

    async def action_open_in_claude(self) -> None:
        """One-key takeover: suspend the cockpit and hand the terminal to the real
        Claude UI for this session (its own worktree, its own conversation). Exit
        Claude and you land back in the cockpit — the session keeps running."""
        sid = self.selected_session
        feed = self.query_one("#feed-log", RichLog)
        if sid is None:
            feed.write("no session selected")
            return
        frames = await self._rpc({"type": "sessions.list"})
        rec = next((s for f in frames if f["type"] == "sessions.snapshot"
                    for s in f["sessions"] if s["id"] == sid), None)
        if rec is None:
            feed.write(f"session {sid} not found on the daemon")
            return
        argv = ["claude", "--resume", rec["resume_key"]] if rec.get("resume_key") \
            else ["claude", "--continue"]
        cwd = rec.get("cwd")
        target = self._tmux_other_pane()
        if target:
            # Inside tmux: drive the OTHER pane. Never steal the cockpit's own pane
            # (dogfood: ctrl+o replaced the left pane instead of the right).
            subprocess.run(["tmux", "respawn-pane", "-k", "-t", target,
                            "-c", cwd or ".", *argv])
            feed.write(f"opened {sid} in pane {target} — ctrl+b o to switch panes")
            return
        # Outside tmux: a detached terminal if we have one, else take this terminal
        for term in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
            if shutil.which(term):
                subprocess.Popen([term, "-e", *argv], cwd=cwd,
                                 start_new_session=True)
                feed.write(f"opened {sid} in a new {term} window")
                return
        feed.write(f"no tmux pane or terminal available — run: "
                   f"cd {cwd} && {' '.join(argv)}")

    async def action_change_mode(self) -> None:
        """Modes are not fixed at spawn: switch a live session's permission mode."""
        sid = self.selected_session
        if sid is None:
            return
        labels = ["supervised — ask me before commands",
                  "autonomous — run tools, don't ask",
                  "bypass — ask nothing at all"]
        modes = ["supervised", "autonomous", "bypass"]

        def picked(choice: int | None):
            if choice:
                asyncio.create_task(self._send(
                    {"type": "session.mode", "session_id": sid,
                     "mode": modes[choice - 1]}))
                info = self.session_info.setdefault(sid, {})
                info["autonomy"] = modes[choice - 1]
                self._update_session_bar()

        self.push_screen(QuestionDialog(
            f"session {sid}", "Permission mode for this running session "
            "(takes effect immediately):", labels), picked)

    async def _rpc(self, frame: dict, reply_type: str | None = None) -> list[dict]:
        """One-shot request on a side connection (the main socket is a live stream)."""
        out = []
        async with websockets.connect(CONFIG.ws_url, max_size=16 * 1024 * 1024) as ws:
            await ws.recv(); await ws.recv()          # hello + snapshot
            await ws.send(protocol.dumps(frame))
            async with asyncio.timeout(5):
                while True:
                    reply = protocol.loads(await ws.recv())
                    if reply["type"] in ("output", "task.updated", "narration",
                                         "session.status"):
                        continue
                    out.append(reply)
                    break
        return out

    def action_focus_board(self) -> None:
        self.query_one("#board-tree", Tree).focus()

    def action_back(self) -> None:
        """Up one level: session → task → board (spec 013)."""
        feed = self.query_one("#feed-log", RichLog)
        if self.selected_session:
            self.selected_session = None
            self._update_session_bar()
            feed.write("← back to task")
        elif self.selected_task:
            self.selected_task = None
            feed.write("← back to board")
        self.query_one("#board-tree", Tree).focus()

    async def action_send_escape(self) -> None:
        """Esc goes to the live session (interrupt), like in the real CLI."""
        if self.selected_session:
            await self._send({"type": "agent.send",
                              "session_id": self.selected_session, "text": "\x1b"})


def run() -> None:
    Cockpit().run()


if __name__ == "__main__":
    run()
