"""WorkspaceManager — one isolated git worktree + `task/<id>` branch per dispatched
task. Approve = merge into the project base branch (the operator is the merge gate,
decision 45 — nothing merges without this call). Reject = worktree removed, branch kept
for inspection, feedback travels with the task.
"""

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

    def create(self, repo_root: str, task_id: str, base_branch: str = "main") -> str:
        """Worktree on a fresh task branch off the project base branch."""
        path = self._wt_path(task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            self.remove(repo_root, task_id)
        branch = self.branch(task_id)
        existing = _git(repo_root, "branch", "--list", branch).strip()
        if existing:
            _git(repo_root, "worktree", "add", str(path), branch)
        else:
            _git(repo_root, "worktree", "add", "-b", branch, str(path), base_branch)
        return str(path)

    def diff(self, repo_root: str, task_id: str, base_branch: str = "main") -> str:
        """The reviewable diff: base branch → task branch (committed work only)."""
        return _git(repo_root, "diff", f"{base_branch}...{self.branch(task_id)}")

    def has_commits(self, repo_root: str, task_id: str, base_branch: str = "main") -> bool:
        out = _git(repo_root, "rev-list", "--count",
                   f"{base_branch}..{self.branch(task_id)}")
        return int(out.strip()) > 0

    def approve(self, repo_root: str, task_id: str, base_branch: str = "main") -> None:
        """Merge the task branch into the base branch, then clean up. A conflict raises
        GitError — surfaced, never auto-resolved."""
        branch = self.branch(task_id)
        current = _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD").strip()
        if current != base_branch:
            raise GitError(
                f"repo checkout is on {current!r}, not base branch {base_branch!r}; "
                "refusing to merge from a detached position"
            )
        try:
            _git(repo_root, "merge", "--no-ff", "--no-edit", branch)
        except GitError:
            _git(repo_root, "merge", "--abort")
            raise
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
