"""The retry loop: wires repo_fetcher (T2) -> obfuscation_scanner (T10) ->
readme_parser (T4) -> codegen (T6) -> sandbox_runner (T5) into one run,
with the 3-attempt wrapper-repair budget, the 10-minute total-run ceiling,
and per-attempt structured logging.

Design doc: docs/designs/legwork-jit-ai-runtime.md, T6.

Two exception buckets, per the Error & Rescue Registry:
- FAIL_FAST: not a wrapper-repair attempt at all — happens once, aborts
  the whole run immediately, no retry budget consumed. Covers repo-fetch
  and obfuscation-scan failures (one-shot preconditions before any attempt
  starts) plus the codegen refusal categories (retrying the same README
  won't change a "this needs its own hardware" answer) plus LLMAuthError
  (a bad key won't fix itself).
- RETRYABLE: counts as one of the 3 wrapper-repair attempts, feeds the
  failure back into the next attempt's prompt (latest failure only, never
  accumulated history — see codegen.build_messages).

**Not end-to-end validated against a real LLM** — no OpenAI-compatible API
key is available in this environment. Every piece downstream of the LLM
response (parsing, venv isolation, sandboxed install/invoke, timeout
enforcement, retry bucketing) IS validated for real, with a real filesystem
and a real sandbox; the codegen.py module docstring covers what's
specifically unvalidated and why.
"""

from __future__ import annotations

import shlex
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from legwork import (
    codegen,
    obfuscation_scanner,
    readme_parser,
    repo_fetcher,
    sandbox_runner,
)
from legwork.llm_client import (
    LLMAuthError,
    LLMConfig,
    LLMMalformedOutputError,
    LLMRateLimitError,
    LLMTimeoutError,
    complete,
)

MAX_WRAPPER_ATTEMPTS = 3
TOTAL_RUN_TIMEOUT_SECONDS = 600

# Counts as a wrapper-repair attempt: plausibly fixable by trying again,
# whether that's the LLM producing different code or a transient infra hiccup.
RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    LLMMalformedOutputError,
    LLMTimeoutError,
    LLMRateLimitError,
    sandbox_runner.DependencyInstallError,
    sandbox_runner.InstallTimeoutExceeded,
    sandbox_runner.WrapperRuntimeError,
    sandbox_runner.TimeoutExceeded,
)

# Not a wrapper-repair attempt: retrying with the same README/config won't
# change the outcome (design doc Error Registry, explicit "fail fast, no
# retry" rescue action).
FAIL_FAST_EXCEPTIONS: tuple[type[Exception], ...] = codegen.FAIL_FAST_EXCEPTIONS + (LLMAuthError,)


class TotalRunTimeoutExceeded(Exception):
    """The whole run (all attempts, all phases) exceeded
    TOTAL_RUN_TIMEOUT_SECONDS. Hard abort — no further retries."""


@dataclass(frozen=True)
class AttemptLog:
    attempt_number: int
    what_changed: str
    outcome: str  # "success" or an exception class name
    detail: str


@dataclass(frozen=True)
class RunResult:
    success: bool
    wrapper_code: str | None
    install_command: str | None
    attempts: list[AttemptLog] = field(default_factory=list)
    final_error: str | None = None


def _find_system_python() -> str:
    found = shutil.which("python3", path=sandbox_runner.MINIMAL_PATH)
    if found is None:
        raise sandbox_runner.SandboxUnavailableError(
            "No python3 found on the minimal sandbox PATH — can't create an "
            "isolated venv for the target repo's dependencies."
        )
    return found


