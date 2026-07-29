"""The Podium cockpit (Textual) — the visibility invariant as a screen.

Panes: board (full hierarchy), live session (PTY stream + takeover input), review
(diff + approve/reject), approval inbox, quota gauge, event feed — and one explicit
`blocked` pane per not-yet-live surface (decision 44), stage-labelled, never hidden.

Connects to podiumd over the wire protocol like any other client.
"""

import asyncio
import subprocess

import websockets
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
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
    #session-scroll { border: solid $primary; height: 1fr; }
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
        Binding("ctrl+d", "dispatch", "run next"),
        Binding("ctrl+t", "focus_takeover", "takeover"),
        Binding("ctrl+o", "open_in_claude", "open in claude"),
        Binding("ctrl+b", "focus_board", "board"),
        Binding("ctrl+q", "quit", "quit"),
        Binding("escape", "send_escape", "esc → session"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.ws = None
        self.selected_session: str | None = None
        self.selected_task: str | None = None
        self.review_task: str | None = None
        self.emulators: dict[str, TerminalEmulator] = {}
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
        mode_txt = ("AUTONOMOUS · runs tools without asking" if mode == "autonomous"
                    else "SUPERVISED · asks you before running commands")
        resumed = " · resumed" if info.get("resumed") else ""
        bar.update(f" {sid} · {info.get('label', '?')} · {mode_txt}{resumed}"
                   f" · ctrl+o open in claude · ctrl+t takeover")

    def _emulator(self, sid: str) -> TerminalEmulator:
        if sid not in self.emulators:
            self.emulators[sid] = TerminalEmulator()
        return self.emulators[sid]

    def _select_session(self, sid: str) -> None:
        self.selected_session = sid
        self.query_one("#session-view", TerminalView).attach(self._emulator(sid))
        self._sync_pty_size()
        self._update_session_bar()

    def _viewport(self) -> tuple[int, int] | None:
        """PTY size = the scroll container's viewport (rows) × content width
        (cols, minus scrollbar); the view itself is content-tall."""
        try:
            scroll = self.query_one("#session-scroll", VerticalScroll)
        except Exception:
            return None
        area = scroll.content_size
        if area.height <= 2 or area.width <= 10:
            return None
        return area.height, max(10, area.width - 1)

    def _sync_pty_size(self) -> None:
        vp = self._viewport()
        if vp is None:
            return
        rows, cols = vp
        sid = self.selected_session
        if sid is not None:
            self._emulator(sid).resize(rows, cols)
            asyncio.create_task(self._send(
                {"type": "resize", "session_id": sid, "rows": rows, "cols": cols}))
        # daemon remembers the pane size and spawns future PTYs to match, so
        # sessions never start at a mismatched width (the interleave glitch)
        asyncio.create_task(self._send(
            {"type": "winsize", "rows": rows, "cols": cols}))

    def on_resize(self, event) -> None:
        self._sync_pty_size()

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
                        with VerticalScroll(id="session-scroll"):
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
        # Focus the takeover input immediately: keys typed at the cockpit must
        # reach the live session, not the board tree (dogfood: answers to a
        # worker's approval prompt went nowhere).
        self.query_one("#takeover", Input).focus()
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
                scroll = self.query_one("#session-scroll", VerticalScroll)
                at_bottom = scroll.scroll_offset.y >= scroll.max_scroll_y - 1
                self.query_one("#session-view", TerminalView).refresh(layout=True)
                if at_bottom:  # follow the stream unless the operator scrolled up
                    self.call_after_refresh(
                        lambda: scroll.scroll_end(animate=False))
        elif t == "snapshot":
            for info in f.get("sessions", []):
                self.session_info[info["id"]] = info
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
        self.query_one("#feed-log", RichLog).write("dispatch requested (run next)")

    def action_focus_takeover(self) -> None:
        self.query_one("#takeover", Input).focus()

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
        feed.write(f"opening {sid} in claude ({' '.join(argv)})…")
        with self.suspend():
            subprocess.run(argv, cwd=cwd)
        self.refresh()

    async def _rpc(self, frame: dict, reply_type: str | None = None) -> list[dict]:
        """One-shot request on a side connection (the main socket is a live stream)."""
        out = []
        async with websockets.connect(CONFIG.ws_url) as ws:
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

    async def action_send_escape(self) -> None:
        """Esc goes to the live session (interrupt), like in the real CLI."""
        if self.selected_session:
            await self._send({"type": "agent.send",
                              "session_id": self.selected_session, "text": "\x1b"})


def run() -> None:
    Cockpit().run()


if __name__ == "__main__":
    run()
