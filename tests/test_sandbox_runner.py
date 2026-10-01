"""Tests run the REAL sandbox-exec, not mocks — mocking subprocess for a
security-critical isolation module would validate nothing about the
properties that actually matter (can it write outside its workdir? can it
read outside $HOME? does the network cutoff actually cut?). Skipped on
non-macOS since sandbox-exec is macOS-only (see module docstring for why)."""

from __future__ import annotations

import os
import platform
import shutil
import socket
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from legwork import sandbox_runner
from legwork.sandbox_runner import (
    check_read_grants,
    DependencyInstallError,
    InstallTimeoutExceeded,
    SandboxUnavailableError,
    TimeoutExceeded,
    WrapperRuntimeError,
    available_toolchains,
    install,
    invoke,
)

pytestmark = pytest.mark.skipif(not sandbox_runner.available(), reason="no sandbox backend on this machine")
macos_only = pytest.mark.skipif(platform.system() != "Darwin", reason="tests a sandbox-exec mechanism")
linux_only = pytest.mark.skipif(platform.system() != "Linux", reason="tests the bubblewrap backend")
# Outside the sandbox, a hidden file reads as "not permitted" on macOS and
# "doesn't exist" on Linux (home is an empty tmpfs there).
_HIDDEN = "PermissionError|Operation not permitted|FileNotFoundError|No such file"

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


def test_unavailable_on_an_unsupported_platform(tmp_path):
    with (
        patch("legwork.sandbox_runner.platform.system", return_value="Windows"),
        pytest.raises(SandboxUnavailableError, match="Windows"),
    ):
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
    under $HOME (SSH keys, cloud credentials, ~/.legwork.env) outside the
    sandbox workdir must fail, even though workdir itself is readable.
    Uses a file this test creates and controls, not a dotfile that may or
    may not exist on the machine running the suite."""
    marker = Path.home() / f"legwork-read-test-{id(tmp_path)}.txt"
    marker.write_text("should not be readable from inside the sandbox")
    try:
        with pytest.raises(WrapperRuntimeError, match=_HIDDEN):
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
    declared minimal PATH (behind Legwork's own shim folder), not whatever
    the parent happens to have."""
    from legwork.sandbox_runner import MINIMAL_PATH

    result = invoke([SYSTEM_PYTHON, "-c", "import os; print(os.environ['PATH'])"], tmp_path)
    shim = f"{tmp_path.resolve() / '.legwork-bin'}:" if platform.system() == "Darwin" else ""
    assert result.stdout.strip() == f"{shim}{MINIMAL_PATH}"


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


# --- workdir under $HOME (saved wrappers live in ~/.legwork) ---------------


def test_venv_in_a_workdir_under_home_runs():
    """Python resolves its own binary's real path at startup, which stats
    every parent folder; those are under $HOME, which the sandbox otherwise
    denies. Failed with 'realpath: Operation not permitted' before the
    ancestor-metadata allowance."""
    root = Path.home() / f".legwork-test-{os.getpid()}"
    workdir = root / "build" / "attempt-1"
    workdir.mkdir(parents=True)
    sibling = root / "sibling-secret.txt"
    sibling.write_text("still off limits")
    try:
        install([SYSTEM_PYTHON, "-m", "venv", str(workdir / ".venv")], workdir)
        venv_python = str(workdir / ".venv" / "bin" / "python")
        result = invoke([venv_python, "-c", "import os; print(os.getcwd())"], workdir)
        assert result.stdout.strip() == str(workdir.resolve())
        with pytest.raises(WrapperRuntimeError, match=_HIDDEN):
            invoke([venv_python, "-c", f"open({str(sibling)!r}).read()"], workdir)
    finally:
        shutil.rmtree(root)


# --- exec_serve: what `legwork serve` hands to sandbox-exec -----------------


