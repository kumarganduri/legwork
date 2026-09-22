"""Two-phase sandboxed execution: dependency install (network open) then
entrypoint invocation (network closed), both with filesystem writes AND
reads confined to the sandbox's own working directory (reads outside the
user's home directory are still allowed — needed for Python/pip/system
libraries — but nothing under $HOME beyond the workdir itself).

Design doc: docs/designs/legwork-jit-ai-runtime.md, T5 and "Decided"
section (prompt-injection defense, two-phase network policy).

## vndee/llm-sandbox evaluation (2026-09-22)

T5 asked to evaluate `vndee/llm-sandbox` against the phase-based
network-cutoff requirement before building custom orchestration. Findings:

- Its only backends are Docker, Kubernetes, Podman, and Micromamba
  (`llm_sandbox.SandboxBackend`) — every one needs an external runtime
  installed and running. **None of the four were available on the
  evaluation machine** (no Docker, no Podman, no Kubernetes, no
  Micromamba) — confirmed by trying each, not assumed.
- Its `SessionConfig` has no first-class network-policy field; per-phase
  network control would go through `runtime_configs` (raw pass-through to
  the underlying Docker/Podman/K8s client's own options), which still
  requires one of those runtimes to exist.
- Verdict: not usable for T5 in this environment. Not a mark against the
  library — it's designed for exactly the container runtimes it lists —
  just a real evaluation result, not an assumption.

Built instead: macOS's native `sandbox-exec` (Seatbelt), which the CEO
review's validation spike already proved implements this exact two-phase
network policy correctly (verified in that spike: writes confined, network
allowed only in the install profile). This module hardens that spike
profile for real use — the spike's profile allowed unrestricted reads
everywhere, which is a real exfiltration risk specifically during the
network-open install phase (env vars, SSH keys, cloud credentials all live
under $HOME) — fixed here: reads outside $HOME are allowed (needed for
Python/pip/system libraries), reads under $HOME are confined to the
sandbox workdir only, in both phases.

**Real, disclosed limitation:** `sandbox-exec` is macOS-only. A
Linux/Windows-portable backend (Docker-based, revisiting `llm-sandbox` once
a container runtime is actually available) is real follow-up work — this
module raises `SandboxUnavailableError` with a clear message on other
platforms rather than silently running unsandboxed, which would be a
disclosed-but-then-secretly-ignored security regression.

**Also disclosed:** `sandbox-exec` gives real filesystem and network
confinement (which is what the two named threats — install-time
exfiltration, and the phase-2 cutoff — actually need), but not full
container-grade isolation (no separate PID namespace, no cgroup resource
limits, a runaway process can still consume host CPU/memory up to the
timeout). Approach B's move to real container orchestration is still the
eventual, more complete answer; this is what's actually available and
verifiable on this machine today.

## Environment isolation (found integrating T6, fixed here — 2026-09-22)

The filesystem/network sandboxing above says nothing about environment
*variables*. `subprocess.run()` with no `env=` override inherits the
**full parent process environment** — including secrets like
`LEGWORK_LLM_API_KEY` (llm_client.py). During the install phase, network is
open: a malicious install script wouldn't even need file access to
exfiltrate that key, `os.environ` is enough on its own. `install()` and
`invoke()` now build a minimal environment (PATH + a workdir-scoped HOME)
rather than inheriting anything, with an `extra_env` parameter for the one
thing callers actually need to add (e.g. prepending a fresh venv's `bin/`
to PATH so `pip`/`python` resolve there, not to Legwork's own environment).
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

INSTALL_TIMEOUT_SECONDS = 120
INVOKE_TIMEOUT_SECONDS = 60

# Deliberately narrow — no inherited secrets. /opt/homebrew is included
# since that's where Homebrew-installed tools (including sandbox-exec's own
# dependencies on some setups) live on Apple Silicon Macs.
MINIMAL_PATH = "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/opt/homebrew/sbin"


class SandboxUnavailableError(Exception):
    """No working sandbox backend on this platform. Never fall back to
    running unsandboxed — that would silently discard the security
    guarantees this module exists to provide."""


class DependencyInstallError(Exception):
    """The install-phase command exited non-zero."""


class InstallTimeoutExceeded(Exception):
    """The install-phase command exceeded INSTALL_TIMEOUT_SECONDS."""


class WrapperRuntimeError(Exception):
    """The invoke-phase command exited non-zero."""


class TimeoutExceeded(Exception):
    """The invoke-phase command exceeded INVOKE_TIMEOUT_SECONDS."""


@dataclass(frozen=True)
class ExecutionResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float


def _check_backend_available() -> None:
    if platform.system() != "Darwin":
        raise SandboxUnavailableError(
            f"No sandbox backend for platform {platform.system()!r} — v1 only "
            "implements macOS (sandbox-exec). Docker-based orchestration for "
            "Linux/Windows is real follow-up work, not solved here."
        )
    if shutil.which("sandbox-exec") is None:
        raise SandboxUnavailableError(
            "sandbox-exec not found on PATH — expected to be present on macOS "
            "by default. Refusing to run unsandboxed."
        )


def _generate_profile(workdir: Path, allow_network: bool) -> str:
    real_workdir = str(workdir.resolve())
    home = str(Path.home().resolve())
    lines = [
        "(version 1)",
        "(deny default)",
        "(allow process-fork)",
        "(allow process-exec)",
        "(allow sysctl-read)",
        "(allow mach-lookup)",
        "(allow iokit-open)",
        "(allow signal (target self))",
        # Reads: allowed everywhere EXCEPT under the user's home directory
        # (needed for Python/pip/system libraries, which mostly live
        # outside $HOME) — then separately re-allowed for the sandbox
        # workdir specifically, even though it's under $HOME. This matters
        # most during the install phase, when network is also open:
        # unrestricted read + open network is the exfiltration path named
        # as the primary attack surface (env vars, SSH keys, cloud
        # credentials all live under $HOME).
        f'(allow file-read* (require-not (subpath "{home}")))',
        f'(allow file-read* (subpath "{real_workdir}"))',
        f'(allow file-write* (subpath "{real_workdir}"))',
    ]
    if allow_network:
        lines.append("(allow network*)")
    return "\n".join(lines)


def _build_env(workdir: Path, extra_env: dict[str, str] | None) -> dict[str, str]:
    """A minimal environment, NOT inherited from the parent process — see
    the module docstring's "Environment isolation" section for why."""
    env = {"PATH": MINIMAL_PATH, "HOME": str(workdir.resolve())}
    if extra_env:
        env.update(extra_env)
    return env


