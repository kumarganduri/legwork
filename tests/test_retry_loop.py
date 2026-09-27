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
from legwork.retry_loop import (
    MAX_WRAPPER_ATTEMPTS,
    TOTAL_RUN_TIMEOUT_SECONDS,
    TotalRunTimeoutExceeded,
    _find_system_python,
    run,
)
from legwork.sandbox_runner import (
    DependencyInstallError,
    SandboxUnavailableError,
    WrapperRuntimeError,
)

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
            past = TOTAL_RUN_TIMEOUT_SECONDS + 1
            with patch("legwork.retry_loop.time.monotonic", side_effect=[0, past, past, past]):
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


# --- venv interpreter selection (found in the first live run) --------------


def test_find_system_python_skips_versions_below_310():
    """macOS's /usr/bin/python3 is 3.9, where pip can't install the MCP SDK
    at all — the first live run died on exactly this."""
    from legwork import retry_loop

    locations = {"python3": "/usr/bin/python3", "python3.14": "/opt/homebrew/bin/python3.14"}
    versions = {"/usr/bin/python3": (3, 9), "/opt/homebrew/bin/python3.14": (3, 14)}
    with (
        patch("legwork.retry_loop.shutil.which", side_effect=lambda name, path=None: locations.get(name)),
        patch("legwork.retry_loop._python_version", side_effect=versions.get),
        patch("legwork.retry_loop._has_venv", return_value=True),
    ):
        assert retry_loop._find_system_python() == "/opt/homebrew/bin/python3.14"


def test_find_system_python_raises_when_only_old_python_exists():
    from legwork import retry_loop
    from legwork.sandbox_runner import SandboxUnavailableError

    with (
        patch(
            "legwork.retry_loop.shutil.which",
            side_effect=lambda name, path=None: "/usr/bin/python3" if name == "python3" else None,
        ),
        patch("legwork.retry_loop._python_version", return_value=(3, 9)),
        patch("legwork.retry_loop.sandbox_runner.interpreter_home", return_value=None),
        pytest.raises(SandboxUnavailableError, match="3.10"),
    ):
        retry_loop._find_system_python()


def test_codegen_call_uses_the_longer_codegen_timeout(tmp_path):
    from legwork.retry_loop import CODEGEN_TIMEOUT_SECONDS

    patches = _patched(tmp_path, complete=patch("legwork.retry_loop.complete", return_value="raw"))
    with (
        _MultiPatch(patches) as mocks,
        patch("legwork.retry_loop.codegen.parse_response", return_value=VALID_DRAFT),
    ):
        run("owner/repo", tmp_path, CONFIG)
    assert mocks["complete"].call_args.kwargs["timeout"] == CODEGEN_TIMEOUT_SECONDS


def test_mcp_pin_is_installed_after_the_models_install_command(tmp_path):
    from legwork.codegen import MCP_SDK_PIN

    patches = _patched(tmp_path, complete=patch("legwork.retry_loop.complete", return_value="raw"))
    with (
        _MultiPatch(patches) as mocks,
        patch("legwork.retry_loop.codegen.parse_response", return_value=VALID_DRAFT),
    ):
        run("owner/repo", tmp_path, CONFIG)
    shell_cmd = mocks["sandbox_install"].call_args.args[0][-1]
    assert MCP_SDK_PIN in shell_cmd
    assert shell_cmd.index(VALID_DRAFT.install_command) < shell_cmd.index(MCP_SDK_PIN)


def test_third_attempt_prompt_includes_first_attempts_failure(tmp_path):
    """The live run's exact failure: attempt 3 repeated attempt 1's mistake
    because the prompt only carried attempt 2's error."""
    patches = _patched(tmp_path, complete=patch("legwork.retry_loop.complete", return_value="raw"))
    failures = [WrapperRuntimeError("bad hex input"), WrapperRuntimeError("no .tool attribute"), VALID_DRAFT]
    with (
        _MultiPatch(patches) as mocks,
        patch("legwork.retry_loop.codegen.parse_response", side_effect=failures),
    ):
        run("owner/repo", tmp_path, CONFIG)
    third_call_messages = mocks["complete"].call_args_list[2].args[1]
    user_content = third_call_messages[1].content
    assert "bad hex input" in user_content
    assert "no .tool attribute" in user_content


# --- which Python builds the venv -------------------------------------------------


def test_falls_back_to_the_python_running_legwork(tmp_path, monkeypatch):
    """A Mac with only /usr/bin/python3 (3.9) and uv: use uv's Python."""
    fake = tmp_path / "uvpython"
    (fake / "bin").mkdir(parents=True)
    import sys as _sys

    exe = fake / "bin" / f"python{_sys.version_info[0]}.{_sys.version_info[1]}"
    exe.write_text("")
    monkeypatch.setattr("legwork.retry_loop._VENV_PYTHON_CANDIDATES", ())
    monkeypatch.setattr("legwork.retry_loop.sandbox_runner.interpreter_home", lambda: fake)
    monkeypatch.setattr("legwork.retry_loop._python_version", lambda path: (3, 12))
    monkeypatch.setattr("legwork.retry_loop._has_venv", lambda path: True)
    assert _find_system_python() == str(exe)


def test_skips_a_python_that_cannot_make_venvs(monkeypatch):
    """Debian's python3 without python3-venv fails every build; skip it."""
    monkeypatch.setattr("legwork.retry_loop._VENV_PYTHON_CANDIDATES", ("python3",))
    monkeypatch.setattr("legwork.retry_loop.sandbox_runner.interpreter_home", lambda: None)
    monkeypatch.setattr("legwork.retry_loop._python_version", lambda path: (3, 12))
    monkeypatch.setattr("legwork.retry_loop._has_venv", lambda path: False)
    with pytest.raises(SandboxUnavailableError, match="uvx legwork-mcp"):
        _find_system_python()


def test_a_finished_build_drops_downloads_and_failed_environments(tmp_path):
    from legwork.retry_loop import DOWNLOAD_CACHE_DIR, RunResult, _tidy

    for n in (1, 2, 3):
        (tmp_path / f"attempt-{n}" / ".venv").mkdir(parents=True)
        (tmp_path / f"attempt-{n}" / "wrapper.py").write_text("")
    (tmp_path / DOWNLOAD_CACHE_DIR / "pip").mkdir(parents=True)
    _tidy(tmp_path, RunResult(True, "", "", attempt_dir=tmp_path / "attempt-3"))
    assert not (tmp_path / DOWNLOAD_CACHE_DIR).exists()
    assert not (tmp_path / "attempt-1" / ".venv").exists()
    assert (tmp_path / "attempt-1" / "wrapper.py").exists()  # kept for debugging
    assert (tmp_path / "attempt-3" / ".venv").exists()  # the one that works



def test_every_model_reply_is_kept_in_its_attempt_folder(tmp_path):
    patches = _patched(tmp_path, complete=patch("legwork.retry_loop.complete", return_value="the raw reply"))
    with _MultiPatch(patches), patch("legwork.retry_loop.codegen.parse_response", return_value=VALID_DRAFT):
        run("owner/repo", tmp_path, CONFIG)
    assert (tmp_path / "attempt-1" / "model-reply.md").read_text() == "the raw reply"
