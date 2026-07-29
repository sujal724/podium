"""Worktree lifecycle: create, diff, approve-merge, reject-keep-branch."""

import subprocess

import pytest

from podium.workspace import GitError, WorkspaceManager
from tests.conftest import git


def _commit_in(worktree, name="f.txt"):
    (worktree / name).write_text("change\n")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-m", f"add {name}")


def test_create_diff_approve(tmp_path, repo):
    wm = WorkspaceManager(str(tmp_path / "work"))
    wt = wm.create(str(repo), "t_1", "main")
    assert not wm.has_commits(str(repo), "t_1")
    _commit_in(tmp_path / "work" / "worktrees" / "t_1")
    assert wm.has_commits(str(repo), "t_1")
    assert "+change" in wm.diff(str(repo), "t_1")
    wm.approve(str(repo), "t_1", "main")
    assert (repo / "f.txt").exists()
    branches = subprocess.run(["git", "-C", str(repo), "branch"],
                              capture_output=True, text=True).stdout
    assert "task/t_1" not in branches
    assert wt not in subprocess.run(["git", "-C", str(repo), "worktree", "list"],
                                    capture_output=True, text=True).stdout


def test_reject_keeps_branch(tmp_path, repo):
    wm = WorkspaceManager(str(tmp_path / "work"))
    wm.create(str(repo), "t_2", "main")
    _commit_in(tmp_path / "work" / "worktrees" / "t_2")
    wm.reject(str(repo), "t_2")
    branches = subprocess.run(["git", "-C", str(repo), "branch"],
                              capture_output=True, text=True).stdout
    assert "task/t_2" in branches
    # a re-dispatch reuses the surviving branch
    wt = wm.create(str(repo), "t_2", "main")
    assert (tmp_path / "work" / "worktrees" / "t_2" / "f.txt").exists()
    assert wt


def test_approve_conflict_surfaces(tmp_path, repo):
    wm = WorkspaceManager(str(tmp_path / "work"))
    wm.create(str(repo), "t_3", "main")
    wt = tmp_path / "work" / "worktrees" / "t_3"
    (wt / "README.md").write_text("task version\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-m", "task edit")
    (repo / "README.md").write_text("main version\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "main edit")
    with pytest.raises(GitError):
        wm.approve(str(repo), "t_3", "main")
    # base branch left clean (merge aborted)
    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                            capture_output=True, text=True).stdout
    assert status.strip() == ""


def test_approve_refuses_wrong_checkout(tmp_path, repo):
    wm = WorkspaceManager(str(tmp_path / "work"))
    wm.create(str(repo), "t_4", "main")
    _commit_in(tmp_path / "work" / "worktrees" / "t_4")
    git(repo, "checkout", "-b", "elsewhere")
    with pytest.raises(GitError):
        wm.approve(str(repo), "t_4", "main")
