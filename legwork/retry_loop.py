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

Validated end to end against a real model (gpt-5, 2026-09-25): reverify
produced a working FastMCP wrapper on attempt 2; JobFlow and phone-harness
were refused correctly. Results in
docs/designs/legwork-live-runs-2026-09-25.md.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
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
    # The successful attempt's folder (wrapper.py + its .venv), for serving.
    attempt_dir: Path | None = None


def _no_progress(_message: str) -> None:
    pass


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


def _has_venv(path: str) -> bool:
    """Debian/Ubuntu ship python3 without venv's ensurepip unless
    python3-venv is installed; skip such an interpreter rather than fail
    every build on it."""
    try:
        return subprocess.run([path, "-c", "import ensurepip, venv"], capture_output=True, timeout=10, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _find_system_python() -> str:
    # Must live on the minimal sandbox PATH (outside $HOME): the venv's
    # interpreter points back at this base install, and the sandbox blocks
    # reads under $HOME.
    candidates = [shutil.which(name, path=sandbox_runner.MINIMAL_PATH) for name in _VENV_PYTHON_CANDIDATES]
    # Then the Python running Legwork itself (uv's under uvx, which is
    # always new enough); the sandbox can read its install read-only.
    interpreter = sandbox_runner.interpreter_home()
    if interpreter is not None:
        own = interpreter / "bin" / f"python{sys.version_info[0]}.{sys.version_info[1]}"
        candidates.append(str(own) if own.exists() else None)
    for found in candidates:
        if found is None:
            continue
        version = _python_version(found)
        if version is not None and version >= MIN_VENV_PYTHON and _has_venv(found):
            return found
    raise sandbox_runner.SandboxUnavailableError(
        f"No Python {MIN_VENV_PYTHON[0]}.{MIN_VENV_PYTHON[1]}+ that can create a venv — needed for "
        "the isolated environment the MCP SDK installs into. Run Legwork with `uvx legwork-mcp` "
        "(uv supplies one), or install one: `brew install python@3.12` on macOS, "
        "`sudo apt install python3 python3-venv` on Debian/Ubuntu."
    )


def _install_and_self_test(
    attempt_dir: Path, install_command: str, wrapper_code: str, progress: Callable[[str], None] = _no_progress
) -> None:
    """Write the wrapper, install into a fresh venv isolated from Legwork's
    own environment, invoke the self-test. Shared by freshly written and
    cached wrappers; raises on any failure."""
    attempt_dir.mkdir(parents=True, exist_ok=True)
    wrapper_path = attempt_dir / "wrapper.py"
    wrapper_path.write_text(wrapper_code)

    venv_dir = attempt_dir / ".venv"
    system_python = _find_system_python()
    # One combined install step: create the venv, then run the LLM's own
    # install command. `extra_env` puts the fresh venv's bin/ first on PATH
    # so a bare "pip"/"python" in install_command resolves there, not
    # to Legwork's own environment — the isolation gap found integrating
    # T5+T6 (see sandbox_runner.py's "Environment isolation" section).
    # The MCP SDK pin goes LAST so it wins even if the model's command (or
    # the target repo's own dependencies) pulled in mcp 2.x — the wrapper
    # is written against the 1.x API the prompt specifies.
    venv_env = {"PATH": f"{venv_dir / 'bin'}:{sandbox_runner.MINIMAL_PATH}"}
    combined_install_cmd = (
        f"{shlex.quote(system_python)} -m venv {shlex.quote(str(venv_dir))}"
        f" && {install_command}"
        f" && python -m pip install {shlex.quote(codegen.MCP_SDK_PIN)}"
    )
    progress("installing dependencies in a sandboxed venv")
    sandbox_runner.install(["/bin/sh", "-c", combined_install_cmd], attempt_dir, extra_env=venv_env)

    progress("running the wrapper's self-test (network off)")
    sandbox_runner.invoke([str(venv_dir / "bin" / "python"), str(wrapper_path)], attempt_dir, extra_env=venv_env)


def _run_one_attempt(
    attempt_dir: Path,
    readme_content: str,
    prior_failures: list[str],
    llm_config: LLMConfig,
    progress: Callable[[str], None] = _no_progress,
) -> codegen.WrapperDraft:
    """One wrapper-repair attempt: ask the LLM, install into a fresh venv
    isolated from Legwork's own environment, invoke the self-test. Raises
    on any failure — caller decides retryable vs fail-fast."""
    progress("asking the model for a wrapper")
    sandbox = codegen.describe_sandbox(sandbox_runner.available_toolchains())
    messages = codegen.build_messages(readme_content, prior_failures, sandbox)
    response_text = complete(llm_config, messages, timeout=CODEGEN_TIMEOUT_SECONDS)
    draft = codegen.parse_response(response_text)

    _install_and_self_test(attempt_dir, draft.install_command, draft.wrapper_code, progress)
    return draft


def _fetch_and_scan(repo_url: str, workdir: Path, progress: Callable[[str], None]) -> repo_fetcher.ClonedRepo:
    progress(f"cloning {repo_url}")
    cloned = repo_fetcher.fetch(repo_url, workdir / "repo")
    progress("scanning the source for obfuscated code")
    obfuscation_scanner.scan(cloned.path)
    return cloned


def run_cached(
    repo_url: str,
    workdir: Path,
    install_command: str,
    wrapper_code: str,
    progress: Callable[[str], None] = _no_progress,
) -> RunResult:
    """Build from a public-cache wrapper instead of asking a model: the same
    fetch and scan, a scan of the cached wrapper itself, then the same
    sandboxed install and self-test. One attempt; the caller falls back to
    `run` if it fails. Raises the precondition errors `run` raises."""
    _fetch_and_scan(repo_url, workdir, progress)
    attempt_dir = workdir / "attempt-1"
    attempt_dir.mkdir(parents=True)
    (attempt_dir / "wrapper.py").write_text(wrapper_code)
    progress("scanning the cached wrapper")
    obfuscation_scanner.scan(attempt_dir)
    try:
        _install_and_self_test(attempt_dir, install_command, wrapper_code, progress)
    except RETRYABLE_EXCEPTIONS as exc:
        detail = f"{type(exc).__name__}: {exc}"
        attempts = [AttemptLog(1, "cached wrapper", type(exc).__name__, detail)]
        return RunResult(success=False, wrapper_code=None, install_command=None, attempts=attempts, final_error=detail)
    return RunResult(
        success=True,
        wrapper_code=wrapper_code,
        install_command=install_command,
        attempts=[AttemptLog(1, "cached wrapper", "success", "")],
        attempt_dir=attempt_dir,
    )


def run(
    repo_url: str,
    workdir: Path,
    llm_config: LLMConfig,
    progress: Callable[[str], None] = _no_progress,
) -> RunResult:
    """The T6 entrypoint. Fetches, scans, parses, then runs up to
    MAX_WRAPPER_ATTEMPTS codegen+install+invoke attempts, each logged.
    `progress` gets one short status line per stage (a build takes
    minutes; the CLI shows these so it doesn't look hung)."""
    deadline = time.monotonic() + TOTAL_RUN_TIMEOUT_SECONDS

    def _check_deadline() -> None:
        if time.monotonic() > deadline:
            raise TotalRunTimeoutExceeded(f"Run exceeded {TOTAL_RUN_TIMEOUT_SECONDS}s total")

    # Preconditions: one-shot, not part of the wrapper-repair budget. Any
    # failure here aborts before attempt 1 is even logged — these aren't
    # wrapper-repair attempts at all.
    _check_deadline()
    cloned = _fetch_and_scan(repo_url, workdir, progress)
    _check_deadline()
    progress("reading the README")
    readme = readme_parser.parse_readme(cloned.path)
    if readme.linked_docs:
        progress(f"also reading {', '.join(str(d.relative_to(cloned.path.resolve())) for d in readme.linked_docs)}")

    attempts: list[AttemptLog] = []
    prior_failures: list[str] = []

    for attempt_number in range(1, MAX_WRAPPER_ATTEMPTS + 1):
        _check_deadline()
        what_changed = "initial attempt" if attempt_number == 1 else f"retry after: {prior_failures[-1]}"
        attempt_dir = workdir / f"attempt-{attempt_number}"

        def attempt_progress(message: str, n: int = attempt_number) -> None:
            progress(f"attempt {n}/{MAX_WRAPPER_ATTEMPTS}: {message}")

        try:
            draft = _run_one_attempt(attempt_dir, readme.content, prior_failures, llm_config, attempt_progress)
        except FAIL_FAST_EXCEPTIONS as exc:
            detail = f"{type(exc).__name__}: {exc}"
            attempts.append(AttemptLog(attempt_number, what_changed, type(exc).__name__, detail))
            return RunResult(success=False, wrapper_code=None, install_command=None, attempts=attempts, final_error=detail)
        except RETRYABLE_EXCEPTIONS as exc:
            detail = f"{type(exc).__name__}: {exc}"
            attempts.append(AttemptLog(attempt_number, what_changed, type(exc).__name__, detail))
            prior_failures.append(detail)
            attempt_progress(f"failed ({type(exc).__name__})")
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
                attempt_dir=attempt_dir,
            )

    raise AssertionError("unreachable: loop always returns")  # pragma: no cover
