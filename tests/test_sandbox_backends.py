"""Backend selection and the Linux bubblewrap command line. Pure: these run
on any OS. The behaviour of each backend is tested for real in
tests/test_sandbox_runner.py on the machine that has it."""

from __future__ import annotations

import struct as _struct
from pathlib import Path
from unittest.mock import patch

import pytest

from legwork import sandbox_runner
from legwork.sandbox_runner import (
    SandboxUnavailableError,
    _bwrap_args,
    no_unix_sockets_filter,
)


def _args(workdir="/home/me/.legwork/wrappers/x/builds/1/attempt-1", network=False, home="/home/me"):
    with (
        patch("legwork.sandbox_runner.Path.home", return_value=Path(home)),
        patch("legwork.sandbox_runner._hidden_dirs", return_value=[Path("/home"), Path(home)]),
    ):
        return _bwrap_args(Path(workdir), allow_network=network)


def _pairs(args, flag):
    return [args[i + 1] for i, a in enumerate(args) if a == flag]


def test_system_is_read_only_and_only_the_workdir_is_writable():
    args = _args()
    # Absolute: looked up by bare name, the launcher came from the child's
    # PATH, which starts with folders the tool can write (pre-launch review).
    assert args[0].startswith("/") and args[0].endswith("/bwrap") and args[1] == "--unshare-all"
    assert ["--ro-bind", "/", "/"] == args[args.index("--ro-bind") : args.index("--ro-bind") + 3]
    workdir = str(Path("/home/me/.legwork/wrappers/x/builds/1/attempt-1").resolve())
    assert _pairs(args, "--bind") == [workdir]
    assert args[-2:] == ["--chdir", workdir]


def test_homes_and_socket_folders_are_replaced_before_the_workdir_is_mounted():
    args = _args()
    hidden = _pairs(args, "--tmpfs")
    for path in ("/tmp", "/run", "/home", "/home/me"):
        assert path in hidden
    # The workdir (inside /home/me) is bound after /home/me is hidden, so
    # it's the only thing visible there.
    assert max(i for i, a in enumerate(args) if a == "--tmpfs") < args.index("--bind")


def test_no_network_unless_installing():
    assert "--share-net" not in _args(network=False)
    install = _args(network=True)
    assert "--share-net" in install
    # DNS comes back; nothing else from /run does.
    assert "/run/systemd/resolve" in _pairs(install, "--ro-bind-try")


def test_linux_without_bubblewrap_refuses_with_install_instructions():
    with (
        patch("legwork.sandbox_runner.platform.system", return_value="Linux"),
        patch("legwork.sandbox_runner.shutil.which", return_value=None),
        pytest.raises(SandboxUnavailableError, match="apt install bubblewrap"),
    ):
        sandbox_runner._check_backend_available()


def test_linux_with_user_namespaces_disabled_refuses_and_says_why():
    sandbox_runner._bwrap_probe_error.cache_clear()
    with (
        patch("legwork.sandbox_runner.platform.system", return_value="Linux"),
        patch("legwork.sandbox_runner.shutil.which", return_value="/usr/bin/bwrap"),
        patch("legwork.sandbox_runner._bwrap_probe_error", return_value="setting up uid map: Permission denied"),
        patch("legwork.sandbox_runner._apparmor_restricts_userns", return_value=False),
        pytest.raises(SandboxUnavailableError, match="user namespaces are probably disabled"),
    ):
        sandbox_runner._check_backend_available()


def test_available_is_false_rather_than_raising():
    with patch("legwork.sandbox_runner.platform.system", return_value="Windows"):
        assert sandbox_runner.available() is False


def test_ubuntu_apparmor_restriction_gets_the_exact_fix():
    with (
        patch("legwork.sandbox_runner.platform.system", return_value="Linux"),
        patch("legwork.sandbox_runner.shutil.which", return_value="/usr/bin/bwrap"),
        patch("legwork.sandbox_runner._bwrap_probe_error", return_value="loopback: Failed RTM_NEWADDR"),
        patch("legwork.sandbox_runner._apparmor_restricts_userns", return_value=True),
        pytest.raises(SandboxUnavailableError) as exc,
    ):
        sandbox_runner._check_backend_available()
    assert "sudo apparmor_parser -r /etc/apparmor.d/bwrap" in str(exc.value)
    assert "profile bwrap /usr/bin/bwrap flags=(unconfined)" in str(exc.value)


def test_stand_in_homes_are_read_only_after_the_workdir_is_mounted():
    args = _args()
    assert set(_pairs(args, "--remount-ro")) == {"/home", "/home/me"}
    assert args.index("--bind") < args.index("--remount-ro")


def test_python_under_home_is_mounted_read_only_after_home_is_hidden():
    with patch("legwork.sandbox_runner.interpreter_home", return_value=Path("/home/me/.local/share/uv/python/cpython-3.12")):
        args = _args()
    assert "/home/me/.local/share/uv/python/cpython-3.12" in _pairs(args, "--ro-bind")
    ro = args.index("--ro-bind", args.index("--bind"))
    assert max(i for i, a in enumerate(args) if a == "--tmpfs") < ro


