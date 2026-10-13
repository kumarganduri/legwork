"""Legwork's recording proxy for installs (issue #5): it records the hosts an
install reached and must never become a way around the sandbox."""

from __future__ import annotations

import json
import socket
import subprocess

import pytest

from legwork import netlog, sandbox_runner


def _connect_through(recorder: netlog.Recorder, target: str) -> bytes:
    with socket.create_connection(("127.0.0.1", recorder.port), timeout=10) as s:
        s.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
        return s.recv(200)


@pytest.mark.parametrize("target", ["127.0.0.1:22", "localhost:5432", "[::1]:80", "10.0.0.1:443", "192.168.1.1:80", "169.254.169.254:80"])
def test_the_proxy_never_reaches_this_machine_or_its_network(target):
    """It runs outside the sandbox: connecting anywhere would undo the
    sandbox's localhost block (cloud metadata at 169.254.169.254 included)."""
    with netlog.Recorder() as recorder:
        assert _connect_through(recorder, target).startswith(b"HTTP/1.1 403")
        assert any(h["refused"] for h in recorder.hosts())


def test_only_public_addresses_pass():
    with pytest.raises(ValueError, match="public"):
        netlog.public_address("127.0.0.1", 80)
    with pytest.raises(ValueError):
        netlog.public_address("no-such-host.invalid", 80)


def test_the_proxy_records_hosts_it_relayed():
    with netlog.Recorder() as recorder:
        env = {"PATH": "/usr/bin:/bin", **recorder.environment()}
        out = subprocess.run(["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "https://pypi.org/simple/pdfplumber/"],
                             env=env, capture_output=True, text=True, timeout=60)
        assert out.stdout == "200"
        [pypi] = [h for h in recorder.hosts() if h["host"] == "pypi.org"]
    assert pypi["port"] == 443 and pypi["connections"] == 1 and pypi["bytes_in"] > 0 and not pypi["refused"]


def test_records_are_appended_and_never_follow_links(tmp_path):
    path = tmp_path / "records" / "owner__repo.jsonl"
    netlog.record(path, {"event": "install", "hosts": [{"host": "pypi.org", "port": 443}]})
    netlog.record(path, {"event": "install", "hosts": []})
    entries = netlog.read_records(path)
    assert [e["event"] for e in entries] == ["install", "install"] and entries[0]["hosts"][0]["host"] == "pypi.org"
    assert oct(path.stat().st_mode & 0o777) == "0o600"

    target = tmp_path / "a-file-of-yours"
    target.write_text("keep")
    link = tmp_path / "records" / "planted.jsonl"
    link.symlink_to(target)
    with pytest.raises(OSError):
        netlog.record(link, {"event": "x"})
    assert target.read_text() == "keep"


def test_an_install_is_pointed_at_the_proxy(tmp_path):
    """Every install runs behind it; the sandbox may reach its port, nothing else local."""
    sandbox_runner._check_backend_available()
    workdir = tmp_path / "w"
    result = sandbox_runner.install(["/bin/sh", "-c", "env | grep -i '^https_proxy=' > proxy.txt"], workdir)
    assert "http://127.0.0.1:" in (workdir / "proxy.txt").read_text()
    assert result.contacted == ()


@pytest.mark.skipif(not sandbox_runner.platform.system() == "Darwin", reason="strict mode is macOS only for now")
def test_strict_mode_allows_only_the_proxy(tmp_path, monkeypatch):
    sandbox_runner._check_backend_available()
    monkeypatch.setenv(sandbox_runner.INSTALL_NETWORK_ENV, "proxy")
    script = (
        "curl -sS -o /dev/null https://pypi.org/simple/pdfplumber/ && echo via-proxy-ok > a.txt; "
        "curl -sS --noproxy '*' -o /dev/null --max-time 10 https://pypi.org/simple/pdfplumber/ && echo direct-ok > b.txt; true"
    )
    result = sandbox_runner.install(["/bin/sh", "-c", script], tmp_path / "w")
    assert (tmp_path / "w" / "a.txt").exists() and not (tmp_path / "w" / "b.txt").exists()
    assert [h["host"] for h in result.contacted] == ["pypi.org"]
    assert json.dumps(result.contacted)  # serialisable for the record
