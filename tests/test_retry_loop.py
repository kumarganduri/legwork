"""Pure retry-loop logic, fully mocked at the module boundary (repo fetch,
obfuscation scan, README parse, LLM call, sandbox install/invoke) — this
suite validates the orchestration decisions (3-attempt budget, fail-fast
vs retryable bucketing, per-attempt logging, total-timeout enforcement),
not the real infrastructure underneath, which T2/T4/T5/T10's own suites
already validate for real. See test_retry_loop_integration.py for a real
end-to-end run (real fetch, real scan, real sandbox — LLM response is the
only mocked seam, since no API key exists in this environment)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from legwork.codegen import InsufficientReadmeError, WrapperDraft
from legwork.llm_client import LLMAuthError, LLMConfig, LLMMalformedOutputError
from legwork.readme_parser import ParsedReadme
from legwork.repo_fetcher import ClonedRepo, RepoRef
from legwork.retry_loop import MAX_WRAPPER_ATTEMPTS, TotalRunTimeoutExceeded, run
from legwork.sandbox_runner import DependencyInstallError, WrapperRuntimeError

CONFIG = LLMConfig(endpoint="https://api.example.com/v1", api_key="sk-test", model="gpt-test")

VALID_DRAFT = WrapperDraft(
    entrypoint_description="does a thing",
    install_command="pip install foo",
    wrapper_code="print('hello')",
)


def _cloned(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(exist_ok=True)
    return ClonedRepo(ref=RepoRef(owner="owner", repo="repo"), path=repo_dir)


def _readme():
    return ParsedReadme(path=None, content="# Repo", original_bytes=7, truncated=False)


def _patched(tmp_path, **overrides):
    """Context manager stack patching every pipeline dependency with a
    working default, overridable per test."""
    defaults = {
        "fetch": patch("legwork.retry_loop.repo_fetcher.fetch", return_value=_cloned(tmp_path)),
        "scan": patch("legwork.retry_loop.obfuscation_scanner.scan", return_value=None),
        "parse_readme": patch("legwork.retry_loop.readme_parser.parse_readme", return_value=_readme()),
        "find_python": patch("legwork.retry_loop._find_system_python", return_value="/usr/bin/python3"),
        "sandbox_install": patch("legwork.retry_loop.sandbox_runner.install", return_value=None),
        "sandbox_invoke": patch("legwork.retry_loop.sandbox_runner.invoke", return_value=None),
    }
    defaults.update(overrides)
    return defaults


class _MultiPatch:
    def __init__(self, patches: dict):
        self._patches = patches
        self._mocks = {}

    def __enter__(self):
        for name, p in self._patches.items():
            self._mocks[name] = p.start()
        return self._mocks

    def __exit__(self, *exc):
        for p in self._patches.values():
            p.stop()


# --- happy path ---------------------------------------------------------


def test_success_on_first_attempt(tmp_path):
    patches = _patched(tmp_path, complete=patch("legwork.retry_loop.complete", return_value="raw response"))
    with _MultiPatch(patches):
        with patch("legwork.retry_loop.codegen.parse_response", return_value=VALID_DRAFT):
            result = run("owner/repo", tmp_path, CONFIG)

    assert result.success is True
    assert result.wrapper_code == VALID_DRAFT.wrapper_code
    assert len(result.attempts) == 1
    assert result.attempts[0].outcome == "success"
    assert result.attempts[0].what_changed == "initial attempt"


# --- retryable failures: consume budget, feed back only the latest ---------


def test_fails_twice_then_succeeds_on_third_attempt(tmp_path):
    patches = _patched(tmp_path, complete=patch("legwork.retry_loop.complete", return_value="raw"))
    parse_side_effects = [
        WrapperRuntimeError("boom 1"),
        DependencyInstallError("boom 2"),
        VALID_DRAFT,
    ]
    with _MultiPatch(patches):
        with patch("legwork.retry_loop.codegen.parse_response", side_effect=parse_side_effects):
            result = run("owner/repo", tmp_path, CONFIG)

    assert result.success is True
    assert len(result.attempts) == 3
    assert result.attempts[0].outcome == "WrapperRuntimeError"
    assert result.attempts[1].outcome == "DependencyInstallError"
    assert result.attempts[2].outcome == "success"
    # Each retry's "what changed" carries only the immediately prior
    # failure, not an accumulated transcript of all prior attempts.
    assert "boom 1" in result.attempts[1].what_changed
    assert "boom 1" not in result.attempts[2].what_changed  # not accumulated
    assert "boom 2" in result.attempts[2].what_changed


def test_fails_all_three_attempts_reports_all(tmp_path):
    patches = _patched(tmp_path, complete=patch("legwork.retry_loop.complete", return_value="raw"))
    failures = [WrapperRuntimeError(f"fail {i}") for i in range(1, MAX_WRAPPER_ATTEMPTS + 1)]
    with _MultiPatch(patches):
        with patch("legwork.retry_loop.codegen.parse_response", side_effect=failures):
            result = run("owner/repo", tmp_path, CONFIG)

    assert result.success is False
    assert len(result.attempts) == MAX_WRAPPER_ATTEMPTS
    assert "fail 1" in result.final_error
    assert "fail 2" in result.final_error
    assert "fail 3" in result.final_error
    assert "Retry budget exhausted" in result.final_error


def test_llm_malformed_output_is_retryable(tmp_path):
    patches = _patched(tmp_path, complete=patch("legwork.retry_loop.complete", return_value="garbage"))
    with _MultiPatch(patches), patch(
        "legwork.retry_loop.codegen.parse_response",
        side_effect=[LLMMalformedOutputError("bad shape"), VALID_DRAFT],
    ):
        result = run("owner/repo", tmp_path, CONFIG)
    assert result.success is True
    assert len(result.attempts) == 2


# --- fail-fast failures: never consume the wrapper-repair budget -----------


def test_codegen_refusal_fails_fast_no_retry(tmp_path):
    """INSUFFICIENT_README-style refusals mean retrying with the same
    README won't help — must abort after attempt 1, not burn the budget."""
    patches = _patched(tmp_path, complete=patch("legwork.retry_loop.complete", return_value="raw"))
    with _MultiPatch(patches), patch(
        "legwork.retry_loop.codegen.parse_response",
        side_effect=InsufficientReadmeError("no install info"),
    ):
        result = run("owner/repo", tmp_path, CONFIG)

    assert result.success is False
    assert len(result.attempts) == 1  # did NOT retry
    assert "InsufficientReadmeError" in result.attempts[0].outcome


