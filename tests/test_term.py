"""C1–C4: the session pane is a terminal, not a log."""

from podium.tui.term import TerminalEmulator, _rich_color


def test_repaints_render_once():
    emu = TerminalEmulator(cols=40, rows=6)
    frame = "\x1b[H\x1b[2Jclaude> working on task\r\n[tool] reading files\r\n"
    for _ in range(25):
        emu.feed(frame)
    joined = "\n".join(emu.plain_lines())
    assert joined.count("claude> working on task") == 1
    assert joined.count("[tool] reading files") == 1


def test_in_place_progress_shows_latest():
    emu = TerminalEmulator(cols=40, rows=4)
    emu.feed("\x1b[H\x1b[2J")
    for pct in (10, 40, 99):
        emu.feed(f"\x1b[1;1Hprogress: {pct}%   ")
    text = "\n".join(emu.plain_lines())
    assert "progress: 99%" in text
    assert "progress: 10%" not in text


def test_resize_reshapes_grid():
    emu = TerminalEmulator(cols=120, rows=40)
    emu.resize(10, 50)
    assert emu.size == (10, 50)
    emu.feed("x" * 60)  # wraps on the new width
    assert emu.plain_lines()[0] == "x" * 50


def test_bright_colors_and_garbage_never_crash():
    """Regression: pyte reports brights as 'brightblue' (rich wants 'bright_blue');
    one unknown name must degrade, not take down the app."""
    from podium.tui.term import _style
    assert _rich_color("brightblue") == "bright_blue"
    assert _rich_color("brightbrown") == "bright_yellow"
    assert str(_style("brightblue", "brightblack", True, False, False, False)) != ""
    assert _style("not-a-color-at-all", "??", False, False, False, False) is not None
    # a real bright-color escape renders end to end
    emu = TerminalEmulator(cols=20, rows=2)
    emu.feed("\x1b[94mbright blue text\x1b[0m")
    assert emu.rich_lines()[0].plain.startswith("bright blue text")


def test_color_and_attr_mapping():
    emu = TerminalEmulator(cols=20, rows=2)
    emu.feed("\x1b[1;31mred bold\x1b[0m plain")
    line = emu.rich_lines()[0]
    spans_styles = [str(s.style) for s in line.spans]
    assert any("red" in s and "bold" in s for s in spans_styles)
    assert _rich_color("brown") == "yellow"
    assert _rich_color("aabbcc") == "#aabbcc"
    assert _rich_color("default") is None
