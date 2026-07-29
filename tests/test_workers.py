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


def test_bypass_mode_argv_and_guard(tmp_path):
    """Bypass asks nothing, but the peer-call guard hook still runs — the
    decision-50 policy must not depend on the permission system."""
    import json as _json
    sess = ClaudeWorker().make_session("s_b", None, str(tmp_path), "go",
                                       autonomy="bypass")
    assert "--dangerously-skip-permissions" in sess.argv
    assert "--permission-mode" not in sess.argv
    settings = _json.loads((tmp_path / ".podium" / "settings.json").read_text())
    pre = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "podium.guard" in pre
    # supervised keeps the prompting permission mode
    sess2 = ClaudeWorker().make_session("s_s", None, str(tmp_path), "go")
    assert "--permission-mode" in sess2.argv
    assert "--dangerously-skip-permissions" not in sess2.argv


def test_guard_blocks_peer_calls_only():
    from podium.guard import blocked_binary
    for cmd in ("claude -p 'hi'", "sudo claude", "/usr/bin/gemini --acp",
                "FOO=1 codex exec", "ls; claude --resume x", "npx claude",
                "echo hi && podiumd"):
        assert blocked_binary(cmd), cmd
    for cmd in ("pytest -q", "echo claude is a name", "git commit -m 'claude'",
                "cat ~/.claude/settings.json", "grep -r claude ."):
        assert blocked_binary(cmd) is None, cmd


def test_guard_exit_codes(tmp_path, monkeypatch, capsys):
    import io
    from podium import guard
    log = tmp_path / "hooks.jsonl"
    event = '{"hook_event_name":"PreToolUse","tool_name":"Bash",' \
            '"tool_input":{"command":"claude -p hi"}}'
    monkeypatch.setattr("sys.stdin", io.StringIO(event))
    assert guard.main(["guard", str(log)]) == 2          # blocked
    assert "Blocked by Podium policy" in capsys.readouterr().err
    assert log.read_text().strip() == event              # still logged
    ok = '{"hook_event_name":"PreToolUse","tool_name":"Bash",' \
         '"tool_input":{"command":"pytest -q"}}'
    monkeypatch.setattr("sys.stdin", io.StringIO(ok))
    assert guard.main(["guard", str(log)]) == 0          # allowed


def test_peer_shims_block_at_os_level(tmp_path):
    """Verified need: bypass mode ignores hook denials, so decision 50 is enforced
    by shims on PATH — no permission setting can switch that off."""
    import subprocess
    bin_dir = policy.write_peer_shims(str(tmp_path))
    env = policy.worker_env(str(tmp_path))
    assert env["PATH"].startswith(str(bin_dir))
    for name in ("claude", "gemini", "codex"):
        proc = subprocess.run([str(bin_dir / name), "--version"],
                              capture_output=True, text=True)
        assert proc.returncode == 126
        assert "Blocked by Podium policy" in proc.stderr


def test_worker_env_strips_child_session_and_keys(monkeypatch, tmp_path):
    """Inherited CLAUDE_CODE_* markers silently disable the CLI's transcript
    saving (seen in a real run), which breaks session visibility and resume."""
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    env = policy.worker_env(str(tmp_path))
    assert "CLAUDE_CODE_CHILD_SESSION" not in env
    assert "CLAUDECODE" not in env and "ANTHROPIC_API_KEY" not in env
