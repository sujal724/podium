"""A7: posture honesty — Codex blocked-with-hint, API-key warning + stripping,
peer-call deny policy generated for Claude sessions."""

import json

from podium import policy
from podium.workers.claude import ClaudeWorker
from podium.workers.codex import CodexWorker


def test_codex_blocked_with_hint():
    av = CodexWorker().available()
    if not av.ok:
        assert av.hint


def test_claude_api_key_warning(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-nope")
    av = ClaudeWorker().available()
    assert "ANTHROPIC_API_KEY" in av.warning


def test_claude_session_strips_api_keys_and_denies_peers(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-nope")
    sess = ClaudeWorker().make_session("s_x", None, str(tmp_path), "do it")
    assert "ANTHROPIC_API_KEY" not in sess.env
    settings = json.loads((tmp_path / ".podium" / "settings.json").read_text())
    assert "Bash(gemini *)" in settings["permissions"]["deny"]
    assert "Bash(claude *)" in settings["permissions"]["deny"]
    assert "PreToolUse" in settings["hooks"]
    assert sess.argv[-1] == "do it"


def test_munge_matches_claude_layout():
    assert policy.munge_project_path("/home/x/.podium/work/worktrees/t_a") \
        == "-home-x--podium-work-worktrees-t-a"


def test_mirror_session_symlinks_into_repo_project(tmp_path):
    base = tmp_path / "projects"
    wt = tmp_path / "wt"
    repo = tmp_path / "repo"
    wt.mkdir(); repo.mkdir()
    src_dir = base / policy.munge_project_path(str(wt))
    src_dir.mkdir(parents=True)
    (src_dir / "uuid-1.jsonl").write_text("{}")
    dest = policy.mirror_session(str(wt), str(repo), "uuid-1", projects_base=base)
    assert dest is not None and dest.is_symlink()
    assert dest.parent.name == policy.munge_project_path(str(repo))
    assert policy.mirror_session(str(wt), str(repo), "uuid-1",
                                 projects_base=base) == dest  # idempotent
    assert policy.mirror_session(str(wt), str(repo), "missing",
                                 projects_base=base) is None


def test_hook_events_tail(tmp_path):
    log = policy.hooks_path(str(tmp_path))
    log.parent.mkdir(parents=True)
    log.write_text('{"hook_event_name":"PreToolUse","tool_name":"Bash"}\n'
                   "not json\n"
                   '{"hook_event_name":"Stop"}\n')
    events, offset = policy.read_hook_events(str(tmp_path))
    assert [e["hook_event_name"] for e in events] == ["PreToolUse", "Stop"]
    events2, _ = policy.read_hook_events(str(tmp_path), offset)
    assert events2 == []
