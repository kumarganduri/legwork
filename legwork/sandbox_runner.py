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
traffic, the DNS socket and sockets inside the workdir only. On Linux,
path sockets live in the hidden directories, and abstract sockets (which
belong to the network namespace install shares with the host) are covered
by a seccomp filter that refuses AF_UNIX sockets during install
(`no_unix_sockets_filter`, x86-64 and ARM64). The macOS profile also
allows only the system services builds were measured to need
(`_MACH_SERVICES`).

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

import contextlib
import functools
import os
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

# AI repos often pull PyTorch: 5.3 GB on Linux (CUDA build). That took 66s
# on a GitHub runner's network and would take minutes on a home connection
# (measured 2026-09-26), so installs get 15 minutes.
INSTALL_TIMEOUT_SECONDS = 900
# A self-test that loads an AI model offline (docling, rembg) takes over a
# minute on first load (2026-10-01), so self-tests get 3 minutes.
INVOKE_TIMEOUT_SECONDS = 180

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


def _bwrap_args(
    workdir: Path,
    allow_network: bool,
    extra_writable: tuple[Path, ...] = (),
    read_only: tuple[Path, ...] = (),
    die_with_parent: bool = True,
) -> list[str]:
    """The Linux equivalent of _generate_profile: everything read-only, home
    directories and every place local sockets live (/tmp, /run) replaced by
    empty private tmpfs, only the workdir writable, and no network unless
    `allow_network`. Local sockets — SSH agent, Docker, dbus — sit in
    exactly those hidden places, so they're unreachable in both phases."""
    real_workdir = str(workdir.resolve())
    args = [
        "bwrap",
        "--unshare-all",
        *(["--die-with-parent"] if die_with_parent else []),
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
    for d in extra_writable:
        args += ["--bind", str(Path(d).resolve()), str(Path(d).resolve())]
    for d in read_only:  # `legwork serve --allow-read`
        args += ["--ro-bind", str(Path(d).resolve()), str(Path(d).resolve())]
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


# --- Linux: no Unix sockets while installing ----------------------------------
#
# During install the sandbox shares the host's network namespace (it needs
# the internet), and abstract Unix sockets belong to the network namespace,
# not the filesystem — so hiding /tmp and /run doesn't hide them. Services
# such as an X11 display listen on them. This seccomp filter makes
# socket(AF_UNIX, ...) fail for install commands; internet sockets and
# socketpair() (what pipes between processes use) still work. Syscalls from
# a non-native ABI (32-bit, x32) are refused outright, since their socket
# calls can't be inspected. Added 2026-09-27.

_BPF_LD_W_ABS, _BPF_JEQ_K, _BPF_JGE_K, _BPF_RET_K = 0x20, 0x15, 0x35, 0x06
_SECCOMP_RET_ALLOW = 0x7FFF0000
_SECCOMP_RET_ERRNO = 0x00050000
_EAFNOSUPPORT, _ENOSYS = 97, 38
_AF_UNIX = 1
# machine -> (AUDIT_ARCH value, socket syscall number)
_SECCOMP_ARCHES = {
    "x86_64": (0xC000003E, 41),
    "amd64": (0xC000003E, 41),
    "aarch64": (0xC00000B7, 198),
    "arm64": (0xC00000B7, 198),
}


def _bpf(code: int, jt: int, jf: int, k: int) -> bytes:
    return struct.pack("<HBBI", code, jt, jf, k)


def no_unix_sockets_filter(machine: str | None = None) -> bytes | None:
    """The seccomp program (for bwrap --seccomp), or None on an architecture
    this doesn't cover."""
    arch = _SECCOMP_ARCHES.get((machine or platform.machine()).lower())
    if arch is None:
        return None
    audit_arch, socket_nr = arch
    deny = _SECCOMP_RET_ERRNO | _EAFNOSUPPORT
    return b"".join(
        [
            _bpf(_BPF_LD_W_ABS, 0, 0, 4),  # 0: A = arch
            _bpf(_BPF_JEQ_K, 1, 0, audit_arch),  # 1: native -> 3
            _bpf(_BPF_RET_K, 0, 0, _SECCOMP_RET_ERRNO | _ENOSYS),  # 2: other ABIs refused
            _bpf(_BPF_LD_W_ABS, 0, 0, 0),  # 3: A = syscall number
            _bpf(_BPF_JGE_K, 3, 0, 0x40000000),  # 4: x32 calls -> 8
            _bpf(_BPF_JEQ_K, 0, 3, socket_nr),  # 5: socket() ? 6 : 9
            _bpf(_BPF_LD_W_ABS, 0, 0, 16),  # 6: A = domain (args[0], low word)
            _bpf(_BPF_JEQ_K, 0, 1, _AF_UNIX),  # 7: AF_UNIX ? 8 : 9
            _bpf(_BPF_RET_K, 0, 0, deny),  # 8: refused
            _bpf(_BPF_RET_K, 0, 0, _SECCOMP_RET_ALLOW),  # 9: allowed
        ]
    )


def _sandboxed_argv(
    command: list[str],
    workdir: Path,
    allow_network: bool,
    profile_path: Path | None,
    extra_writable: tuple[Path, ...] = (),
    seccomp_fd: int | None = None,
    read_only: tuple[Path, ...] = (),
    die_with_parent: bool = True,
) -> list[str]:
    if platform.system() == "Darwin":
        return ["sandbox-exec", "-f", str(profile_path), *command]
    seccomp = ["--seccomp", str(seccomp_fd)] if seccomp_fd is not None else []
    return [*_bwrap_args(workdir, allow_network, extra_writable, read_only, die_with_parent), *seccomp, "--", *command]


_MACH_SERVICES = (
    "com.apple.system.opendirectoryd.libinfo",
    "com.apple.system.opendirectoryd.membership",
    "com.apple.system.notification_center",
    "com.apple.system.logger",
    "com.apple.logd",
    "com.apple.diagnosticd",
    "com.apple.SystemConfiguration.configd",
    "com.apple.SystemConfiguration.DNSConfiguration",
    "com.apple.dnssd.service",
    "com.apple.trustd",
    "com.apple.trustd.agent",
    "com.apple.cfprefsd.daemon",
    "com.apple.cfprefsd.agent",
)


class SandboxGrantError(Exception):
    """A requested --allow-read folder isn't safe or doesn't exist."""


# Never grantable, even on request: where credentials and private data live.
# Relative to $HOME; a grant equal to or inside any of these is refused.
_SECRET_DIRS = (
    ".ssh", ".aws", ".gnupg", ".kube", ".docker", ".config", ".azure", ".password-store",
    ".netrc", ".legwork", "Library/Keychains", "Library/Cookies", "Library/Application Support",
    "Library/Messages", "Library/Mail", "Library/Safari", "Library/Containers",
)


def check_read_grants(paths: list[str] | tuple[str, ...]) -> tuple[Path, ...]:
    """Validate `legwork serve --allow-read` folders: they must exist, and
    can't be /, your home folder or anything above it, or a place secrets
    live (~/.ssh, ~/.aws, ~/.config, keychains...). Returns resolved paths."""
    home = Path.home().resolve()
    granted = []
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        # Refusals come before the existence check: a secret folder is refused
        # as such whether or not it exists here.
        if path == Path("/") or home.is_relative_to(path):
            raise SandboxGrantError(
                f"--allow-read {raw}: that's your whole home folder (or above it). "
                "Grant the specific folder the tool needs, e.g. ~/Downloads."
            )
        for secret in _SECRET_DIRS:
            if path.is_relative_to(home / secret):
                raise SandboxGrantError(f"--allow-read {raw}: ~/{secret} holds credentials or private data; refusing.")
        if not path.exists():
            raise SandboxGrantError(f"--allow-read {raw}: no such file or folder")
        granted.append(path)
    return tuple(granted)


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


def _generate_profile(
    workdir: Path, allow_network: bool, extra_writable: tuple[Path, ...] = (), read_only: tuple[Path, ...] = ()
) -> str:
    real_workdir = str(workdir.resolve())
    home = str(Path.home().resolve())
    lines = [
        "(version 1)",
        "(deny default)",
        "(allow process-fork)",
        "(allow process-exec)",
        "(allow sysctl-read)",
        # System services: only what Python, pip, npm, git and curl were
        # measured to need (user lookups, DNS and network config, TLS
        # certificate checks, logging, preferences). A bare (allow
        # mach-lookup) also reached services that open apps, read the
        # clipboard or script other apps (tightened 2026-09-26).
        "(allow mach-lookup " + " ".join(f'(global-name "{name}")' for name in _MACH_SERVICES) + ")",
        "(allow iokit-open)",
        # Its own children too, which inherit this sandbox: moviepy could
        # start ffmpeg but not stop it (os.kill: Operation not permitted,
        # 2026-10-01). Processes outside the sandbox stay out of reach.
        "(allow signal (target same-sandbox))",
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
    extra = [Path(d).resolve() for d in extra_writable]
    for d in extra:
        lines.append(f'(allow file-read* file-write* (subpath "{d}"))')
    grants = [Path(d).resolve() for d in read_only]
    for d in grants:  # `legwork serve --allow-read`: read-only, exactly this folder
        lines.append(f'(allow file-read* (subpath "{d}"))')
    metadata = set(_ancestors_under_home(workdir_path, home_path))
    for d in [*extra, *grants]:
        metadata |= set(_ancestors_under_home(d, home_path))
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
    extra_writable: tuple[Path, ...] = (),
    read_only: tuple[Path, ...] = (),
) -> ExecutionResult:
    _check_backend_available()
    workdir.mkdir(parents=True, exist_ok=True)
    profile_path = None
    if platform.system() == "Darwin":
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sb", delete=False) as f:
            f.write(_generate_profile(workdir, allow_network=allow_network, extra_writable=extra_writable, read_only=read_only))
            profile_path = Path(f.name)

    started = time.monotonic()
    with contextlib.ExitStack() as stack:
        seccomp_fd = None
        program = no_unix_sockets_filter() if platform.system() == "Linux" and allow_network else None
        if program is not None:
            seccomp_file = stack.enter_context(tempfile.TemporaryFile())
            seccomp_file.write(program)
            seccomp_file.seek(0)
            seccomp_fd = seccomp_file.fileno()
        full_command = _sandboxed_argv(command, workdir, allow_network, profile_path, extra_writable, seccomp_fd, read_only)
        try:
            result = subprocess.run(
                full_command,
                pass_fds=(seccomp_fd,) if seccomp_fd is not None else (),
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
    cache_dir: Path | None = None,
) -> ExecutionResult:
    """Run a dependency-install command (e.g. `pip install -r
    requirements.txt`) with network access allowed but filesystem
    reads/writes confined to `workdir` (reads outside $HOME are still
    allowed, for Python/pip/system libraries) and a minimal, non-inherited
    environment (`extra_env` to add anything specific — e.g. a venv's PATH).
    `cache_dir`, also writable, holds pip/npm/uv downloads so a retry in the
    same build doesn't fetch a multi-GB dependency again.
    Raises InstallTimeoutExceeded on timeout, DependencyInstallError on a
    non-zero exit."""
    env = dict(extra_env or {})
    writable: tuple[Path, ...] = ()
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        writable = (cache_dir,)
        env.update(
            PIP_CACHE_DIR=str(cache_dir / "pip"),
            npm_config_cache=str(cache_dir / "npm"),
            UV_CACHE_DIR=str(cache_dir / "uv"),
            XDG_CACHE_HOME=str(cache_dir / "xdg"),
        )
    result = _run_sandboxed(
        command,
        workdir,
        allow_network=True,
        timeout=timeout,
        timeout_error=InstallTimeoutExceeded,
        extra_env=env,
        extra_writable=writable,
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


def exec_serve(
    command: list[str],
    workdir: Path,
    extra_env: dict[str, str] | None = None,
    allow_read: tuple[Path, ...] = (),
    allow_network: bool = False,
) -> NoReturn:
    """Replace this process with `command` running under the invoke-phase
    policy (network off, reads/writes confined to `workdir`, minimal env),
    with no timeout: an MCP server lives as long as the client that launched
    it, and stdio is inherited directly because stdio IS the MCP transport.
    Never returns. The profile is written into `workdir` rather than a temp
    file, since nothing is left running to delete a temp file afterwards.

    `allow_read` (already checked by check_read_grants) and `allow_network`
    are the user's explicit grants from `legwork serve --allow-read/--allow-net`;
    without them a served tool sees no files of yours and no network. With
    network on, Linux gets the same no-Unix-sockets filter as installs."""
    _check_backend_available()
    profile_path = None
    if platform.system() == "Darwin":
        profile_path = workdir / ".legwork-serve.sb"
        profile_path.write_text(_generate_profile(workdir, allow_network=allow_network, read_only=allow_read))
    seccomp_fd = None
    if platform.system() == "Linux" and allow_network:
        program = no_unix_sockets_filter()
        if program is not None:
            seccomp_fd = os.memfd_create("legwork-seccomp")
            os.write(seccomp_fd, program)
            os.lseek(seccomp_fd, 0, os.SEEK_SET)
            os.set_inheritable(seccomp_fd, True)  # must survive the exec into bwrap
    # No --die-with-parent here: it fires when the *thread* that started the
    # server exits, and MCP clients (the hub among them) may start servers
    # from short-lived threads; the hub's tools died right after installing
    # on Linux (2026-09-27). A served MCP server exits when its client closes
    # stdin anyway.
    argv = _sandboxed_argv(
        command, workdir, allow_network=allow_network, profile_path=profile_path,
        seccomp_fd=seccomp_fd, read_only=allow_read, die_with_parent=False,
    )
    os.chdir(workdir)
    os.execve(shutil.which(argv[0]), argv, _build_env(workdir, extra_env))
