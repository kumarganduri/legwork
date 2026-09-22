"""Tests run the REAL sandbox-exec, not mocks — mocking subprocess for a
security-critical isolation module would validate nothing about the
properties that actually matter (can it write outside its workdir? can it
read outside $HOME? does the network cutoff actually cut?). Skipped on
non-macOS since sandbox-exec is macOS-only (see module docstring for why)."""

from __future__ import annotations

import platform
import shutil
import socket
from pathlib import Path
from unittest.mock import patch

import pytest

from legwork.sandbox_runner import (
    DependencyInstallError,
    InstallTimeoutExceeded,
    SandboxUnavailableError,
    TimeoutExceeded,
    WrapperRuntimeError,
    install,
    invoke,
)

pytestmark = pytest.mark.skipif(
    platform.system() != "Darwin", reason="sandbox-exec is macOS-only (v1 scope, see module docstring)"
)

# Deliberately NOT sys.executable: in this dev checkout that resolves to
# .venv/bin/python, which lives under $HOME — and the sandbox correctly
# blocks reading (and therefore exec'ing) anything under $HOME outside the
# workdir. That's the module working as designed, not a bug. The real
# pipeline's install/invoke phases run against a venv created fresh inside
# `workdir` (which the sandbox does allow) or a system interpreter like
# this one — never the surrounding dev environment's own venv.
SYSTEM_PYTHON = shutil.which("python3", path="/usr/bin:/opt/homebrew/bin") or "/usr/bin/python3"


def _network_reachable() -> bool:
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=3).close()
        return True
    except OSError:
        return False


# --- backend detection -------------------------------------------------


def test_unavailable_on_non_darwin_platform(tmp_path):
    with patch("legwork.sandbox_runner.platform.system", return_value="Linux"):
        with pytest.raises(SandboxUnavailableError, match="Linux"):
            install([SYSTEM_PYTHON, "-c", "print(1)"], tmp_path)


# --- filesystem confinement ----------------------------------------------


def test_invoke_write_inside_workdir_succeeds(tmp_path):
    result = invoke([SYSTEM_PYTHON, "-c", "open('out.txt', 'w').write('ok')"], tmp_path)
    assert result.exit_code == 0
    assert (tmp_path / "out.txt").read_text() == "ok"


def test_invoke_write_outside_workdir_fails(tmp_path):
    home_target = str(Path.home() / "legwork-escape-test.txt")
    with pytest.raises(WrapperRuntimeError):
        invoke([SYSTEM_PYTHON, "-c", f"open({home_target!r}, 'w').write('escaped')"], tmp_path)
    assert not Path(home_target).exists()


def test_invoke_read_outside_home_succeeds(tmp_path):
    """System paths (outside $HOME) must stay readable — Python/pip need
    this to function at all."""
    result = invoke([SYSTEM_PYTHON, "-c", "print(open('/etc/hosts').read()[:1])"], tmp_path)
    assert result.exit_code == 0


def test_invoke_read_elsewhere_in_home_fails(tmp_path):
    """The exfiltration-relevant restriction: reading arbitrary files
    under $HOME (SSH keys, cloud credentials, shell history) outside the
    sandbox workdir must fail, even though workdir itself is readable.
    Uses a file this test creates and controls, not a dotfile that may or
    may not exist on the machine running the suite."""
    marker = Path.home() / f"legwork-read-test-{id(tmp_path)}.txt"
    marker.write_text("should not be readable from inside the sandbox")
    try:
        with pytest.raises(WrapperRuntimeError, match="PermissionError|Operation not permitted"):
            invoke([SYSTEM_PYTHON, "-c", f"open({str(marker)!r}).read()"], tmp_path)
    finally:
        marker.unlink(missing_ok=True)


# --- network policy: the actual two-phase requirement ----------------------


