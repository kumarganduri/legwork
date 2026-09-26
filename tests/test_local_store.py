from __future__ import annotations

import pytest

from legwork import local_store
from legwork.repo_fetcher import RepoRef

REF = RepoRef("owner", "repo")


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGWORK_HOME", str(tmp_path))


def _fake_attempt(build_dir):
    attempt = build_dir / "attempt-2"
    (attempt / ".venv" / "bin").mkdir(parents=True)
    (attempt / ".venv" / "bin" / "python").write_text("")
    (attempt / "wrapper.py").write_text("")
    return attempt


def test_each_build_gets_its_own_folder():
    assert local_store.new_build_dir(REF) != local_store.new_build_dir(REF)


def test_save_then_load_round_trips(tmp_path):
    attempt = _fake_attempt(local_store.new_build_dir(REF))
    saved = local_store.save_current(REF, attempt, "pip install repo", "does a thing", "gpt-5")
    loaded = local_store.load_current(REF)
    assert loaded == saved
    assert loaded.wrapper_path == str(attempt / "wrapper.py")
    assert str(tmp_path) in loaded.attempt_dir  # honours LEGWORK_HOME


def test_load_without_a_build_says_how_to_build():
    with pytest.raises(local_store.NoBuildError, match="legwork owner/repo"):
        local_store.load_current(REF)


def test_load_refuses_a_record_whose_files_are_gone():
    attempt = _fake_attempt(local_store.new_build_dir(REF))
    local_store.save_current(REF, attempt, "pip install repo", "does a thing", "gpt-5")
    (attempt / "wrapper.py").unlink()
    with pytest.raises(local_store.NoBuildError, match="incomplete"):
        local_store.load_current(REF)
