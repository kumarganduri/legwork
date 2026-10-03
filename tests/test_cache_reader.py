"""Reading the public cache, and `legwork build` using it. The pipeline's
sandbox steps are mocked here; the install/self-test path is the same one
tests/test_retry_loop_integration.py runs for real."""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from legwork import cache_reader, cli, local_store, retry_loop
from legwork.cache_reader import CacheUnavailableError
from legwork.obfuscation_scanner import ObfuscatedPayloadDetectedError
from legwork.repo_fetcher import RepoRef
from legwork.retry_loop import AttemptLog, RunResult
from legwork.sandbox_runner import DependencyInstallError

REF = RepoRef("owner", "repo")
MANIFEST = {
    "source_repo_url": "https://github.com/owner/repo",
    "commit_sha": "a" * 40,
    "install_command": "pip install repo",
    "entrypoint": "Does a thing",
    "llm_model": "gpt-test",
}
WRAPPER = "from mcp.server.fastmcp import FastMCP\nmcp = FastMCP('repo')\n"
FIXTURE = Path(__file__).parent / "fixtures" / "ai_data_extractor_payload.py"


def write_entry(cache_dir: Path, manifest: dict | None = None, wrapper: str | None = WRAPPER, name: str = "owner__repo"):
    entry = cache_dir / name
    entry.mkdir(parents=True)
    (entry / "manifest.json").write_text(json.dumps(manifest or MANIFEST))
    if wrapper is not None:
        (entry / "wrapper.py").write_text(wrapper)


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGWORK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("LEGWORK_CACHE_URL", str(tmp_path / "cache"))
    for var in ("LEGWORK_LLM_ENDPOINT", "LEGWORK_LLM_API_KEY", "LEGWORK_LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)


# --- fetch ------------------------------------------------------------------


def test_no_entry_means_none(tmp_path):
    assert cache_reader.fetch(REF) is None


def test_a_revoked_entry_is_neither_installed_nor_listed(tmp_path, monkeypatch):
    """A release's cache can't change after it ships; revoked.json can pull an entry."""
    write_entry(tmp_path / "cache")
    (tmp_path / "cache" / "index.json").write_text(json.dumps([{"repo": "owner/repo"}, {"repo": "other/tool"}]))
    assert cache_reader.fetch(REF) is not None
    (tmp_path / "cache" / "revoked.json").write_text(json.dumps({"Owner/Repo": "upstream compromised, 2026-10-10"}))
    monkeypatch.setattr(cache_reader, "_revoked_cache", None)
    assert cache_reader.fetch(REF) is None
    assert [e["repo"] for e in cache_reader.fetch_index()] == ["other/tool"]


def test_a_release_reads_revocations_from_main(monkeypatch):
    monkeypatch.setattr(cache_reader, "default_cache_url", lambda: "https://raw.githubusercontent.com/kumarganduri/legwork/v0.7.9/cache")
    monkeypatch.delenv("LEGWORK_CACHE_URL")
    with patch("legwork.cache_reader._read", return_value='{"owner/repo": "bad"}') as read:
        assert cache_reader._revoked(cache_reader.default_cache_url()) == {"owner/repo"}
    assert read.call_args.args == (cache_reader.DEFAULT_CACHE_URL, "revoked.json")


def test_unreadable_revocations_revoke_nothing(monkeypatch):
    with patch("legwork.cache_reader._read", side_effect=CacheUnavailableError("offline")):
        assert cache_reader._revoked("https://cache.example/cache") == frozenset()
    monkeypatch.setattr(cache_reader, "_revoked_cache", None)
    with patch("legwork.cache_reader._read", return_value='["not", "an", "object"]'):
        assert cache_reader._revoked("https://cache.example/cache") == frozenset()


def test_off_means_none_even_with_an_entry(tmp_path, monkeypatch):
    write_entry(tmp_path / "cache")
    monkeypatch.setenv("LEGWORK_CACHE_URL", "off")
    assert cache_reader.fetch(REF) is None


def test_reads_an_entry_from_a_local_cache_folder(tmp_path):
    write_entry(tmp_path / "cache")
    cached = cache_reader.fetch(REF)
    assert cached.wrapper_code == WRAPPER
    assert cached.install_command == "pip install repo"
    assert cached.entrypoint == "Does a thing"


def test_lookup_ignores_case(tmp_path):
    write_entry(tmp_path / "cache")
    assert cache_reader.fetch(RepoRef("Owner", "Repo")) is not None


def test_an_entry_for_another_repo_is_refused(tmp_path):
    write_entry(tmp_path / "cache", {**MANIFEST, "source_repo_url": "https://github.com/evil/other"})
    with pytest.raises(CacheUnavailableError, match="not owner/repo"):
        cache_reader.fetch(REF)


@pytest.mark.parametrize(
    ("manifest", "wrapper", "match"),
    [
        (MANIFEST, None, "wrapper.py"),
        ({k: v for k, v in MANIFEST.items() if k != "install_command"}, WRAPPER, "install_command"),
    ],
)
def test_an_incomplete_entry_is_refused(tmp_path, manifest, wrapper, match):
    write_entry(tmp_path / "cache", manifest, wrapper)
    with pytest.raises(CacheUnavailableError, match=match):
        cache_reader.fetch(REF)


def _http_error(code):
    return urllib.error.HTTPError("https://x", code, "err", None, io.BytesIO())


def test_http_cache_404_means_no_entry(monkeypatch):
    monkeypatch.setenv("LEGWORK_CACHE_URL", "https://cache.example/cache")
    with patch("legwork.cache_reader.urllib.request.urlopen", side_effect=_http_error(404)) as urlopen:
        assert cache_reader.fetch(REF) is None
    assert urlopen.call_args.args[0] == "https://cache.example/cache/owner__repo/manifest.json"


@pytest.mark.parametrize("error", [_http_error(500), urllib.error.URLError("offline")])
def test_http_cache_trouble_is_unavailable_not_fatal(monkeypatch, error):
    monkeypatch.setenv("LEGWORK_CACHE_URL", "https://cache.example/cache")
    with patch("legwork.cache_reader.urllib.request.urlopen", side_effect=error), pytest.raises(CacheUnavailableError):
        cache_reader.fetch(REF)


def test_default_cache_is_the_legwork_repo():
    assert cache_reader.DEFAULT_CACHE_URL == "https://raw.githubusercontent.com/kumarganduri/legwork/main/cache"


def test_a_released_version_reads_the_cache_at_its_own_tag(monkeypatch):
    """Live main reached every user with no release (pre-launch review, 2026-10-03)."""
    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.7.9")
    assert cache_reader.default_cache_url() == "https://raw.githubusercontent.com/kumarganduri/legwork/v0.7.9/cache"
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.8.0.dev3+g1234")
    assert cache_reader.default_cache_url().endswith("/main/cache")


# --- run_cached -------------------------------------------------------------------


@pytest.fixture
def no_clone():
    with patch("legwork.retry_loop._fetch_and_scan") as fetch_and_scan:
        yield fetch_and_scan


def test_run_cached_installs_and_tests_the_cached_wrapper(tmp_path, no_clone):
    with patch("legwork.retry_loop._install_and_self_test") as install:
        result = retry_loop.run_cached("owner/repo", tmp_path, "pip install repo", WRAPPER)
    assert result.success
    assert result.attempt_dir == tmp_path / "attempt-1"
    attempt_dir, install_command, wrapper_code = install.call_args.args[:3]
    assert (attempt_dir, install_command, wrapper_code) == (tmp_path / "attempt-1", "pip install repo", WRAPPER)
    no_clone.assert_called_once()  # the source repo is still cloned and scanned


def test_run_cached_reports_a_failed_self_test(tmp_path, no_clone):
    with patch("legwork.retry_loop._install_and_self_test", side_effect=DependencyInstallError("no such package")):
        result = retry_loop.run_cached("owner/repo", tmp_path, "pip install repo", WRAPPER)
    assert not result.success
    assert "no such package" in result.final_error


def test_run_cached_scans_the_cached_wrapper_itself(tmp_path, no_clone):
    # The defanged real payload stands in for a poisoned cache entry.
    with (
        patch("legwork.retry_loop._install_and_self_test") as install,
        pytest.raises(ObfuscatedPayloadDetectedError),
    ):
        retry_loop.run_cached("owner/repo", tmp_path, "pip install repo", FIXTURE.read_text())
    install.assert_not_called()


# --- legwork build with the cache ----------------------------------------------------------


def _cached_ok(repo_url, workdir, install_command, wrapper_code, progress):
    attempt = workdir / "attempt-1"
    (attempt / ".venv" / "bin").mkdir(parents=True)
    (attempt / ".venv" / "bin" / "python").write_text("")
    (attempt / "wrapper.py").write_text(wrapper_code)
    return RunResult(True, wrapper_code, install_command, [AttemptLog(1, "cached wrapper", "success", "")], attempt_dir=attempt)


@pytest.fixture
def up_to_date():
    with patch("legwork.cli.repo_fetcher.remote_head_sha", return_value="a" * 40) as remote:
        yield remote


def test_cache_hit_builds_without_any_model_key(tmp_path, capsys, up_to_date):
    write_entry(tmp_path / "cache")
    with (
        patch("legwork.cli.retry_loop.run_cached", side_effect=_cached_ok),
        patch("legwork.cli.retry_loop.run") as run,
    ):
        assert cli.main(["owner/repo"]) == 0
    run.assert_not_called()
    captured = capsys.readouterr()
    assert "Installed the cached MCP wrapper for owner/repo" in captured.out
    assert "stale" not in captured.err
    record = local_store.load_current(REF)
    assert record.model == "gpt-test (Legwork cache)"
    assert record.entrypoint == "Does a thing"


def test_stale_cache_entry_is_used_with_a_warning(tmp_path, capsys, up_to_date):
    up_to_date.return_value = "b" * 40
    write_entry(tmp_path / "cache")
    with patch("legwork.cli.retry_loop.run_cached", side_effect=_cached_ok):
        assert cli.main(["owner/repo"]) == 0
    err = capsys.readouterr().err
    assert "written for an earlier commit" in err and "tested here before use" in err


def test_a_cached_wrapper_that_fails_here_falls_back_to_a_fresh_build(tmp_path, capsys, monkeypatch, up_to_date):
    for var, value in (("ENDPOINT", "https://api.example.com/v1"), ("API_KEY", "sk-test"), ("MODEL", "gpt-test")):
        monkeypatch.setenv(f"LEGWORK_LLM_{var}", value)
    write_entry(tmp_path / "cache")
    failed = RunResult(False, None, None, [AttemptLog(1, "cached wrapper", "DependencyInstallError", "gone")], "gone")
    with (
        patch("legwork.cli.retry_loop.run_cached", return_value=failed),
        patch("legwork.cli.retry_loop.run", return_value=failed) as run,
    ):
        cli.main(["owner/repo"])
    run.assert_called_once()
    assert "writing a fresh one" in capsys.readouterr().err


def test_cache_miss_without_a_model_key_explains_what_is_missing(tmp_path, capsys):
    assert cli.main(["owner/repo"]) == 1
    err = capsys.readouterr().err
    assert "LEGWORK_LLM" in err and "isn't in the Legwork public cache" in err


def test_a_repo_that_doesnt_exist_says_so_instead_of_asking_for_a_key(capsys, monkeypatch):
    """A misspelt repo asked for a model key (fresh QA, 2026-10-03)."""
    from legwork import builder
    from legwork.repo_fetcher import RepoNotFoundError

    def missing(_):
        raise RepoNotFoundError("nope")

    monkeypatch.setattr(builder.repo_fetcher, "check_repo", missing)
    assert cli.main(["owner/repo"]) == 1
    assert "wasn't found on GitHub" in capsys.readouterr().err


def test_no_cache_flag_skips_the_cache(tmp_path):
    write_entry(tmp_path / "cache")
    with patch("legwork.cli.cache_reader.fetch") as fetch:
        cli.main(["--no-cache", "owner/repo"])
    fetch.assert_not_called()


def test_malware_found_while_using_the_cache_stops_the_build(tmp_path, capsys, up_to_date):
    write_entry(tmp_path / "cache")
    with (
        patch("legwork.cli.retry_loop.run_cached", side_effect=ObfuscatedPayloadDetectedError("evil.py")),
        patch("legwork.cli.retry_loop.run") as run,
    ):
        assert cli.main(["owner/repo"]) == 1
    run.assert_not_called()
    assert "ObfuscatedPayloadDetectedError" in capsys.readouterr().err
