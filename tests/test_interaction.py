"""B1: worker dialogs become uniform operator questions; answers translate to the
keystrokes the worker's own dialog expects; Podium never answers by itself."""

from podium.interaction import InteractionLayer, canonicalize
from podium.sink import Sink

# a trust prompt as PTYs actually deliver it: escapes interleaved mid-word
MANGLED_TRUST = (
    "\x1b[2J\x1b[1;1HQuick safety check\r\n"
    "\x1b[1m❯ 1. Yes, I \x1b[0mt\x1b[1mrust this folder\x1b[0m\r\n"
    "  2. No, exit\r\n"
)


def test_canonicalize_strips_ansi_and_whitespace():
    assert "itrustthisfolder" in canonicalize(MANGLED_TRUST)


def _layer():
    sink = Sink()
    frames = []
    sink.tap(frames.append)
    return InteractionLayer(sink), frames


def test_trust_prompt_becomes_question_once():
    layer, frames = _layer()
    # split across reads mid-word
    assert layer.scan("s_1", "claude", MANGLED_TRUST[:30]) is None
    q = layer.scan("s_1", "claude", MANGLED_TRUST[30:])
    assert q is not None and q.pattern.id == "claude.trust"
    emitted = [f for f in frames if f["type"] == "question"]
    assert len(emitted) == 1
    assert emitted[0]["question"]["choices"][0].startswith("Yes")
    # redraws of the same dialog never re-ask
    assert layer.scan("s_1", "claude", MANGLED_TRUST) is None


def test_pattern_is_worker_scoped():
    layer, _ = _layer()
    assert layer.scan("s_2", "gemini", MANGLED_TRUST) is None


def test_answer_maps_index_label_and_fallback():
    layer, _ = _layer()
    q = layer.scan("s_3", "claude", MANGLED_TRUST)
    assert layer.answer_bytes(q.id, "1") == "1\r"
    q2 = layer.scan("s_4", "claude", MANGLED_TRUST)
    assert layer.answer_bytes(q2.id, "No — exit the session") == "2\r"
    # unknown question id → generic line answer
    assert layer.answer_bytes("nope", "y") == "y\r"


def test_forget_session_clears_state():
    layer, _ = _layer()
    q = layer.scan("s_5", "claude", MANGLED_TRUST)
    layer.forget_session("s_5")
    assert q.id not in layer.pending
    assert layer.scan("s_5", "claude", MANGLED_TRUST) is not None  # can re-ask fresh
