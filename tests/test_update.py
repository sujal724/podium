"""B3–B5: the self-updater watches origin/main, announces once, applies only on
approval, and reports honestly when it can't work at all."""

import pytest

from podium.sink import Sink
from podium.update import SelfUpdater, UpdateError
from tests.conftest import git


@pytest.fixture
def repos(tmp_path):
    """origin (the release source) + install (the deployed checkout tracking it)."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-b", "main")
    (origin / "pyproject.toml").write_text('[project]\nname = "podium"\nversion = "0.2.0"\n')
    git(origin, "add", "-A")
    git(origin, "commit", "-m", "v0.2.0")
    install = tmp_path / "install"
    git(tmp_path, "clone", "-q", str(origin), str(install))
    return origin, install


def _release(origin, version):
    (origin / "pyproject.toml").write_text(
        f'[project]\nname = "podium"\nversion = "{version}"\n')
    git(origin, "add", "-A")
    git(origin, "commit", "-m", f"v{version}")


def _updater(install, calls):
    sink = Sink()
    frames = []
    sink.tap(frames.append)

    async def fake_restart():
        calls.append("restart")

    u = SelfUpdater(sink, repo=str(install),
                    install_cmd=["true"], restart_fn=fake_restart)
    return u, frames


async def test_up_to_date_reports_zero_and_stays_quiet(repos):
    origin, install = repos
    u, frames = _updater(install, [])
    st = await u.check()
    assert st["available"] and st["behind"] == 0
    assert not [f for f in frames if f["type"] == "update.available"]


async def test_behind_announces_once_with_remote_version(repos):
    origin, install = repos
    calls = []
    u, frames = _updater(install, calls)
    _release(origin, "0.3.0")
    st = await u.check()
    assert st["behind"] == 1 and st["remote_version"] == "0.3.0"
    await u.check()  # same remote sha → no re-announce
    announced = [f for f in frames if f["type"] == "update.available"]
    assert len(announced) == 1
    assert announced[0]["remote_version"] == "0.3.0"


async def test_apply_ffs_installs_restarts(repos):
    origin, install = repos
    calls = []
    u, _ = _updater(install, calls)
    _release(origin, "0.3.0")
    await u.check()
    await u.apply(actor="sujal")
    assert calls == ["restart"]
    assert (install / "pyproject.toml").read_text().count("0.3.0") == 1
    assert u.status()["behind"] == 0


async def test_apply_refuses_when_current(repos):
    origin, install = repos
    u, _ = _updater(install, [])
    await u.check()
    with pytest.raises(UpdateError, match="up to date"):
        await u.apply()


async def test_non_git_install_reports_reason(tmp_path):
    sink = Sink()
    plain = tmp_path / "plain"
    plain.mkdir()
    u = SelfUpdater(sink, repo=str(plain))
    st = u.status()
    assert st["available"] is False and st["reason"]


async def test_failed_install_step_raises(repos):
    origin, install = repos
    u, _ = _updater(install, [])
    u.install_cmd = ["false"]
    _release(origin, "0.3.0")
    await u.check()
    with pytest.raises(UpdateError, match="install step failed"):
        await u.apply()
