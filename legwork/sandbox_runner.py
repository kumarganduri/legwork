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

**Linux (added 2026-09-26):** the same policy through bubblewrap
(`_bwrap_args`): the system read-only, home directories and /tmp, /run
replaced with empty private tmpfs (stand-in homes then read-only), only
the workdir writable, and a shared network namespace only while
installing, with just the DNS folders put back. Ubuntu 24.04+ needs an
AppArmor profile that allows user namespaces for bwrap alone
(`BWRAP_APPARMOR_PROFILE`; the error message prints the commands). Other
platforms raise `SandboxUnavailableError` rather than silently running
unsandboxed.

**Local sockets (fixed 2026-09-26):** the macOS install profile used to
allow `network*`, which includes connecting to any Unix socket on the
machine — the SSH agent, Docker, password-manager agents. It now allows IP
traffic, the DNS socket and sockets inside the workdir only. On Linux
those sockets live in the hidden directories, so neither phase sees them.

**Also disclosed:** both backends give real filesystem and network
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

import functools
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

INSTALL_TIMEOUT_SECONDS = 120
INVOKE_TIMEOUT_SECONDS = 60

# Deliberately narrow — no inherited secrets. /opt/homebrew is included
# since that's where Homebrew-installed tools (including sandbox-exec's own
# dependencies on some setups) live on Apple Silicon Macs.
MINIMAL_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/opt/homebrew/sbin"

# Toolchains a README's install steps commonly need. Checked on
# MINIMAL_PATH, the only PATH the sandbox sees — so ~/.cargo/bin, ~/go/bin
# and the like don't count even when installed.
_TOOLCHAINS = ("node", "npm", "npx", "bun", "deno", "go", "cargo", "java", "ruby", "gem", "make", "cc", "git", "curl")


def available_toolchains() -> dict[str, bool]:
    return {name: shutil.which(name, path=MINIMAL_PATH) is not None for name in _TOOLCHAINS}


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


_BWRAP_INSTALL_HINT = (
    "Install bubblewrap: `sudo apt install bubblewrap` (Debian/Ubuntu), "
    "`sudo dnf install bubblewrap` (Fedora), `sudo pacman -S bubblewrap` (Arch)."
)


# Ubuntu 24.04+ blocks unprivileged user namespaces through AppArmor unless
# a program's profile allows them. This lets bwrap alone create sandboxes,
# the same way Ubuntu handles Chrome and Flatpak, instead of switching the
# restriction off for the whole system. CI applies exactly these commands.
BWRAP_APPARMOR_PROFILE = """abi <abi/4.0>,
include <tunables/global>

profile bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,
  include if exists <local/bwrap>
}
"""
_APPARMOR_FIX = (
    "Ubuntu 24.04+ restricts user namespaces with AppArmor; allow them for bwrap only:\n"
    "  sudo tee /etc/apparmor.d/bwrap <<'EOF'\n" + BWRAP_APPARMOR_PROFILE + "EOF\n"
    "  sudo apparmor_parser -r /etc/apparmor.d/bwrap\n"
)


def _apparmor_restricts_userns() -> bool:
    try:
        return Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns").read_text().strip() == "1"
    except OSError:
        return False


