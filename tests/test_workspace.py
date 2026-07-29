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


def test_approve_works_when_checkout_is_elsewhere(tmp_path, repo):
    """New contract (spec 008): the merge runs in whichever worktree hosts the base
    ref — or a scratch one — so approving never depends on where your checkout sits.
    Required for stacked branches, where the base is another task's branch."""
    wm = WorkspaceManager(str(tmp_path / "work"))
    wm.create(str(repo), "t_4", "main")
    _commit_in(tmp_path / "work" / "worktrees" / "t_4")
    git(repo, "checkout", "-q", "-b", "elsewhere")
    wm.approve(str(repo), "t_4", "main")
    merged = subprocess.run(["git", "-C", str(repo), "log", "--oneline", "main"],
                            capture_output=True, text=True).stdout
    assert "add f.txt" in merged


def test_branch_tree_stacks_on_parent(tmp_path, repo):
    """A subtask branches off its PARENT's branch and merges back into it — the
    branch structure mirrors the task tree (spec 008)."""
    wm = WorkspaceManager(str(tmp_path / "work"))
    parent_branch = wm.ensure_branch(str(repo), "t_p", "main")   # parent never ran
    assert parent_branch == "task/t_p"
    wm.create(str(repo), "t_p", "main")
    _commit_in(tmp_path / "work" / "worktrees" / "t_p", "parent.txt")
    # child b and child c both branch off the parent's branch
    for child in ("t_b", "t_c"):
        wm.create(str(repo), child, parent_branch)
        _commit_in(tmp_path / "work" / "worktrees" / child, f"{child}.txt")
        assert (tmp_path / "work" / "worktrees" / child / "parent.txt").exists()
        # the child's diff shows only ITS work, not the parent's
        assert f"{child}.txt" in wm.diff(str(repo), child, parent_branch)
        assert "parent.txt" not in wm.diff(str(repo), child, parent_branch)
    wm.approve(str(repo), "t_b", parent_branch)        # merges into the parent branch
    log = subprocess.run(["git", "-C", str(repo), "log", "--oneline", parent_branch],
                         capture_output=True, text=True).stdout
    assert "add t_b.txt" in log and "add t_c.txt" not in log
    assert "add t_b.txt" not in subprocess.run(
        ["git", "-C", str(repo), "log", "--oneline", "main"],
        capture_output=True, text=True).stdout          # main untouched until parent lands
