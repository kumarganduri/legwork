"""What the sandbox blocked while a tool ran (issue #5)."""

from __future__ import annotations

import json
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
    watcher.done(child.pid)
    time.sleep(1)
    found = dict(watcher.collect(child.pid, since))
    watcher.close()
    assert "a network connection (port 443)" in found
    assert watcher.collect(999999, since) == []


def _denied(pid: int) -> str:
    return json.dumps({"eventMessage": f"Sandbox: curl({pid}) deny(1) network-outbound remote:*:443"})


@pytest.mark.skipif(platform.system() != "Darwin", reason="noting a tool's children uses macOS's libproc")
def test_a_denial_from_a_child_that_already_exited_is_still_attributed():
    """The line for a blocked curl arrives about 10 ms before curl is gone on
    a fast Mac and after it on a slow one, when no process list has it any
    more and the denial was dropped (a CI failure, 2026-11-14). Children
    noted while the call was in flight still say whose it was."""
    watcher = denials.Watcher()  # no log stream: the line is fed in below
    tool = subprocess.Popen(
        [sys.executable, "-c", "import subprocess, time\nc = subprocess.Popen(['/bin/sleep', '0.3'])\n"
                               "print(c.pid, flush=True)\nc.wait()\ntime.sleep(30)\n"],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        since = time.monotonic()
        watcher.watch(tool.pid)
        child = int(tool.stdout.readline())
        deadline = time.monotonic() + 10
        while subprocess.run(["/bin/ps", "-p", str(child)], capture_output=True).returncode == 0:
            assert time.monotonic() < deadline, "the child never exited"
            time.sleep(0.05)
        watcher.done(tool.pid)
        watcher._handle(_denied(child))
        assert watcher.collect(tool.pid, since) == [("a network connection (port 443)", 1)]
    finally:
        watcher.close()
        tool.kill()


def test_a_child_noted_long_ago_is_not_believed():
    """Process ids are reused: a note older than the keep time must not pin
    another program's denial on a tool."""
    watcher = denials.Watcher()
    gone = 999_999  # no such process
    with watcher._lock:
        watcher._roots.add(4242)
        watcher._seen[gone] = (4242, time.monotonic() - denials.SAMPLE_KEEP_SECONDS - 1)
    watcher._handle(_denied(gone))
    assert watcher.collect(4242, 0.0) == []
    with watcher._lock:
        watcher._seen[gone] = (4242, time.monotonic())
    watcher._handle(_denied(gone))
    assert watcher.collect(4242, 0.0) == [("a network connection (port 443)", 1)]