def _run_one_attempt(
    attempt_dir: Path,
    readme_content: str,
    prior_failure: str | None,
    llm_config: LLMConfig,
) -> codegen.WrapperDraft:
    """One wrapper-repair attempt: ask the LLM, install into a fresh venv
    isolated from Legwork's own environment, invoke the self-test. Raises
    on any failure — caller decides retryable vs fail-fast."""
    messages = codegen.build_messages(readme_content, prior_failure)
    response_text = complete(llm_config, messages)
    draft = codegen.parse_response(response_text)

    attempt_dir.mkdir(parents=True, exist_ok=True)
    wrapper_path = attempt_dir / "wrapper.py"
    wrapper_path.write_text(draft.wrapper_code)

    venv_dir = attempt_dir / ".venv"
    system_python = _find_system_python()
    # One combined install step: create the venv, then run the LLM's own
    # install command. `extra_env` puts the fresh venv's bin/ first on PATH
    # so a bare "pip"/"python" in draft.install_command resolves there, not
    # to Legwork's own environment — the isolation gap found integrating
    # T5+T6 (see sandbox_runner.py's "Environment isolation" section).
    venv_env = {"PATH": f"{venv_dir / 'bin'}:{sandbox_runner.MINIMAL_PATH}"}
    combined_install_cmd = f"{shlex.quote(system_python)} -m venv {shlex.quote(str(venv_dir))} && {draft.install_command}"
    sandbox_runner.install(["/bin/sh", "-c", combined_install_cmd], attempt_dir, extra_env=venv_env)

    sandbox_runner.invoke([str(venv_dir / "bin" / "python"), str(wrapper_path)], attempt_dir, extra_env=venv_env)

    return draft


def run(repo_url: str, workdir: Path, llm_config: LLMConfig) -> RunResult:
    """The T6 entrypoint. Fetches, scans, parses, then runs up to
    MAX_WRAPPER_ATTEMPTS codegen+install+invoke attempts, each logged."""
    deadline = time.monotonic() + TOTAL_RUN_TIMEOUT_SECONDS

    def _check_deadline() -> None:
        if time.monotonic() > deadline:
            raise TotalRunTimeoutExceeded(f"Run exceeded {TOTAL_RUN_TIMEOUT_SECONDS}s total")

    # Preconditions: one-shot, not part of the wrapper-repair budget. Any
    # failure here aborts before attempt 1 is even logged — these aren't
    # wrapper-repair attempts at all.
    _check_deadline()
    cloned = repo_fetcher.fetch(repo_url, workdir / "repo")
    _check_deadline()
    obfuscation_scanner.scan(cloned.path)
    _check_deadline()
    readme = readme_parser.parse_readme(cloned.path)

    attempts: list[AttemptLog] = []
    prior_failure: str | None = None

    for attempt_number in range(1, MAX_WRAPPER_ATTEMPTS + 1):
        _check_deadline()
        what_changed = "initial attempt" if attempt_number == 1 else f"retry after: {prior_failure}"
        attempt_dir = workdir / f"attempt-{attempt_number}"

        try:
            draft = _run_one_attempt(attempt_dir, readme.content, prior_failure, llm_config)
        except FAIL_FAST_EXCEPTIONS as exc:
            detail = f"{type(exc).__name__}: {exc}"
            attempts.append(AttemptLog(attempt_number, what_changed, type(exc).__name__, detail))
            return RunResult(success=False, wrapper_code=None, install_command=None, attempts=attempts, final_error=detail)
        except RETRYABLE_EXCEPTIONS as exc:
            detail = f"{type(exc).__name__}: {exc}"
            attempts.append(AttemptLog(attempt_number, what_changed, type(exc).__name__, detail))
            prior_failure = detail
            if attempt_number == MAX_WRAPPER_ATTEMPTS:
                summary = "; ".join(f"attempt {a.attempt_number}: {a.detail}" for a in attempts)
                return RunResult(
                    success=False,
                    wrapper_code=None,
                    install_command=None,
                    attempts=attempts,
                    final_error=f"Retry budget exhausted after {MAX_WRAPPER_ATTEMPTS} attempts: {summary}",
                )
            continue
        else:
            attempts.append(AttemptLog(attempt_number, what_changed, "success", draft.entrypoint_description))
            return RunResult(
                success=True,
                wrapper_code=draft.wrapper_code,
                install_command=draft.install_command,
                attempts=attempts,
                final_error=None,
            )

    raise AssertionError("unreachable: loop always returns")  # pragma: no cover
