"""The Podium cockpit (Textual) — the visibility invariant as a screen.

Panes: board (full hierarchy), live session (PTY stream + takeover input), review
(diff + approve/reject), approval inbox, quota gauge, event feed — and one explicit
`blocked` pane per not-yet-live surface (decision 44), stage-labelled, never hidden.

Connects to podiumd over the wire protocol like any other client.
"""

import asyncio

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
from podium.tui.term import TerminalEmulator, TerminalView


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
    #workers { height: 4; border: solid $secondary; }
    #quota { height: 3; border: solid $warning; }
    #takeover { dock: bottom; }
    .blocked { color: $text-muted; padding: 1 2; }
    """
    BINDINGS = [
        Binding("a", "approve", "approve"),
        Binding("r", "reject", "reject"),
        Binding("d", "dispatch", "run next"),
        Binding("q", "quit", "quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.ws = None
        self.selected_session: str | None = None
        self.selected_task: str | None = None
        self.review_task: str | None = None
        self.emulators: dict[str, TerminalEmulator] = {}

    def _emulator(self, sid: str) -> TerminalEmulator:
        if sid not in self.emulators:
            self.emulators[sid] = TerminalEmulator()
        return self.emulators[sid]

    def _select_session(self, sid: str) -> None:
        self.selected_session = sid
        view = self.query_one("#session-view", TerminalView)
        view.on_pty_resize = lambda rows, cols: asyncio.create_task(self._send(
            {"type": "resize", "session_id": sid, "rows": rows, "cols": cols}))
        view.attach(self._emulator(sid))

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
                        yield TerminalView(id="session-view")
                        yield Input(placeholder="takeover — text goes to the live "
                                                "session (Enter sends)", id="takeover")
                    with TabPane("review", id="tab-review"):
                        yield RichLog(id="review-log", highlight=True, wrap=False)
                    with TabPane("inbox", id="tab-inbox"):
                        yield RichLog(id="inbox-log", wrap=True)
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
            sid = f["session_id"]
            self._emulator(sid).feed(f["text"])
            if self.selected_session is None:
                self._select_session(sid)
            elif sid == self.selected_session:
                self.query_one("#session-view", TerminalView).refresh()
        elif t == "snapshot":
            for sid, backlog in f.get("backlogs", {}).items():
                self._emulator(sid).feed(backlog)
                self._select_session(sid)
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
        elif t in ("quota.update", "quota.snapshot"):
            self._render_quota(f)
        elif t == "narration":
            feed.write(f"• {f['text']}")
        elif t == "session.created":
            self._select_session(f["session"]["id"])
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

    async def action_dispatch(self) -> None:
        await self._send({"type": "run.next"})


def run() -> None:
    Cockpit().run()


if __name__ == "__main__":
    run()
