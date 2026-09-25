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
import subprocess
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
# Codegen replies are long and reasoning models are slow: the first live run
# (gpt-5, 2026-09-25) took ~50-60s per reply, so llm_client's 60s default
# kept timing out and silently spending its infra retries.
CODEGEN_TIMEOUT_SECONDS = 300
# The official MCP Python SDK (`mcp` on PyPI) needs 3.10+; macOS's own
# /usr/bin/python3 is 3.9, where pip finds no installable version at all.
MIN_VENV_PYTHON = (3, 10)
# Newest versions sometimes lack wheels for compiled deps, so prefer
# established ones when several are installed.
_VENV_PYTHON_CANDIDATES = ("python3.12", "python3.13", "python3.11", "python3.14", "python3.10", "python3")

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


def _python_version(path: str) -> tuple[int, int] | None:
    try:
        out = subprocess.run(
            [path, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout.split()
        return (int(out[0]), int(out[1]))
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        return None


def _find_system_python() -> str:
    # Must live on the minimal sandbox PATH (outside $HOME): the venv's
    # interpreter points back at this base install, and the sandbox blocks
    # reads under $HOME.
    for name in _VENV_PYTHON_CANDIDATES:
        found = shutil.which(name, path=sandbox_runner.MINIMAL_PATH)
        if found is None:
            continue
        version = _python_version(found)
        if version is not None and version >= MIN_VENV_PYTHON:
            return found
    raise sandbox_runner.SandboxUnavailableError(
        f"No Python {MIN_VENV_PYTHON[0]}.{MIN_VENV_PYTHON[1]}+ found outside your home "
        "directory — can't create an isolated venv the MCP SDK installs into. "
        "Install one with Homebrew (e.g. `brew install python@3.12`)."
    )


def _run_one_attempt(
    attempt_dir: Path,
    readme_content: str,
    prior_failures: list[str],
    llm_config: LLMConfig,
) -> codegen.WrapperDraft:
    """One wrapper-repair attempt: ask the LLM, install into a fresh venv
    isolated from Legwork's own environment, invoke the self-test. Raises
    on any failure — caller decides retryable vs fail-fast."""
    messages = codegen.build_messages(readme_content, prior_failures)
    response_text = complete(llm_config, messages, timeout=CODEGEN_TIMEOUT_SECONDS)
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
    # The MCP SDK pin goes LAST so it wins even if the model's command (or
    # the target repo's own dependencies) pulled in mcp 2.x — the wrapper
    # is written against the 1.x API the prompt specifies.
    venv_env = {"PATH": f"{venv_dir / 'bin'}:{sandbox_runner.MINIMAL_PATH}"}
    combined_install_cmd = (
        f"{shlex.quote(system_python)} -m venv {shlex.quote(str(venv_dir))}"
        f" && {draft.install_command}"
        f" && python -m pip install {shlex.quote(codegen.MCP_SDK_PIN)}"
    )
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
    prior_failures: list[str] = []

    for attempt_number in range(1, MAX_WRAPPER_ATTEMPTS + 1):
        _check_deadline()
        what_changed = "initial attempt" if attempt_number == 1 else f"retry after: {prior_failures[-1]}"
        attempt_dir = workdir / f"attempt-{attempt_number}"

        try:
            draft = _run_one_attempt(attempt_dir, readme.content, prior_failures, llm_config)
        except FAIL_FAST_EXCEPTIONS as exc:
            detail = f"{type(exc).__name__}: {exc}"
            attempts.append(AttemptLog(attempt_number, what_changed, type(exc).__name__, detail))
            return RunResult(success=False, wrapper_code=None, install_command=None, attempts=attempts, final_error=detail)
        except RETRYABLE_EXCEPTIONS as exc:
            detail = f"{type(exc).__name__}: {exc}"
            attempts.append(AttemptLog(attempt_number, what_changed, type(exc).__name__, detail))
            prior_failures.append(detail)
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
