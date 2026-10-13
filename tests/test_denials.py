"""What the sandbox blocked while a tool ran (issue #5)."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import pytest

from legwork import denials, sandbox_runner

HOME = str(Path.home())


@pytest.mark.parametrize(
    ("op", "target", "said"),
    [
        ("network-outbound", "remote:*:443", "a network connection (port 443)"),
        ("network-outbound", "/private/var/run/mDNSResponder", "a DNS lookup (network is off)"),
        ("file-read-data", f"{HOME}/.ssh/id_ed25519", "reading ~/.ssh/id_ed25519"),
        ("file-write-create", "/private/tmp/x", "writing /private/tmp/x"),
        ("signal", "target:1234", "signalling a process outside its sandbox"),
    ],
)
def test_denials_that_matter_are_described(op, target, said):
    assert denials.describe(op, target) == said


@pytest.mark.parametrize(
    ("op", "target"),
    [
        ("file-write-data", "/dev/dtracehelper"),
        ("ipc-posix-shm-read-data", "apple.shm.notification_center"),
        ("file-read-data", f"{HOME}/.CFUserTextEncoding"),
        ("user-preference-read", "kcfpreferencesanyapplication"),
        ("file-read-data", "/usr/lib/libz.dylib"),
    ],
)
def test_the_noise_every_sandboxed_program_makes_is_dropped(op, target):
    assert denials.describe(op, target) is None


def test_the_kernel_line_format_is_parsed():
    m = denials.DENIAL_RE.match("3 duplicate reports for Sandbox: python3.12(4242) deny(1) network-outbound remote:*:443")
    assert m and m["pid"] == "4242" and m["op"] == "network-outbound" and m["repeat"] == "3"


@pytest.mark.skipif(platform.system() != "Darwin", reason="the live sandbox log is macOS")
def test_a_tools_blocked_network_attempt_is_attributed_to_it(tmp_path, monkeypatch):
    """A sandboxed child of a watched process tries the network: the watcher
    sees it, and only for that process tree."""
    monkeypatch.delenv(denials.RECORD_ENV, raising=False)
    sandbox_runner._check_backend_available()
    watcher = denials.Watcher()
    watcher.start()
    script = (
        "import time; from pathlib import Path; from legwork import sandbox_runner\n"
        "time.sleep(1)\n"
        "try:\n"
        f"    sandbox_runner.invoke(['/usr/bin/curl', '-sS', '--max-time', '3', 'https://93.184.215.14/'], Path({str(tmp_path)!r}), timeout=20)\n"
        "except Exception:\n"
        "    pass\n"
    )
    child = subprocess.Popen([sys.executable, "-c", script], env=dict(os.environ))
    watcher.watch(child.pid)
    since = time.monotonic()
    child.wait(timeout=60)
    time.sleep(1)
    found = dict(watcher.collect(child.pid, since))
    watcher.close()
    assert "a network connection (port 443)" in found
    assert watcher.collect(999999, since) == []