@functools.lru_cache(maxsize=1)
def _bwrap_probe_error() -> str | None:
    """Why bwrap can't make a sandbox on this machine, or None if it can.
    Checked once: installed isn't enough when unprivileged user namespaces
    are turned off (e.g. Ubuntu 24.04's AppArmor restriction)."""
    try:
        result = subprocess.run(
            ["bwrap", "--unshare-all", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "true"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    return None if result.returncode == 0 else (result.stderr.strip() or f"exit {result.returncode}")


def _check_backend_available() -> None:
    system = platform.system()
    if system == "Darwin":
        if shutil.which("sandbox-exec") is None:
            raise SandboxUnavailableError(
                "sandbox-exec not found on PATH — expected to be present on macOS "
                "by default. Refusing to run unsandboxed."
            )
        return
    if system == "Linux":
        if shutil.which("bwrap") is None:
            raise SandboxUnavailableError(
                f"Legwork's Linux sandbox needs bubblewrap, which isn't installed. {_BWRAP_INSTALL_HINT} "
                "Refusing to run unsandboxed."
            )
        error = _bwrap_probe_error()
        if error:
            raise SandboxUnavailableError(
                f"bubblewrap is installed but can't create a sandbox here ({error}). "
                + (_APPARMOR_FIX if _apparmor_restricts_userns() else "Unprivileged user namespaces are probably disabled. ")
                + "Refusing to run unsandboxed."
            )
        return
    raise SandboxUnavailableError(
        f"No sandbox backend for platform {system!r}: Legwork supports macOS (sandbox-exec) and "
        "Linux (bubblewrap). Refusing to run unsandboxed."
    )


def available() -> bool:
    """Whether this machine can run Legwork's sandbox at all."""
    try:
        _check_backend_available()
    except SandboxUnavailableError:
        return False
    return True


def _hidden_dirs() -> list[Path]:
    """Home directories to replace with an empty tmpfs, parents first."""
    home = Path.home().resolve()
    candidates = {Path("/home"), Path("/root"), home}
    keep = [p for p in candidates if p != Path("/") and p.is_dir()]
    return sorted(keep, key=lambda p: len(p.parts))


def _bwrap_args(workdir: Path, allow_network: bool) -> list[str]:
    """The Linux equivalent of _generate_profile: everything read-only, home
    directories and every place local sockets live (/tmp, /run) replaced by
    empty private tmpfs, only the workdir writable, and no network unless
    `allow_network`. Local sockets — SSH agent, Docker, dbus — sit in
    exactly those hidden places, so they're unreachable in both phases."""
    real_workdir = str(workdir.resolve())
    args = [
        "bwrap",
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--ro-bind", "/", "/",
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",
        "--tmpfs", "/var/tmp",
        "--tmpfs", "/run",
    ]
    if os.path.isdir("/var/run") and not os.path.islink("/var/run"):
        args += ["--tmpfs", "/var/run"]
    if allow_network:
        args.append("--share-net")
        # /etc/resolv.conf usually points into /run; put back only DNS.
        for dns_dir in ("/run/systemd/resolve", "/run/NetworkManager", "/run/resolvconf"):
            args += ["--ro-bind-try", dns_dir, dns_dir]
    hidden = [str(d) for d in _hidden_dirs()]
    for d in hidden:
        args += ["--tmpfs", d]
    args += ["--bind", real_workdir, real_workdir]
    interpreter = interpreter_home()
    if interpreter is not None:
        args += ["--ro-bind", str(interpreter), str(interpreter)]
    # Then make the stand-in homes read-only, so writing there fails as it
    # does on macOS instead of quietly landing in a throwaway tmpfs. The
    # workdir is its own mount and stays writable.
    for d in reversed(hidden):
        args += ["--remount-ro", d]
    args += ["--chdir", real_workdir]
    return args


def _sandboxed_argv(command: list[str], workdir: Path, allow_network: bool, profile_path: Path | None) -> list[str]:
    if platform.system() == "Darwin":
        return ["sandbox-exec", "-f", str(profile_path), *command]
    return [*_bwrap_args(workdir, allow_network), "--", *command]


def interpreter_home() -> Path | None:
    """The Python installation running Legwork, when it lives under $HOME
    (uv's managed Pythons, pyenv). Builds may create their venv from it, so
    the sandbox gets read-only access to exactly this folder — it holds an
    interpreter and its standard library, nothing personal. Without it, a
    Mac with only uv's Python (no Homebrew) couldn't build at all (found in
    pre-launch testing, 2026-09-26)."""
    base = Path(sys.base_prefix).resolve()
    home = Path.home().resolve()
    if base.is_relative_to(home) and base != home and (base / "bin").is_dir():
        return base
    return None


def _ancestors_under_home(path: Path, home: Path) -> list[Path]:
    if not path.is_relative_to(home):
        return []
    return [a for a in [path, *path.parents] if a.is_relative_to(home)]


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
    # A workdir under $HOME (e.g. saved wrappers in ~/.legwork): Python
    # resolves its own binary's real path at startup, which stats every
    # parent directory, and fails with "realpath: Operation not permitted"
    # when those are denied. Allow metadata (not contents, not siblings) on
    # exactly the ancestor directories between $HOME and the workdir.
    home_path, workdir_path = Path(home), Path(real_workdir)
    interpreter = interpreter_home()
    if interpreter is not None:
        lines.append(f'(allow file-read* (subpath "{interpreter}"))')
    metadata = set(_ancestors_under_home(workdir_path, home_path))
    if interpreter is not None:
        metadata |= set(_ancestors_under_home(interpreter, home_path))
    for ancestor in sorted(metadata):
        lines.append(f'(allow file-read-metadata (literal "{ancestor}"))')
    # /usr/bin/python3, git, make, cc are xcrun stubs. With full Xcode
    # selected, their first run caches the tool lookup in xcrun_db in the
    # per-user temp folder; without that write every call fails ("couldn't
    # create cache file", exit 72) — found on the first CI run, 2026-09-26.
    # Only files named xcrun_db* there; the rest of that folder stays shut.
    lines.append('(allow file-write* (regex #"^/private/var/folders/[^/]+/[^/]+/T/xcrun_db"))')
    # `> /dev/null` is in nearly every install script and xcrun stub; without
    # this it fails with "Operation not permitted" (second CI run). Data
    # writes to exactly these two devices, nothing else under /dev.
    lines.append('(allow file-write-data (literal "/dev/null") (literal "/dev/zero"))')
    if allow_network:
        # Internet only. A bare (allow network*) also allowed connecting to
        # any Unix socket on the machine — the SSH agent, Docker (root-
        # equivalent), password-manager and GPG agents — so an install
        # script could use your SSH keys (found 2026-09-26, pre-launch).
        # IP traffic anywhere, DNS's one system socket, and Unix sockets
        # inside the workdir; nothing else local.
        lines += [
            "(allow system-socket)",
            '(allow network-outbound (remote ip "*:*"))',
            '(allow network-inbound (local ip "localhost:*"))',
            '(allow network-bind (local ip "*:*"))',
            '(allow network-outbound (literal "/private/var/run/mDNSResponder"))',
            f'(allow network* (subpath "{real_workdir}"))',
        ]
    return "\n".join(lines)


# Temp files belong in the workdir (TMPDIR). Python and most tools honour
# TMPDIR, but macOS's mktemp ignores it and writes to the per-user folder
# under /var/folders, which the profile doesn't let sandboxed code write —
# so installers doing `mktemp -d` failed (magpie, trending trial
# 2026-09-26). This shim adds `-p "$TMPDIR"` unless the caller named its
# own template (a path, or a bare XXX template meant for the current dir).
_MKTEMP_SHIM = """#!/bin/sh
skip=
for a in "$@"; do
  if [ -n "$skip" ]; then skip=; continue; fi
  case "$a" in
    -t|-p) skip=1 ;;
    -*) ;;
    */*|*XXX*) exec /usr/bin/mktemp "$@" ;;
  esac
done
exec /usr/bin/mktemp -p "$TMPDIR" "$@"
"""


def _build_env(workdir: Path, extra_env: dict[str, str] | None) -> dict[str, str]:
    """A minimal environment, NOT inherited from the parent process — see
    the module docstring's "Environment isolation" section for why."""
    real_workdir = workdir.resolve()
    tmp = real_workdir / ".tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    env = {"PATH": MINIMAL_PATH, "HOME": str(real_workdir), "TMPDIR": str(tmp)}
    if extra_env:
        env.update(extra_env)
    if platform.system() == "Darwin":  # GNU mktemp honours TMPDIR itself
        shim_dir = real_workdir / ".legwork-bin"
        shim_dir.mkdir(exist_ok=True)
        shim = shim_dir / "mktemp"
        shim.write_text(_MKTEMP_SHIM)
        shim.chmod(0o755)
        env["PATH"] = f"{shim_dir}:{env['PATH']}"
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
    profile_path = None
    if platform.system() == "Darwin":
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sb", delete=False) as f:
            f.write(_generate_profile(workdir, allow_network=allow_network))
            profile_path = Path(f.name)

    full_command = _sandboxed_argv(command, workdir, allow_network, profile_path)
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
        if profile_path is not None:
            profile_path.unlink(missing_ok=True)
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


def exec_serve(command: list[str], workdir: Path, extra_env: dict[str, str] | None = None) -> NoReturn:
    """Replace this process with `command` running under the invoke-phase
    policy (network off, reads/writes confined to `workdir`, minimal env),
    with no timeout: an MCP server lives as long as the client that launched
    it, and stdio is inherited directly because stdio IS the MCP transport.
    Never returns. The profile is written into `workdir` rather than a temp
    file, since nothing is left running to delete a temp file afterwards."""
    _check_backend_available()
    profile_path = None
    if platform.system() == "Darwin":
        profile_path = workdir / ".legwork-serve.sb"
        profile_path.write_text(_generate_profile(workdir, allow_network=False))
    argv = _sandboxed_argv(command, workdir, allow_network=False, profile_path=profile_path)
    os.chdir(workdir)
    os.execve(shutil.which(argv[0]), argv, _build_env(workdir, extra_env))
