"""Self-update (spec 002 Part 2) — watch, tell, and only on operator approval, apply.

The editable install's repo is the deployment: a release is a new commit on
`origin/main` (SDLC). The daemon fetches on a timer, announces when it's behind, and
`update.apply` fast-forwards the repo, reinstalls into the daemon's own venv, and
re-execs the process. Auto-resume (decision 23) re-queues whatever was interrupted.

If Podium isn't running from a git checkout, the updater truthfully reports
`available: false` with the reason (decision 44) — never a silent no-op.
"""

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

import podium
from podium import protocol
from podium.sink import Sink


class UpdateError(RuntimeError):
    pass


def _detect_repo() -> tuple[str | None, str]:
    override = os.environ.get("PODIUM_SELF_REPO", "")
    candidate = Path(override).expanduser() if override \
        else Path(podium.__file__).resolve().parent.parent
    if not (candidate / "pyproject.toml").exists():
        return None, f"{candidate} has no pyproject.toml (not an editable checkout)"
    probe = subprocess.run(["git", "-C", str(candidate), "rev-parse", "--git-dir"],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        return None, f"{candidate} is not a git checkout"
    return str(candidate), ""


class SelfUpdater:
    def __init__(self, sink: Sink, repo: str | None = None,
                 install_cmd: list[str] | None = None,
                 restart_fn=None) -> None:
        self.sink = sink
        if repo is not None:
            self.repo, self.reason = repo, ""
        else:
            self.repo, self.reason = _detect_repo()
        self.install_cmd = install_cmd or [
            sys.executable, "-m", "pip", "install", "--quiet", "-e", self.repo or "."]
        self.restart_fn = restart_fn or self._default_restart
        self._announced_sha: str | None = None

    # --- read ----------------------------------------------------------------

    def _git(self, *args: str) -> str:
        proc = subprocess.run(["git", "-C", self.repo, *args],
                              capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise UpdateError(f"git {' '.join(args)}: {proc.stderr.strip()}")
        return proc.stdout.strip()

    def status(self) -> dict:
        """No-network view of where we are vs the last-fetched origin/main."""
        base = {"type": "update.status", "installed": podium.__version__,
                "repo": self.repo}
        if self.repo is None:
            return base | {"available": False, "reason": self.reason}
        try:
            head = self._git("rev-parse", "HEAD")
            remote = self._git("rev-parse", "origin/main")
            behind = int(self._git("rev-list", "--count", "HEAD..origin/main"))
            remote_version = self._remote_version()
        except UpdateError as e:
            return base | {"available": False, "reason": str(e)}
        return base | {"available": True, "head": head[:10], "remote": remote[:10],
                       "behind": behind, "remote_version": remote_version}

    def _remote_version(self) -> str:
        try:
            toml = self._git("show", "origin/main:pyproject.toml")
            m = re.search(r'^version\s*=\s*"([^"]+)"', toml, re.M)
            return m.group(1) if m else "?"
        except UpdateError:
            return "?"

    async def check(self) -> dict:
        """Fetch origin/main and announce (once per remote sha) if we're behind."""
        if self.repo is None:
            return self.status()
        try:
            await asyncio.to_thread(self._git, "fetch", "--quiet", "origin", "main")
        except UpdateError as e:
            return {"type": "update.status", "installed": podium.__version__,
                    "repo": self.repo, "available": False, "reason": str(e)}
        st = self.status()
        if st.get("behind", 0) > 0 and st["remote"] != self._announced_sha:
            self._announced_sha = st["remote"]
            self.sink.emit(protocol.update_available(
                st["installed"], st["remote_version"], st["behind"]))
        return st

    # --- apply (operator-approved only) --------------------------------------

    async def apply(self, actor: str = "human") -> dict:
        st = self.status()
        if not st.get("available"):
            raise UpdateError(st.get("reason", "updater unavailable"))
        if st.get("behind", 0) == 0:
            raise UpdateError("already up to date — nothing to apply")
        self.sink.emit(protocol.narration(
            None, f"update approved by {actor}: {st['installed']} → "
                  f"{st['remote_version']} ({st['behind']} commit(s)); "
                  "restarting when applied"))
        await asyncio.to_thread(self._git, "merge", "--ff-only", "origin/main")
        proc = await asyncio.to_thread(
            subprocess.run, self.install_cmd,
            capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            raise UpdateError(f"install step failed: {proc.stderr.strip()[-500:]}")
        await self.restart_fn()
        return self.status()

    async def _default_restart(self) -> None:
        """Replaced by the daemon with a graceful stop-sessions-then-exec; this bare
        fallback just re-execs (server sockets are non-inheritable, the port frees)."""
        os.execv(sys.executable, [sys.executable, "-m", "podium.gateway"])