def test_interpreter_home_ignores_pythons_outside_home(monkeypatch):
    monkeypatch.setattr("sys.base_prefix", "/opt/homebrew/Cellar/python@3.12/3.12.7")
    assert sandbox_runner.interpreter_home() is None


def test_a_download_cache_is_bound_writable_on_linux():
    with (
        patch("legwork.sandbox_runner.Path.home", return_value=Path("/home/me")),
        patch("legwork.sandbox_runner._hidden_dirs", return_value=[Path("/home"), Path("/home/me")]),
    ):
        args = _bwrap_args(Path("/home/me/b/attempt-1"), allow_network=True, extra_writable=(Path("/home/me/b/.download-cache"),))
    assert str(Path("/home/me/b/.download-cache").resolve()) in _pairs(args, "--bind")


# --- the install-phase seccomp filter, run through a tiny BPF interpreter ----

ALLOW, ERRNO = 0x7FFF0000, 0x00050000


def _run_bpf(program: bytes, arch: int, nr: int, arg0: int = 0) -> int:
    data = _struct.pack("<iIQ6Q", nr, arch, 0, arg0, 0, 0, 0, 0, 0)
    insns = [_struct.unpack("<HBBI", program[i : i + 8]) for i in range(0, len(program), 8)]
    pc, acc = 0, 0
    while True:
        code, jt, jf, k = insns[pc]
        if code == 0x20:
            acc = _struct.unpack_from("<I", data, k)[0]
        elif code == 0x06:
            return k
        elif code in (0x15, 0x35):
            taken = acc == k if code == 0x15 else acc >= k
            pc += jt if taken else jf
        pc += 1


@pytest.mark.parametrize(("machine", "arch", "socket_nr"), [("x86_64", 0xC000003E, 41), ("aarch64", 0xC00000B7, 198)])
def test_filter_refuses_only_unix_sockets(machine, arch, socket_nr):
    prog = no_unix_sockets_filter(machine)
    assert _run_bpf(prog, arch, socket_nr, arg0=1) == ERRNO | 97  # AF_UNIX: refused
    assert _run_bpf(prog, arch, socket_nr, arg0=2) == ALLOW  # AF_INET
    assert _run_bpf(prog, arch, socket_nr, arg0=10) == ALLOW  # AF_INET6
    assert _run_bpf(prog, arch, socket_nr + 1, arg0=1) == ALLOW  # any other syscall
    assert _run_bpf(prog, 0x40000003, 102) == ERRNO | 38  # 32-bit ABI: refused


def test_filter_refuses_x32_syscalls():
    assert _run_bpf(no_unix_sockets_filter("x86_64"), 0xC000003E, 0x40000000 | 41, arg0=1) == ERRNO | 97


def test_no_filter_on_unknown_architectures():
    assert no_unix_sockets_filter("riscv64") is None


def test_bwrap_gets_the_filter_only_when_one_is_given(monkeypatch):
    monkeypatch.setattr("legwork.sandbox_runner.platform.system", lambda: "Linux")
    monkeypatch.setattr("legwork.sandbox_runner._hidden_dirs", list)
    args = sandbox_runner._sandboxed_argv(["true"], Path("/w"), True, None, (), seccomp_fd=7)
    assert args[args.index("--seccomp") + 1] == "7" and args[-2:] == ["--", "true"]
    assert "--seccomp" not in sandbox_runner._sandboxed_argv(["true"], Path("/w"), True, None)


def test_linux_grants_are_read_only_binds_and_network_is_opt_in():
    with (
        patch("legwork.sandbox_runner.Path.home", return_value=Path("/home/me")),
        patch("legwork.sandbox_runner._hidden_dirs", return_value=[Path("/home"), Path("/home/me")]),
    ):
        args = _bwrap_args(Path("/home/me/b/attempt-1"), allow_network=False, read_only=(Path("/home/me/Downloads"),))
    assert str(Path("/home/me/Downloads").resolve()) in _pairs(args, "--ro-bind")
    assert "--share-net" not in args
    assert args.index("--ro-bind", args.index("--bind")) < args.index("--remount-ro")


def test_macos_grants_are_read_only_in_the_profile(tmp_path):
    profile = sandbox_runner._generate_profile(tmp_path, allow_network=False, read_only=(tmp_path / "Downloads",))
    assert profile.params["GRANT_0"] == str((tmp_path / "Downloads").resolve())
    assert '(allow file-read* (subpath (param "GRANT_0")))' in profile.text
    assert "file-write*" not in "".join(line for line in profile.text.splitlines() if "GRANT_0" in line)


def test_served_tools_outlive_the_thread_that_started_them(monkeypatch):
    """--die-with-parent kills the sandbox when the *thread* that started it
    exits; the hub starts tools from install threads. Serving must not use it;
    one-shot installs and self-tests still do."""
    monkeypatch.setattr("legwork.sandbox_runner.platform.system", lambda: "Linux")
    monkeypatch.setattr("legwork.sandbox_runner._hidden_dirs", list)
    served = sandbox_runner._sandboxed_argv(["x"], Path("/w"), False, None, die_with_parent=False)
    assert "--die-with-parent" not in served
    assert "--die-with-parent" in sandbox_runner._sandboxed_argv(["x"], Path("/w"), False, None)
