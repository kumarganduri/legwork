"""What a served tool tried to reach on Linux (issue #5): seccomp user
notification, with the listener held by the hub, outside the sandbox. The
real path runs on Linux only (CI's Linux jobs); the parsing runs anywhere."""

from __future__ import annotations

import ipaddress
import json
import os
import platform
import socket
import struct
import subprocess
import sys
import time

import pytest

from legwork import cli, denials, sandbox_runner, seccomp_notify


def _inet(address: str, port: int) -> tuple[int, bytes]:
    ip = ipaddress.ip_address(address)
    if ip.version == 4:
        return 2, struct.pack("=H", 2) + struct.pack("!H", port) + ip.packed + bytes(8)
    return 10, struct.pack("=H", 10) + struct.pack("!H", port) + bytes(4) + ip.packed + bytes(4)


def test_destinations_are_described_and_the_tools_own_services_left_out():
    assert seccomp_notify.describe(*_inet("140.82.121.4", 443)) == "a network connection to 140.82.121.4:443"
    assert seccomp_notify.describe(*_inet("2606:4700::1", 443)) == "a network connection to [2606:4700::1]:443"
    assert seccomp_notify.describe(*_inet("127.0.0.53", 53)) == "a DNS lookup (network is off)"
    assert seccomp_notify.describe(*_inet("127.0.0.1", 8080)) is None  # inside its own sandbox
    assert seccomp_notify.describe(*_inet("::ffff:127.0.0.1", 8080)) is None
    assert seccomp_notify.describe(1, b"\x01\x00/tmp/x") is None  # a Unix socket
    assert seccomp_notify.describe(2, b"\x02\x00") is None  # truncated


def test_only_connect_is_paused():
    for machine, connect_nr in (("x86_64", 42), ("aarch64", 203)):
        program = seccomp_notify.filter_program(machine)
        ops = [struct.unpack("<HBBI", program[i:i + 8]) for i in range(0, len(program), 8)]
        assert ops[3] == (0x15, 0, 1, connect_nr) and ops[4][3] == 0x7FC00000 and ops[5][3] == 0x7FFF0000
    assert seccomp_notify.filter_program("riscv64") is None
    # The kernel's ioctl numbers (linux/seccomp.h)
    assert (seccomp_notify._IOCTL_RECV, seccomp_notify._IOCTL_SEND) == (0xC0502100, 0xC0182101)


def test_only_a_python_command_is_started_through_the_loader():
    wrapped = seccomp_notify.wrap(["/w/.venv/bin/python", "-c", "x", "w.py"], 7, "x86_64")
    assert wrapped[:3] == ["/w/.venv/bin/python", "-I", "-c"] and wrapped[4] == "7"
    assert wrapped[-4:] == ["/w/.venv/bin/python", "-c", "x", "w.py"]
    compile(wrapped[3], "loader", "exec")
    assert seccomp_notify.wrap(["/usr/bin/node", "w.js"], 7, "x86_64") is None
    assert seccomp_notify.wrap(["/w/python"], 7, "riscv64") is None


def test_serve_takes_only_a_socket_from_the_environment(monkeypatch, tmp_path):
    a, b = socket.socketpair()
    with a, b:
        monkeypatch.setenv(seccomp_notify.FD_ENV, str(b.fileno()))
        assert cli._notify_fd() == b.fileno()
        assert seccomp_notify.FD_ENV not in os.environ  # not passed on
    with open(tmp_path / "f", "w") as f:
        monkeypatch.setenv(seccomp_notify.FD_ENV, str(f.fileno()))
        assert cli._notify_fd() is None
    monkeypatch.setenv(seccomp_notify.FD_ENV, "not a number")
    assert cli._notify_fd() is None


# --- the real path, on Linux ---------------------------------------------------

TRIES = r"""
import errno, socket
for family, kind, address in [(socket.AF_INET, socket.SOCK_STREAM, ("203.0.113.9", 443)),
                              (socket.AF_INET, socket.SOCK_DGRAM, ("127.0.0.53", 53)),
                              (socket.AF_INET, socket.SOCK_STREAM, ("127.0.0.1", 8080))]:
    s = socket.socket(family, kind)
    try:
        s.connect(address)
        print(address[1], "connected")
    except OSError as exc:
        print(address[1], errno.errorcode.get(exc.errno, exc.errno))
"""


def _serve_sandboxed(tmp_path, allow_network: bool, script: str = TRIES):
    """`legwork serve`'s exec_serve, running `script` with the hub's socket."""
    python = os.path.realpath(sys._base_executable)  # visible in the sandbox, unlike the repo's .venv
    hub_end, sandbox_end = seccomp_notify.socketpair()
    start = (
        "import json, sys; from pathlib import Path; from legwork import sandbox_runner; "
        "sandbox_runner.exec_serve(json.loads(sys.argv[1]), Path(sys.argv[2]), allow_network=sys.argv[3] == '1', "
        "notify_fd=int(sys.argv[4]))"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", start, json.dumps([python, "-c", script]), str(tmp_path), "1" if allow_network else "0",
         str(sandbox_end.fileno())],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, pass_fds=(sandbox_end.fileno(),),
    )
    sandbox_end.close()
    return proc, hub_end


@pytest.mark.skipif(platform.system() != "Linux", reason="seccomp is Linux's")
def test_a_served_tools_network_attempts_are_recorded_and_still_refused(tmp_path):
    sandbox_runner._check_backend_available()
    proc, hub_end = _serve_sandboxed(tmp_path, allow_network=False)
    with hub_end:
        listener = seccomp_notify.receive_listener(hub_end)
    assert listener is not None, "the loader couldn't make a seccomp listener here"
    watcher = denials.SeccompWatcher()
    watcher.adopt(proc.pid, listener, alive=lambda: proc.poll() is None)
    out, err = proc.communicate(timeout=60)
    assert proc.returncode == 0, err
    # The tool sees what it saw before: no network.
    assert "443 ENETUNREACH" in out and "8080 ECONNREFUSED" in out, out
    time.sleep(0.2)
    found = dict(watcher.collect(proc.pid, 0))
    assert found == {"a network connection to 203.0.113.9:443": 1, "a DNS lookup (network is off)": 1}, found


@pytest.mark.skipif(platform.system() != "Linux", reason="seccomp is Linux's")
def test_with_network_allowed_nothing_is_paused(tmp_path):
    """Only refused attempts are recorded: with --allow-net the socket is
    closed and the tool starts as before."""
    sandbox_runner._check_backend_available()
    # It connects nowhere: with real network, TRIES's unroutable address
    # waits minutes for a TCP timeout.
    proc, hub_end = _serve_sandboxed(tmp_path, allow_network=True, script="print('started')")
    with hub_end:
        assert seccomp_notify.receive_listener(hub_end, timeout=30) is None
    out, err = proc.communicate(timeout=60)
    assert proc.returncode == 0 and out.strip() == "started", err
