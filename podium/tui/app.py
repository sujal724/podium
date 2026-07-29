"""The Podium cockpit (Textual) — the visibility invariant as a screen.

Panes: board (full hierarchy), live session (PTY stream + takeover input), review
(diff + approve/reject), approval inbox, native approvals (the uniform prompt —
y=allow / n=deny the oldest), quota gauge, event feed — and one explicit `blocked`
pane per not-yet-live surface (decision 44), stage-labelled, never hidden.

Connects to podiumd over the wire protocol like any other client.
"""

import asyncio

import websockets
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import (Footer, Header, Input, Label, RichLog, Static,
                             TabbedContent, TabPane, Tree)

from podium import protocol
from podium.config import CONFIG
from podium.surfaces import blocked_surfaces


class Cockpit(App):
    TITLE = "Podium"
    CSS = """
    #board { width: 34%; border: solid $primary; }
    #main  { width: 66%; }
    #session-log, #review-log, #feed-log { border: solid $primary; height: 1fr; }
    #quota { height: 3; border: solid $warning; }
    #takeover { dock: bottom; }
    .blocked { color: $text-muted; padding: 1 2; }
    """
    BINDINGS = [
        Binding("a", "approve", "approve"),
        Binding("r", "reject", "reject"),
        Binding("y", "allow", "allow prompt"),
        Binding("n", "deny", "deny prompt"),
        Binding("d", "dispatch", "run next"),
        Binding("q", "quit", "quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.ws = None
        self.selected_session: str | None = None
        self.selected_task: str | None = None
        self.review_task: str | None = None
        self.pending_approvals: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="board"):
                yield Label("work board")
                yield Tree("workspaces", id="board-tree")
                yield Static("quota: …", id="quota")
            with Vertical(id="main"):
                with TabbedContent():
                    with TabPane("session", id="tab-session"):
                        yield RichLog(id="session-log", highlight=False, wrap=False)
                        yield Input(placeholder="takeover — text goes to the live "
                                                "session (Enter sends)", id="takeover")
                    with TabPane("review", id="tab-review"):
                        yield RichLog(id="review-log", highlight=True, wrap=False)
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
        self.run_worker(self._connect(), exclusive=True)

    async def _connect(self) -> None:
        feed = self.query_one("#feed-log", RichLog)
        try:
            self.ws = await websockets.connect(CONFIG.ws_url)
        except OSError as e:
            feed.write(f"cannot reach podiumd at {CONFIG.ws_url}: {e}")
            return
        await self._send({"type": "work.list"})
        await self._send({"type": "quota.query"})
        await self._send({"type": "task.inbox"})
        await self._send({"type": "interaction.pending"})
        async for raw in self.ws:
            self._on_frame(protocol.loads(raw))

    async def _send(self, frame: dict) -> None:
        if self.ws:
            await self.ws.send(protocol.dumps(frame))

    # --- incoming frames ----------------------------------------------------

    def _on_frame(self, f: dict) -> None:
        t = f["type"]
        feed = self.query_one("#feed-log", RichLog)
        if t == "output":
            if self.selected_session in (None, f["session_id"]):
                self.selected_session = self.selected_session or f["session_id"]
                self.query_one("#session-log", RichLog).write(
                    Text.from_ansi(f["text"]), scroll_end=True)
        elif t == "snapshot":
            for sid, backlog in f.get("backlogs", {}).items():
                self.selected_session = sid
                self.query_one("#session-log", RichLog).write(Text.from_ansi(backlog))
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
            feed.write(f"❓ {f['request']['title']} — approvals tab, y/n")
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
            self.selected_session = f["session"]["id"]
            self.query_one("#session-log", RichLog).clear()
            feed.write(f"session {f['session']['id']} ({f['session']['label']}) started")
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
                      "ACP request_permission) land here; y=allow n=deny the oldest")
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
        gauge = self.query_one("#quota", Static)
        if f["type"] == "quota.snapshot":
            parts = []
            for worker, st in f["workers"].items():
                mark = "LIMITED" if st.get("limited") else st.get("window_5h")
                parts.append(f"{worker}: {mark}")
            gauge.update("quota  " + "  ".join(parts))
        else:
            gauge.update(f"quota  {f['worker']}: "
                         + ("LIMITED" if f.get("limited") else "updated"))

    # --- actions ------------------------------------------------------------

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        if getattr(event.node, "data", None):
            self.selected_task = event.node.data

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


def run() -> None:
    Cockpit().run()


if __name__ == "__main__":
    run()
