"""A5: ledger attribution; rate-limit output flips the gauge to limited and blocks
dispatch. A4 (gauge honesty): no reader wired → `unavailable`, never a number."""

from podium.metering import Meter


def test_gauge_unavailable_without_reader(state, sink):
    m = Meter(state, sink)
    st = m.gauge.state("claude")
    assert st["window_5h"] == "unavailable"
    assert st["weekly"] == "unavailable"
    assert not st["limited"]
    assert m.gauge.may_dispatch("claude")


def test_rate_limit_backoff(state, sink):
    m = Meter(state, sink)
    hit = m.scan_output("claude", "s_1", "…usage limit reached, resets 03:00…")
    assert hit
    assert not m.gauge.may_dispatch("claude")
    assert m.gauge.state("claude")["limited"]
    # gauge event was emitted and persisted path exercised via sink tap in daemon
    assert m.gauge.may_dispatch("gemini")


def test_split_marker_across_reads(state, sink):
    m = Meter(state, sink)
    assert not m.scan_output("claude", "s_2", "…rate li")
    assert m.scan_output("claude", "s_2", "mit reached…")


def test_ledger_rows(state, sink):
    m = Meter(state, sink)
    m.record_session("s_1", "t_1", "claude", "pty", 100, 160, "ok")
    rows = state.query("SELECT * FROM metrics WHERE session_id='s_1'")
    assert rows and rows[0]["value"] == 60.0
    ledger = state.query("SELECT * FROM quota_ledger WHERE worker='claude'")
    assert len(ledger) == 1
