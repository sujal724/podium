"""Terminal emulation for the session pane (spec 003).

Full-screen CLIs (Claude Code's ink UI) repaint constantly with cursor-positioning
escapes; appending each repaint to a log renders as endless repetition. The pane must
behave like a terminal: maintain a screen grid, apply the escapes, show the *current*
screen. `TerminalEmulator` is the pure, testable core (pyte-backed); `TerminalView`
is its Textual widget.
"""

from functools import lru_cache

import pyte
from rich.errors import StyleSyntaxError
from rich.color import ColorParseError
from rich.style import Style
from rich.text import Text
from textual.widget import Widget

# pyte color names → rich colors: "brown" is the ANSI yellow slot, brights are
# "brightblue"-style in pyte but "bright_blue" in rich; 256/truecolor arrive as
# bare hex strings.
_COLOR_FIX = {"brown": "yellow", "default": None}


def _rich_color(c: str | None) -> str | None:
    if c is None:
        return None
    mapped = _COLOR_FIX.get(c, c)
    if mapped is None:
        return None
    if len(mapped) == 6 and all(ch in "0123456789abcdef" for ch in mapped):
        return f"#{mapped}"
    if mapped.startswith("bright") and not mapped.startswith("bright_"):
        base = mapped[len("bright"):]
        return f"bright_{'yellow' if base == 'brown' else base}"
    return mapped


@lru_cache(maxsize=4096)
def _style(fg, bg, bold, italics, underscore, reverse) -> Style:
    """One cached Style per attribute combo — and unparseable colors degrade to
    unstyled text instead of crashing the app (a PTY can emit anything)."""
    try:
        return Style(color=_rich_color(fg), bgcolor=_rich_color(bg), bold=bold,
                     italic=italics, underline=underscore, reverse=reverse)
    except (ColorParseError, StyleSyntaxError):
        return Style(bold=bold, italic=italics, underline=underscore,
                     reverse=reverse)


HISTORY_LINES = 5000


class TerminalEmulator:
    """pyte HistoryScreen-backed: the visible grid PLUS scrollback, so transcript
    that scrolls off the live screen is kept, not lost (spec 003 rev 2)."""

    def __init__(self, cols: int = 120, rows: int = 40) -> None:
        self.screen = pyte.HistoryScreen(cols, rows, history=HISTORY_LINES,
                                         ratio=0.5)
        self.stream = pyte.Stream(self.screen)

    def feed(self, text: str) -> None:
        self.stream.feed(text)

    def resize(self, rows: int, cols: int) -> None:
        if rows > 0 and cols > 0 and (rows, cols) != (self.screen.lines,
                                                      self.screen.columns):
            self.screen.resize(rows, cols)

    @property
    def size(self) -> tuple[int, int]:
        return self.screen.lines, self.screen.columns

    def _rich_row(self, buf) -> Text:
        line = Text()
        run, run_style = [], None
        for col in range(self.screen.columns):
            ch = buf[col]
            style = _style(ch.fg, ch.bg, ch.bold, ch.italics, ch.underscore,
                           ch.reverse)
            if style != run_style and run:
                line.append("".join(run), run_style)
                run = []
            run_style = style
            run.append(ch.data)
        if run:
            line.append("".join(run), run_style)
        line.rstrip()
        return line

    def plain_lines(self) -> list[str]:
        """Visible screen as plain text (tests + fallbacks)."""
        return [line.rstrip() for line in self.screen.display]

    def rich_lines(self) -> list[Text]:
        """Visible screen with colors/attributes, one Text per row."""
        return [self._rich_row(self.screen.buffer[row])
                for row in range(self.screen.lines)]

    def rich_full(self) -> list[Text]:
        """Scrollback + visible screen — what the scrollable pane renders."""
        out = [self._rich_row(row) for row in self.screen.history.top]
        out += self.rich_lines()
        while out and not out[-1].plain.strip():
            out.pop()
        return out

    def plain_full(self) -> list[str]:
        return [t.plain for t in self.rich_full()]


class TerminalView(Widget, can_focus=False):
    """Renders a TerminalEmulator's scrollback + live screen. Lives inside a
    scrollable container (height: auto), so the operator can wheel back through
    the whole transcript; the app keeps it anchored to the bottom while streaming
    and propagates the *viewport* size to the daemon's PTY."""

    DEFAULT_CSS = """
    TerminalView { height: auto; }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.emulator: TerminalEmulator | None = None

    def attach(self, emulator: TerminalEmulator) -> None:
        self.emulator = emulator
        self.refresh(layout=True)

    def render(self):
        if self.emulator is None:
            return Text("no session selected — dispatch a task or spawn a session")
        return Text("\n").join(self.emulator.rich_full())

    def get_content_height(self, container, viewport, width) -> int:
        if self.emulator is None:
            return 1
        return max(1, len(self.emulator.rich_full()))
