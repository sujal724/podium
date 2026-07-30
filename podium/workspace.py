"""WorkspaceManager — one isolated git worktree + `task/<id>` branch per dispatched
task. Approve = merge into the project base branch (the operator is the merge gate,
decision 45 — nothing merges without this call). Reject = worktree removed, branch kept
for inspection, feedback travels with the task.
"""

import contextlib
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def _git(repo: str | Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


class WorkspaceManager:
    def __init__(self, workdir: str) -> None:
        self.workdir = Path(workdir).expanduser()
        self.workdir.mkdir(parents=True, exist_ok=True)

    def _wt_path(self, task_id: str) -> Path:
        return self.workdir / "worktrees" / task_id

    def branch(self, task_id: str) -> str:
        return f"task/{task_id}"

    def create(self, repo_root: str, task_id: str, base_ref: str = "main") -> str:
        """Worktree on a fresh task branch off `base_ref` — the project base branch,
        or the parent task's branch when this is a subtask."""
        path = self._wt_path(task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            self.remove(repo_root, task_id)
        branch = self.branch(task_id)
        existing = _git(repo_root, "branch", "--list", branch).strip()
        if existing:
            _git(repo_root, "worktree", "add", str(path), branch)
        else:
            _git(repo_root, "worktree", "add", "-b", branch, str(path), base_ref)
        return str(path)

    def diff(self, repo_root: str, task_id: str, base_ref: str = "main") -> str:
        """The reviewable diff: base → task branch (committed work only). For a
        subtask the base is its parent's branch, so the diff shows only its own work."""
        return _git(repo_root, "diff", f"{base_ref}...{self.branch(task_id)}")

    def has_commits(self, repo_root: str, task_id: str, base_ref: str = "main") -> bool:
        out = _git(repo_root, "rev-list", "--count",
                   f"{base_ref}..{self.branch(task_id)}")
        return int(out.strip()) > 0

    def ensure_branch(self, repo_root: str, task_id: str, base_ref: str) -> str:
        """Create a task's branch (off `base_ref`) without a worktree — so a subtask
        can branch off its parent's branch before the parent has ever run."""
        branch = self.branch(task_id)
        if not _git(repo_root, "branch", "--list", branch).strip():
            _git(repo_root, "branch", branch, base_ref)
        return branch

    def _worktree_on(self, repo_root: str, ref: str) -> str | None:
        """Which checkout (if any) currently has `ref` checked out — a merge must run
        there, since git forbids checking the same branch out twice."""
        out = _git(repo_root, "worktree", "list", "--porcelain")
        path = None
        for line in out.splitlines():
            if line.startswith("worktree "):
                path = line.split(" ", 1)[1]
            elif line.startswith("branch ") and path:
                if line.split(" ", 1)[1].rsplit("/", 1)[-1] == ref.rsplit("/", 1)[-1]:
                    return path
        return None

    def approve(self, repo_root: str, task_id: str, base_ref: str = "main") -> None:
        """Merge the task branch into its base — the project's base branch for a
        top-level task, the PARENT TASK's branch for a subtask (branches mirror the
        task tree). A conflict raises GitError: surfaced, never auto-resolved."""
        branch = self.branch(task_id)
        host = self._worktree_on(repo_root, base_ref)
        temp = None
        if host is None:
            # nobody has the base checked out — borrow a scratch worktree for it
            temp = self.workdir / "merge" / task_id
            temp.parent.mkdir(parents=True, exist_ok=True)
            if temp.exists():
                _git(repo_root, "worktree", "remove", "--force", str(temp))
            _git(repo_root, "worktree", "add", str(temp), base_ref)
            host = str(temp)
        try:
            _git(host, "merge", "--no-ff", "--no-edit", branch)
        except GitError:
            with contextlib.suppress(GitError):
                _git(host, "merge", "--abort")
            raise
        finally:
            if temp is not None:
                _git(repo_root, "worktree", "remove", "--force", str(temp))
        self.remove(repo_root, task_id)
        _git(repo_root, "branch", "-D", branch)

    def reject(self, repo_root: str, task_id: str) -> None:
        """Drop the worktree; keep the branch for inspection / the retry to build on."""
        self.remove(repo_root, task_id)

    def remove(self, repo_root: str, task_id: str) -> None:
        path = self._wt_path(task_id)
        if path.exists():
            _git(repo_root, "worktree", "remove", "--force", str(path))
        _git(repo_root, "worktree", "prune")