@macos_only
def test_exec_serve_uses_no_network_profile_and_minimal_env(tmp_path, monkeypatch):
    from legwork import sandbox_runner

    monkeypatch.setenv("LEGWORK_LLM_API_KEY", "sk-should-not-leak")
    captured = {}

    def fake_execve(path, args, env):
        captured.update(path=path, args=args, env=env)
        raise SystemExit(0)

    monkeypatch.chdir(tmp_path)
    with patch("legwork.sandbox_runner.os.execve", side_effect=fake_execve), pytest.raises(SystemExit):
        sandbox_runner.exec_serve(["/bin/echo", "hi"], tmp_path, {"EXTRA": "1"})

    profile = captured["args"][2]  # inline: nothing for the tool to rewrite in its workdir
    assert "(allow network*)" not in profile
    assert not list(tmp_path.glob("*.sb"))
    assert captured["args"][-2:] == ["/bin/echo", "hi"]
    assert "LEGWORK_LLM_API_KEY" not in captured["env"]
    assert captured["env"]["EXTRA"] == "1"


def test_available_toolchains_only_counts_the_sandbox_path(tmp_path, monkeypatch):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "node").write_text("#!/bin/sh\n")
    (fake_bin / "node").chmod(0o755)
    monkeypatch.setattr("legwork.sandbox_runner.MINIMAL_PATH", str(fake_bin))
    found = available_toolchains()
    assert found["node"] is True
    assert found["go"] is False


def test_temp_files_work_inside_the_sandbox(tmp_path):
    """mktemp with no TMPDIR goes to /var/folders, which the sandbox blocks;
    installers that unpack into a temp dir failed (trending trial)."""
    result = install(["/bin/sh", "-c", "d=$(mktemp -d) && echo ok > $d/f && cat $d/f && echo $d"], tmp_path)
    out = result.stdout.split()
    assert out[0] == "ok"
    assert out[1].startswith(str(tmp_path.resolve()))


@macos_only
@pytest.mark.parametrize(
    ("args", "where"),
    [("-d", "tmp"), ("", "tmp"), ("-d -t prefix", "tmp"), ("-d mine.XXXXXX", "workdir"), ("-d $PWD/sub.XXXXXX", "workdir")],
)
def test_mktemp_shim_keeps_explicit_templates(tmp_path, args, where):
    result = install(["/bin/sh", "-c", f"mktemp {args}"], tmp_path)
    created = tmp_path.resolve() / result.stdout.strip()  # a bare template gives a relative path
    parent = tmp_path.resolve() / ".tmp" if where == "tmp" else tmp_path.resolve()
    assert created.parent == parent


@macos_only
def test_only_the_xcrun_cache_is_writable_in_the_user_temp_folder(tmp_path):
    """xcrun stubs (/usr/bin/python3, git) need to write xcrun_db in the
    per-user temp folder on machines with full Xcode; nothing else there."""
    import subprocess

    user_tmp = Path(subprocess.run(["getconf", "DARWIN_USER_TEMP_DIR"], capture_output=True, text=True, check=True).stdout.strip())
    allowed, denied = user_tmp / "xcrun_db-legwork-test", user_tmp / "legwork-sandbox-test"
    try:
        install([SYSTEM_PYTHON, "-c", f"open({str(allowed)!r}, 'w').write('x')"], tmp_path)
        assert allowed.exists()
        with pytest.raises(DependencyInstallError, match="not permitted"):
            install([SYSTEM_PYTHON, "-c", f"open({str(denied)!r}, 'w').write('x')"], tmp_path)
        assert not denied.exists()
    finally:
        allowed.unlink(missing_ok=True)
        denied.unlink(missing_ok=True)


def test_output_can_be_discarded_to_dev_null(tmp_path):
    result = install(["/bin/sh", "-c", "echo hidden > /dev/null && echo shown 2>/dev/null"], tmp_path)
    assert result.stdout.strip() == "shown"


@macos_only  # bwrap gives the sandbox its own private /dev
def test_other_devices_stay_unwritable(tmp_path):
    with pytest.raises(DependencyInstallError, match="not permitted"):
        install(["/bin/sh", "-c", "echo x > /dev/tty.legwork-test"], tmp_path)