def _run_sandboxed(
    command: list[str],
    workdir: Path,
    allow_network: bool,
    timeout: float,
    timeout_error: type[Exception],
    extra_env: dict[str, str] | None = None,
) -> ExecutionResult:
    _check_backend_available()
    workdir.mkdir(parents=True, exist_ok=True)
    profile = _generate_profile(workdir, allow_network=allow_network)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".sb", delete=False) as f:
        f.write(profile)
        profile_path = f.name

    full_command = ["sandbox-exec", "-f", profile_path] + command
    started = time.monotonic()
    try:
        result = subprocess.run(
            full_command,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_build_env(workdir, extra_env),
            check=False,  # exit code inspected manually by install()/invoke()
        )
    except subprocess.TimeoutExpired as exc:
        raise timeout_error(
            f"{' '.join(command)!r} exceeded {timeout}s inside the sandbox"
        ) from exc
    finally:
        Path(profile_path).unlink(missing_ok=True)
    duration = time.monotonic() - started

    return ExecutionResult(
        command=command,
        exit_code=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_seconds=duration,
    )


def install(
    command: list[str],
    workdir: Path,
    timeout: float = INSTALL_TIMEOUT_SECONDS,
    extra_env: dict[str, str] | None = None,
) -> ExecutionResult:
    """Run a dependency-install command (e.g. `pip install -r
    requirements.txt`) with network access allowed but filesystem
    reads/writes confined to `workdir` (reads outside $HOME are still
    allowed, for Python/pip/system libraries) and a minimal, non-inherited
    environment (`extra_env` to add anything specific — e.g. a venv's PATH).
    Raises InstallTimeoutExceeded on timeout, DependencyInstallError on a
    non-zero exit."""
    result = _run_sandboxed(
        command,
        workdir,
        allow_network=True,
        timeout=timeout,
        timeout_error=InstallTimeoutExceeded,
        extra_env=extra_env,
    )
    if result.exit_code != 0:
        raise DependencyInstallError(
            f"{' '.join(command)!r} failed (exit {result.exit_code}): {result.stderr.strip()}"
        )
    return result


def invoke(
    command: list[str],
    workdir: Path,
    timeout: float = INVOKE_TIMEOUT_SECONDS,
    extra_env: dict[str, str] | None = None,
) -> ExecutionResult:
    """Run the entrypoint-invocation command with network access cut,
    filesystem reads/writes confined to `workdir` (same read-outside-$HOME
    allowance as install), minimal non-inherited environment. Raises
    TimeoutExceeded on timeout, WrapperRuntimeError on a non-zero exit."""
    result = _run_sandboxed(
        command,
        workdir,
        allow_network=False,
        timeout=timeout,
        timeout_error=TimeoutExceeded,
        extra_env=extra_env,
    )
    if result.exit_code != 0:
        raise WrapperRuntimeError(
            f"{' '.join(command)!r} failed (exit {result.exit_code}): {result.stderr.strip()}"
        )
    return result
