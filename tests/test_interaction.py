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


PERMISSION_PROMPT = (
    "Bash command\n  python -m pytest tests/ -x -q\n\n"
    "This command requires approval\n\nDo you want to proceed?\n"
    "\x1b[1m❯ 1. Yes\x1b[0m\n  2. Yes, and don't ask again for: python -m pytest\n"
    "  3. No\n\nEsc to cancel\n"
)


def test_permission_prompt_offers_the_workers_own_options():
    """Dogfood: an approval prompt must surface as a dialog with exactly the
    options the worker offered, and the answer is that option's number."""
    layer, frames = _layer()
    q = layer.scan("s_p", "claude", PERMISSION_PROMPT)
    assert q is not None and q.pattern.id == "claude.permission"
    assert list(q.choices)[0] == "Yes"
    assert len(q.choices) == 3
    assert layer.answer_bytes(q.id, "1") == "1\r"
    q2 = layer.scan("s_p2", "claude", PERMISSION_PROMPT)
    assert layer.answer_bytes(q2.id, "No") == "3\r"


def test_permission_rearms_for_the_next_prompt():
    """Each approval is a separate decision: once the prompt leaves the screen the
    pattern re-arms so the next one asks again."""
    layer, _ = _layer()
    assert layer.scan("s_r", "claude", PERMISSION_PROMPT) is not None
    assert layer.scan("s_r", "claude", PERMISSION_PROMPT) is None      # same prompt
    layer.scan("s_r", "claude", "working…\n" * 400)                    # prompt scrolls off
    assert layer.scan("s_r", "claude", PERMISSION_PROMPT) is not None  # next one asks


def test_choice_parsing_ignores_noise():
    from podium.interaction import parse_choices
    assert parse_choices("1. Yes\n2. No\nrandom 7. text later") == ["Yes", "No"]
    assert parse_choices("no options here") == []