def test_install_cannot_reach_local_unix_sockets(tmp_path):
    """With network on, install code must still not reach the machine's own
    Unix sockets — the SSH agent, Docker, password managers. A bare
    (allow network*) permitted all of them (found 2026-09-26)."""
    import socket
    import tempfile

    sock_dir = Path(tempfile.mkdtemp(dir="/tmp"))  # AF_UNIX paths must be short
    sock_path = sock_dir / "agent.sock"
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(sock_path))
    server.listen(1)
    try:
        # On Linux the socket can't even be created during install (seccomp);
        # on macOS it can, but connecting is refused. Either is "blocked".
        code = (
            "import socket, sys\n"
            "try:\n    s = socket.socket(socket.AF_UNIX); s.connect(sys.argv[1]); print('connected')\n"
            "except OSError:\n    print('blocked')\n"
        )
        result = install([SYSTEM_PYTHON, "-c", code, str(sock_path)], tmp_path)
        assert result.stdout.strip() == "blocked"
    finally:
        server.close()
        sock_path.unlink(missing_ok=True)
        sock_dir.rmdir()


@linux_only
def test_exec_serve_runs_bwrap_without_network(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGWORK_LLM_API_KEY", "sk-should-not-leak")
    captured = {}

    def fake_execve(path, args, env):
        captured.update(path=path, args=args, env=env)
        raise SystemExit(0)

    monkeypatch.chdir(tmp_path)
    with patch("legwork.sandbox_runner.os.execve", side_effect=fake_execve), pytest.raises(SystemExit):
        sandbox_runner.exec_serve(["/bin/echo", "hi"], tmp_path)
    assert captured["args"][0] == "bwrap"
    assert "--unshare-all" in captured["args"] and "--share-net" not in captured["args"]
    assert captured["args"][-3:] == ["--", "/bin/echo", "hi"]
    assert "LEGWORK_LLM_API_KEY" not in captured["env"]


def test_python_running_legwork_is_readable_but_only_that_install(tmp_path):
    """Under uvx, Legwork runs on uv's Python in ~/.local/share/uv; a Mac
    with no Homebrew Python builds venvs from it. The sandbox may read that
    one install, nothing beside it."""
    interpreter = sandbox_runner.interpreter_home()
    if interpreter is None:
        pytest.skip("Legwork isn't running on a Python under $HOME here")
    python = interpreter / "bin" / "python3"
    result = invoke([str(python), "-c", "import sys; print(sys.version_info >= (3, 10))"], tmp_path)
    assert result.stdout.strip() == "True"
    sibling = interpreter.parent / f"legwork-sibling-{os.getpid()}.txt"
    sibling.write_text("not part of the interpreter")
    try:
        with pytest.raises(WrapperRuntimeError, match=_HIDDEN):
            invoke([str(python), "-c", f"open({str(sibling)!r}).read()"], tmp_path)
    finally:
        sibling.unlink()


@macos_only
def test_only_allowlisted_system_services_are_reachable(tmp_path):
    """A bare (allow mach-lookup) let sandboxed code reach every macOS
    service, including ones that open apps, read the clipboard or script
    other apps. Only the measured allowlist remains."""
    profile = sandbox_runner._generate_profile(tmp_path, allow_network=True)
    assert "(allow mach-lookup)" not in profile.text
    assert "com.apple.pasteboard" not in profile.text
    result = sandbox_runner._run_sandboxed(
        ["/bin/sh", "-c", "/usr/bin/pbpaste >/dev/null 2>&1; echo $?"],
        tmp_path,
        allow_network=True,
        timeout=30,
        timeout_error=sandbox_runner.InstallTimeoutExceeded,
    )
    assert result.stdout.strip() != "0"


def test_install_can_use_the_builds_shared_download_cache(tmp_path):
    """Retries in one build share downloads (a PyTorch install is ~3 GB):
    the cache folder is writable, everything else outside the workdir isn't."""
    workdir, cache = tmp_path / "attempt-1", tmp_path / ".download-cache"
    workdir.mkdir()
    result = install(
        ["/bin/sh", "-c", 'echo cached > "$PIP_CACHE_DIR.marker" && echo "$npm_config_cache $UV_CACHE_DIR"'],
        workdir,
        cache_dir=cache,
    )
    assert (tmp_path / ".download-cache" / "pip.marker").read_text().strip() == "cached"
    assert result.stdout.split() == [str(cache / "npm"), str(cache / "uv")]
    # macOS refuses the write; Linux lets it land in the sandbox's private
    # /tmp, which is thrown away. Either way nothing reaches the real one.
    try:
        install(["/bin/sh", "-c", f"echo x > {tmp_path / 'outside.txt'}"], workdir, cache_dir=cache)
    except DependencyInstallError:
        pass
    assert not (tmp_path / "outside.txt").exists()


@linux_only
def test_install_cannot_reach_abstract_unix_sockets(tmp_path):
    """Abstract sockets live in the network namespace, which install shares
    with the host; services like an X11 display listen on them. The seccomp
    filter refuses AF_UNIX sockets during install."""
    import socket

    name = f"legwork-test-{os.getpid()}"
    server = socket.socket(socket.AF_UNIX)
    server.bind("\0" + name)  # abstract: a leading NUL, no file anywhere
    server.listen(1)
    code = (
        "import socket, sys\n"
        "try:\n"
        "    s = socket.socket(socket.AF_UNIX); s.connect('\\0' + sys.argv[1]); print('connected')\n"
        "except OSError as e:\n"
        "    print('blocked', e.errno)\n"
        "socket.socketpair(); print('socketpair ok')\n"
    )
    try:
        result = install([SYSTEM_PYTHON, "-c", code, name], tmp_path)
        assert result.stdout.split("\n")[0].startswith("blocked")
        assert "socketpair ok" in result.stdout
    finally:
        server.close()


@pytest.mark.skipif(not _network_reachable(), reason="no network in this environment")
@pytest.mark.parametrize(
    ("tool", "command"),
    [
        ("npm", "npm install --no-fund --no-audit --silent left-pad >/dev/null && echo ok"),
        ("git", "git clone -q --depth 1 https://github.com/octocat/Hello-World.git hw && echo ok"),
    ],
)
def test_real_installers_work_inside_the_install_phase(tmp_path, tool, command):
    """Every tightening (system-service allowlist, socket rules, the Linux
    seccomp filter) must leave real installers working."""
    if shutil.which(tool, path=sandbox_runner.MINIMAL_PATH) is None:
        pytest.skip(f"{tool} isn't on the sandbox PATH here")
    assert install(["/bin/sh", "-c", command], tmp_path).stdout.strip().endswith("ok")


# --- `legwork serve --allow-read` grants -------------------------------------------


def test_a_granted_folder_is_readable_and_its_neighbours_are_not(tmp_path):
    base = Path.home() / f".legwork-grant-test-{os.getpid()}"
    granted, neighbour = base / "Downloads", base / "Private"
    granted.mkdir(parents=True)
    neighbour.mkdir()
    (granted / "report.txt").write_text("quarterly numbers")
    (neighbour / "diary.txt").write_text("secret")
    workdir = tmp_path / "attempt"
    workdir.mkdir()
    grants = sandbox_runner.check_read_grants([str(granted)])
    try:
        def run(code):
            return sandbox_runner._run_sandboxed(
                [SYSTEM_PYTHON, "-c", code], workdir, allow_network=False, timeout=30,
                timeout_error=TimeoutExceeded, read_only=grants,
            )
        assert run(f"print(open({str(granted / 'report.txt')!r}).read())").stdout.strip() == "quarterly numbers"
        denied = run(f"open({str(neighbour / 'diary.txt')!r}).read()")
        assert denied.exit_code != 0
        writing = run(f"open({str(granted / 'new.txt')!r}, 'w').write('x')")
        assert writing.exit_code != 0 and not (granted / "new.txt").exists()  # read-only
    finally:
        shutil.rmtree(base)


@pytest.mark.parametrize(
    "target",
    [
        "~", "/", "~/.ssh", "~/.aws/credentials", "~/.config", "~/Library/Keychains",
        # Folders that CONTAIN secrets, and secret files (pre-launch QA, 2026-10-02)
        "~/Library", "~/Library/Group Containers", "~/.legwork.env", "~/.zsh_history", "~/.npmrc",
        # Different letter case: the same folders on macOS's default filesystem
        "~/.SSH", "~/LIBRARY", "~/Library/KEYCHAINS", "~/.Legwork.env",
    ],
)
def test_grants_refuse_home_and_secret_folders_whether_or_not_they_exist(target):
    with pytest.raises(sandbox_runner.SandboxGrantError, match="whole home folder|credentials"):
        sandbox_runner.check_read_grants([target])


def test_grants_refuse_paths_that_do_not_exist(tmp_path):
    with pytest.raises(sandbox_runner.SandboxGrantError, match="no such"):
        sandbox_runner.check_read_grants([str(tmp_path / "missing")])


@pytest.mark.skipif(not _network_reachable(), reason="no network in this environment")
@pytest.mark.parametrize("allow_net", [False, True])
def test_served_tools_get_network_only_when_granted(tmp_path, allow_net):
    """exec_serve replaces the process, so run it in a child. With the grant,
    internet works; on Linux, Unix sockets stay refused even then."""
    import subprocess
    import sys

    probe = (
        "import socket\n"
        "try:\n    socket.create_connection(('1.1.1.1', 443), timeout=5).close(); print('net:yes')\n"
        "except OSError:\n    print('net:no')\n"
        "try:\n    socket.socket(socket.AF_UNIX); print('unix:yes')\n"
        "except OSError:\n    print('unix:no')\n"
    )
    launcher = (
        "import sys; from pathlib import Path; from legwork import sandbox_runner\n"
        f"sandbox_runner.exec_serve([{SYSTEM_PYTHON!r}, '-c', {probe!r}], Path({str(tmp_path)!r}), "
        f"allow_network={allow_net})\n"
    )
    out = subprocess.run([sys.executable, "-c", launcher], capture_output=True, text=True, timeout=60, check=False).stdout
    assert f"net:{'yes' if allow_net else 'no'}" in out
    if platform.system() == "Linux" and allow_net:
        assert "unix:no" in out


def test_a_tool_can_stop_the_processes_it_started(tmp_path):
    """moviepy starts ffmpeg and terminates it when done; the macOS profile
    only allowed signalling the tool itself (2026-10-01)."""
    code = (
        "import subprocess, sys\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "p.terminate(); p.wait(timeout=10); print('stopped', p.returncode)\n"
    )
    result = invoke([SYSTEM_PYTHON, "-c", code], tmp_path)
    assert "stopped" in result.stdout


def test_a_tool_still_cannot_signal_processes_outside_its_sandbox(tmp_path):
    import subprocess

    outside = subprocess.Popen([SYSTEM_PYTHON, "-c", "import time; time.sleep(60)"])
    try:
        code = (
            "import os, signal\n"
            "try:\n"
            f"    os.kill({outside.pid}, signal.SIGTERM); print('killed')\n"
            "except (PermissionError, ProcessLookupError) as e:\n"
            "    print('refused', type(e).__name__)\n"
        )
        result = invoke([SYSTEM_PYTHON, "-c", code], tmp_path)
        assert "refused" in result.stdout
        assert outside.poll() is None
    finally:
        outside.kill()


@macos_only
def test_a_granted_path_cannot_rewrite_the_sandbox_rules(tmp_path):
    """Paths used to be pasted into the profile text: a folder named like
    SBPL inside an allowed folder turned the sandbox off (pre-launch QA,
    2026-10-02). They're parameters now, so the name is just a name."""
    crafted = tmp_path / 'a")) (allow default) (allow file-read* (literal "b'
    crafted.mkdir()
    (crafted / "ok.txt").write_text("granted file")
    grants = check_read_grants([str(crafted)])
    probe = (
        "import os, socket\n"
        f"print(open({str(crafted / 'ok.txt')!r}).read())\n"
        "for attempt in (lambda: open(os.path.expanduser('~/.zshrc')).read(),\n"
        "                lambda: socket.create_connection(('1.1.1.1', 443), timeout=3),\n"
        "                lambda: open('/private/tmp/legwork-escape-probe', 'w').write('x')):\n"
        "    try:\n        attempt(); print('ESCAPED')\n"
        "    except OSError:\n        print('blocked')\n"
    )
    result = sandbox_runner._run_sandboxed(
        [SYSTEM_PYTHON, "-c", probe], tmp_path / "work", allow_network=False, timeout=30,
        timeout_error=sandbox_runner.TimeoutExceeded, read_only=grants,
    )
    assert result.stdout.split("\n")[:4] == ["granted file", "blocked", "blocked", "blocked"]
    assert not os.path.exists("/private/tmp/legwork-escape-probe")


@macos_only
def test_the_profile_never_touches_disk(tmp_path):
    """A profile file in the workdir could be rewritten by the sandboxed code
    before the next launch (pre-launch QA, 2026-10-02): it's passed inline."""
    profile = sandbox_runner._generate_profile(tmp_path, allow_network=False, read_only=(tmp_path,))
    argv = sandbox_runner._sandboxed_argv(["true"], tmp_path, False, profile)
    assert argv[:3] == ["sandbox-exec", "-p", profile.text]
    assert str(tmp_path.resolve()) not in profile.text
    assert f"GRANT_0={tmp_path.resolve()}" in argv


def test_sandboxed_code_cannot_read_the_callers_stdin(tmp_path):
    """Inherited stdin was the hub's MCP transport: an npm step ended the
    hub's input, and install code could read the client's messages."""
    import subprocess

    caller = (
        "import sys\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        "from pathlib import Path\n"
        "from legwork import sandbox_runner as s\n"
        f"r = s.invoke([{SYSTEM_PYTHON!r}, '-c', 'import sys; print(repr(sys.stdin.read()))'], Path({str(tmp_path)!r}))\n"
        "print('child saw', r.stdout.strip())\n"
        "print('caller still has', repr(sys.stdin.readline()))\n"
    )
    out = subprocess.run([sys.executable, "-c", caller], input="CLIENT MESSAGE\n", capture_output=True, text=True, timeout=60)
    assert "child saw ''" in out.stdout, out.stderr
    assert "caller still has 'CLIENT MESSAGE\\n'" in out.stdout


def test_processes_left_behind_by_an_install_are_stopped(tmp_path):
    """An install step could `nohup ... &` a process that outlived the
    install, with network, and no timeout (pre-launch QA, 2026-10-02)."""
    import time

    heartbeat = tmp_path / "heartbeat"
    install(["/bin/sh", "-c", "nohup /bin/sh -c 'while true; do echo x >> heartbeat; sleep 0.2; done' >/dev/null 2>&1 &"], tmp_path, timeout=60)
    time.sleep(0.5)
    size = heartbeat.stat().st_size if heartbeat.exists() else 0
    time.sleep(1.5)
    assert (heartbeat.stat().st_size if heartbeat.exists() else 0) == size


def test_your_home_folder_is_refused_in_any_letter_case(monkeypatch, tmp_path):
    """/USERS/me is your home on macOS's case-insensitive filesystem."""
    home = tmp_path / "Users" / "me"
    home.mkdir(parents=True)
    monkeypatch.setattr("legwork.sandbox_runner.Path.home", lambda: home)
    for spelling in (str(home).upper(), str(tmp_path / "USERS"), str(home).replace("me", "ME")):
        with pytest.raises(sandbox_runner.SandboxGrantError, match="whole home folder"):
            sandbox_runner.check_read_grants([spelling])