def test_llm_auth_error_fails_fast_no_retry(tmp_path):
    patches = _patched(
        tmp_path, complete=patch("legwork.retry_loop.complete", side_effect=LLMAuthError("bad key"))
    )
    with _MultiPatch(patches):
        result = run("owner/repo", tmp_path, CONFIG)

    assert result.success is False
    assert len(result.attempts) == 1
    assert "LLMAuthError" in result.attempts[0].outcome


def test_repo_fetch_failure_never_reaches_the_retry_loop(tmp_path):
    """Repo-fetch/scan/README-parse failures are one-shot preconditions —
    not even attempt 1 gets logged, since no wrapper-repair attempt ever
    started."""
    from legwork.repo_fetcher import RepoNotFoundError

    with patch("legwork.retry_loop.repo_fetcher.fetch", side_effect=RepoNotFoundError("gone")):
        with pytest.raises(RepoNotFoundError):
            run("owner/repo", tmp_path, CONFIG)


def test_obfuscation_scan_failure_never_reaches_the_retry_loop(tmp_path):
    from legwork.obfuscation_scanner import ObfuscatedPayloadDetectedError

    patches = _patched(
        tmp_path,
        scan=patch(
            "legwork.retry_loop.obfuscation_scanner.scan",
            side_effect=ObfuscatedPayloadDetectedError("evil.py: obfuscated"),
        ),
    )
    with _MultiPatch(patches), pytest.raises(ObfuscatedPayloadDetectedError):
        run("owner/repo", tmp_path, CONFIG)


# --- total-run timeout: hard abort regardless of remaining attempts --------


def test_total_run_timeout_aborts_before_starting_an_attempt(tmp_path):
    patches = _patched(tmp_path, complete=patch("legwork.retry_loop.complete", return_value="raw"))
    with _MultiPatch(patches):
        with patch("legwork.retry_loop.codegen.parse_response", return_value=VALID_DRAFT):
            # Force the deadline to already be in the past.
            with patch("legwork.retry_loop.time.monotonic", side_effect=[0, 1000, 1000, 1000]):
                with pytest.raises(TotalRunTimeoutExceeded):
                    run("owner/repo", tmp_path, CONFIG)


# --- isolation: install command runs against a fresh venv, not ambient -----


def test_install_uses_venv_bin_first_on_path(tmp_path):
    patches = _patched(tmp_path, complete=patch("legwork.retry_loop.complete", return_value="raw"))
    with _MultiPatch(patches) as mocks:
        with patch("legwork.retry_loop.codegen.parse_response", return_value=VALID_DRAFT):
            run("owner/repo", tmp_path, CONFIG)

    install_call = mocks["sandbox_install"].call_args
    extra_env = install_call.kwargs["extra_env"]
    assert str(tmp_path / "attempt-1" / ".venv" / "bin") in extra_env["PATH"]
    # The venv creation is chained into the same command as the LLM's own
    # install command, not run as a separate unsandboxed step.
    command = install_call.args[0]
    assert "-m" in command[-1] and "venv" in command[-1]
    assert VALID_DRAFT.install_command in command[-1]
