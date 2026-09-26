"""Backend selection and the Linux bubblewrap command line. Pure: these run
on any OS. The behaviour of each backend is tested for real in
tests/test_sandbox_runner.py on the machine that has it."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from legwork import sandbox_runner
from legwork.sandbox_runner import SandboxUnavailableError, _bwrap_args


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
    assert args[:2] == ["bwrap", "--unshare-all"]
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
        pytest.raises(SandboxUnavailableError, match="apparmor_restrict_unprivileged_userns"),
    ):
        sandbox_runner._check_backend_available()


def test_available_is_false_rather_than_raising():
    with patch("legwork.sandbox_runner.platform.system", return_value="Windows"):
        assert sandbox_runner.available() is False
