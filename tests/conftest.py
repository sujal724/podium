import os
import subprocess

# Must be set before any podium import: registers the mock worker and keeps
# config defaults pointing at test-friendly paths.
os.environ["PODIUM_ENABLE_MOCK"] = "1"

import pytest

from podium.sink import Sink
from podium.state import StateStore
from podium.work.store import WorkStore


@pytest.fixture
def state(tmp_path):
    s = StateStore(str(tmp_path / "state.db"))
    yield s
    s.close()


@pytest.fixture
def sink():
    return Sink()


@pytest.fixture
def work(state, sink):
    return WorkStore(state, sink)


def git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )


@pytest.fixture
def repo(tmp_path):
    """A tiny git repo with an initial commit on main — a managed project."""
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-b", "main")
    (r / "README.md").write_text("hello\n")
    git(r, "add", "-A")
    git(r, "commit", "-m", "init")
    return r
