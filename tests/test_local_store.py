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


# --- what a build came from (issue #4) ----------------------------------------


def test_a_build_records_its_commit_wrapper_hash_and_packages(tmp_path):
    import hashlib
    import json
    import subprocess

    build = local_store.new_build_dir(REF)
    repo = build / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "README.md").write_text("x")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    sha = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()

    attempt = _fake_attempt(build)
    (attempt / "wrapper.py").write_text("print('wrapper')\n")
    site = attempt / ".venv" / "lib" / "python3.12" / "site-packages"
    (site / "pdfplumber-0.11.10.dist-info").mkdir(parents=True)
    (site / "pdfplumber-0.11.10.dist-info" / "METADATA").write_text("Metadata-Version: 2.1\nName: pdfplumber\nVersion: 0.11.10\n")
    (attempt / "node_modules" / "@scope" / "tool").mkdir(parents=True)
    (attempt / "node_modules" / "@scope" / "tool" / "package.json").write_text(json.dumps({"name": "@scope/tool", "version": "2.0.1"}))
    secret = tmp_path / "secret.txt"
    secret.write_text("Name: stolen\nVersion: 1\n")
    (site / "planted-1.dist-info").mkdir()
    (site / "planted-1.dist-info" / "METADATA").symlink_to(secret)  # never followed

    record = local_store.save_current(REF, attempt, "pip install pdfplumber==0.11.10", "reads PDFs", "gpt-5")
    assert record.commit_sha == sha
    assert record.wrapper_sha256 == hashlib.sha256(b"print('wrapper')\n").hexdigest()
    assert record.packages == ("npm:@scope/tool@2.0.1", "pip:pdfplumber==0.11.10")
    assert local_store.load_current(REF) == record


def test_a_record_from_before_issue_4_still_loads():
    import json

    attempt = _fake_attempt(local_store.new_build_dir(REF))
    record = local_store.save_current(REF, attempt, "pip install repo", "does a thing", "gpt-5")
    pointer = attempt.parent.parent.parent / "current.json"
    old = {k: v for k, v in json.loads(pointer.read_text()).items() if k not in ("commit_sha", "wrapper_sha256", "packages")}
    pointer.write_text(json.dumps({**old, "a_future_field": 1}))
    loaded = local_store.load_current(REF)
    assert loaded.commit_sha == "" and loaded.packages == () and loaded.wrapper_path == record.wrapper_path
