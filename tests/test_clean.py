"""`legwork clean`: frees old and failed builds, never the one serve uses."""

from __future__ import annotations

import pytest

from legwork import cli, local_store
from legwork.repo_fetcher import RepoRef

REF = RepoRef("owner", "repo")


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGWORK_HOME", str(tmp_path / "home"))


def build(ref=REF, attempts=2, current=True):
    build_dir = local_store.new_build_dir(ref)
    (build_dir / "repo").mkdir()
    for n in range(1, attempts + 1):
        (build_dir / f"attempt-{n}" / ".venv" / "bin").mkdir(parents=True)
        (build_dir / f"attempt-{n}" / ".venv" / "bin" / "python").write_text("x" * 1000)
        (build_dir / f"attempt-{n}" / "wrapper.py").write_text("")
    if current:
        local_store.save_current(ref, build_dir / f"attempt-{attempts}", "pip install x", "does x", "m")
    return build_dir


def test_keeps_the_current_build_and_removes_the_rest(capsys):
    old = build()
    current = build()
    assert cli.main(["clean"]) == 0
    assert not old.exists()
    assert (current / "attempt-2" / ".venv").exists()  # serve runs this one
    assert (current / "repo").exists()  # contribute reads the clone
    assert not (current / "attempt-1" / ".venv").exists()  # a failed attempt's environment
    assert (current / "attempt-1" / "wrapper.py").exists()
    local_store.load_current(REF)  # still serveable
    assert "Freed" in capsys.readouterr().out


def test_a_repo_that_never_built_is_removed_entirely():
    build(RepoRef("failed", "repo"), current=False)
    cli.main(["clean"])
    assert not (local_store.legwork_home() / "wrappers" / "failed__repo").exists()


def test_dry_run_removes_nothing(capsys):
    old = build()
    build()
    cli.main(["clean", "--dry-run"])
    assert old.exists()
    assert "would remove" in capsys.readouterr().out


def test_all_removes_current_builds_too(capsys):
    build()
    cli.main(["clean", "--all"])
    with pytest.raises(local_store.NoBuildError):
        local_store.load_current(REF)
    assert "Rebuild" in capsys.readouterr().out


def test_one_repo_only():
    other = RepoRef("other", "repo")
    old_other = build(other)
    build(other)
    old = build()
    build()
    cli.main(["clean", "owner/repo"])
    assert not old.exists()
    assert old_other.exists()


def test_nothing_outside_legwork_home_is_ever_a_target(tmp_path):
    build()
    for target in local_store.cleanup_targets(everything=True):
        assert target.resolve().is_relative_to((local_store.legwork_home() / "wrappers").resolve())