# A raw socket connect, not a full HTTPS request — testing whether the
# sandbox permits outbound connections at all, not any particular site's
# response behavior (a bare-IP HTTPS request to Cloudflare returns 403,
# which is that site's policy, not a sandbox failure — found while writing
# this test against the real network instead of assuming urlopen() would
# just work).
_SOCKET_CONNECT_SNIPPET = (
    "import socket; s = socket.create_connection(('1.1.1.1', 443), timeout=5); s.close()"
)


@pytest.mark.skipif(not _network_reachable(), reason="no network in this environment")
def test_install_phase_allows_network(tmp_path):
    result = install([SYSTEM_PYTHON, "-c", _SOCKET_CONNECT_SNIPPET], tmp_path)
    assert result.exit_code == 0


@pytest.mark.skipif(not _network_reachable(), reason="no network in this environment")
def test_invoke_phase_blocks_network(tmp_path):
    with pytest.raises(WrapperRuntimeError):
        invoke([SYSTEM_PYTHON, "-c", _SOCKET_CONNECT_SNIPPET], tmp_path)


# --- timeouts ---------------------------------------------------------------


def test_install_timeout_raises_install_timeout_exceeded(tmp_path):
    with pytest.raises(InstallTimeoutExceeded):
        install([SYSTEM_PYTHON, "-c", "import time; time.sleep(5)"], tmp_path, timeout=1)


def test_invoke_timeout_raises_timeout_exceeded(tmp_path):
    with pytest.raises(TimeoutExceeded):
        invoke([SYSTEM_PYTHON, "-c", "import time; time.sleep(5)"], tmp_path, timeout=1)


# --- environment isolation (found integrating T6, fixed in T5) -------------


def test_secrets_in_calling_process_env_are_not_inherited(tmp_path, monkeypatch):
    """The actual gap found: subprocess.run() with no env= override
    inherits the full parent environment. Set a fake secret in THIS
    process's env and confirm the sandboxed child can't see it."""
    monkeypatch.setenv("LEGWORK_LLM_API_KEY", "sk-should-not-leak")
    result = invoke(
        [SYSTEM_PYTHON, "-c", "import os; print(repr(os.environ.get('LEGWORK_LLM_API_KEY')))"],
        tmp_path,
    )
    assert result.stdout.strip() == "None"


def test_extra_env_is_applied(tmp_path):
    result = invoke(
        [SYSTEM_PYTHON, "-c", "import os; print(os.environ['MY_VAR'])"],
        tmp_path,
        extra_env={"MY_VAR": "hello"},
    )
    assert result.stdout.strip() == "hello"


def test_path_defaults_to_minimal_not_inherited(tmp_path):
    """Don't monkeypatch THIS process's real PATH — that would also break
    the backend check (shutil.which('sandbox-exec')), which runs in-process,
    not in the sandbox. Instead confirm the child sees exactly the module's
    declared minimal PATH, not whatever the parent happens to have."""
    from legwork.sandbox_runner import MINIMAL_PATH

    result = invoke([SYSTEM_PYTHON, "-c", "import os; print(os.environ['PATH'])"], tmp_path)
    assert result.stdout.strip() == MINIMAL_PATH


# --- exit codes --------------------------------------------------------------


def test_install_nonzero_exit_raises_dependency_install_error(tmp_path):
    with pytest.raises(DependencyInstallError, match="boom"):
        install([SYSTEM_PYTHON, "-c", "import sys; print('boom', file=sys.stderr); sys.exit(1)"], tmp_path)


def test_invoke_nonzero_exit_raises_wrapper_runtime_error(tmp_path):
    with pytest.raises(WrapperRuntimeError, match="kaboom"):
        invoke([SYSTEM_PYTHON, "-c", "import sys; print('kaboom', file=sys.stderr); sys.exit(1)"], tmp_path)


def test_successful_invoke_returns_execution_result(tmp_path):
    result = invoke([SYSTEM_PYTHON, "-c", "print('hello')"], tmp_path)
    assert result.exit_code == 0
    assert result.stdout.strip() == "hello"
    assert result.duration_seconds >= 0
