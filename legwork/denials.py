"""What the sandbox blocked while a served tool ran (issue #5), on macOS.

The kernel logs each sandbox denial live, e.g.

    Sandbox: curl(80179) deny(1) network-outbound remote:*:443
    Sandbox: cat(80180) deny(1) file-read-data /Users/you/.zshrc

It doesn't keep them for `log show` later, and it hides network destinations
(only the port is shown). So the hub runs one `log stream` while it serves
tools, keeps only denials from each tool's own processes (found through the
parent-process chain), and drops the noise every sandboxed program makes
(a debugging device, shared-memory and preference lookups). What's left, a
network attempt, a read or write outside what the tool may touch, a signal
to another process, goes into the reply and the tool's record.

It's best effort: a child process that exits before Legwork looks up its
parent can't be attributed, and Linux has no equivalent log yet.
"""

from __future__ import annotations

import json
import os
import platform
import pty
import re
import subprocess
import threading
import time
from collections import Counter, deque
from pathlib import Path

DENIAL_RE = re.compile(r"^(?:(?P<repeat>\d+) duplicate reports? for )?Sandbox: (?P<name>.+?)\((?P<pid>\d+)\) deny\(\d+\) (?P<op>\S+)(?: (?P<target>.*))?$")
_PREDICATE = 'process == "kernel" AND (eventMessage BEGINSWITH "Sandbox: " OR eventMessage CONTAINS "duplicate report")'
_NOISE_WRITES = ("/dev/dtracehelper", "/dev/tty", "/dev/null")
_NOISE_READ_PARTS = ("/.CFUserTextEncoding", "/Library/Preferences/")
RECORD_ENV = "LEGWORK_RECORD_DENIALS"  # "off" to not watch


def describe(op: str, target: str) -> str | None:
    """A denial in words, or None for the noise every sandboxed program makes."""
    target = (target or "").strip()
    home = str(Path.home())
    if op == "network-outbound":
        if "mDNSResponder" in target:
            return "a DNS lookup (network is off)"
        port = target.rsplit(":", 1)[-1] if target.startswith("remote:") else ""
        return f"a network connection{f' (port {port})' if port.isdigit() else ''}"
    if op.startswith("file-read"):
        if not target.startswith(home) or any(p in target for p in _NOISE_READ_PARTS):
            return None
        return f"reading {target.replace(home, '~', 1)}"
    if op.startswith("file-write"):
        if target.startswith(_NOISE_WRITES):
            return None
        return f"writing {target.replace(home, '~', 1)}"
    if op.startswith("signal"):
        return "signalling a process outside its sandbox"
    return None


class Watcher:
    """One `log stream` for a hub; tools register their process ids."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._roots: set[int] = set()
        self._parent: dict[int, int] = {}
        self._snapshot_at = 0.0
        self._events: deque[tuple[float, int, str]] = deque(maxlen=20000)
        self._foreign: set[int] = set()
        self._foreign_since = time.monotonic()
        self._proc: subprocess.Popen | None = None
        self._stream = None
        self._ready = threading.Event()

    @staticmethod
    def available() -> bool:
        return (platform.system() == "Darwin" and Path("/usr/bin/log").exists()
                and os.environ.get(RECORD_ENV, "").strip().lower() != "off")

    def start(self) -> None:
        with self._lock:
            if self._proc is not None or not self.available():
                return
            # Through a pseudo-terminal: into a pipe, `log stream` buffers its
            # output and lines arrived only once kilobytes had piled up, so a
            # call's denials were missed (2026-10-13). On a terminal it writes
            # each line as it happens.
            master, slave = pty.openpty()
            self._proc = subprocess.Popen(
                ["/usr/bin/log", "stream", "--style", "ndjson", "--predicate", _PREDICATE],
                stdin=subprocess.DEVNULL, stdout=slave, stderr=subprocess.DEVNULL, start_new_session=True,
            )
            os.close(slave)
            self._stream = os.fdopen(master, "r", errors="replace")
        threading.Thread(target=self._read, daemon=True).start()
        # The stream takes a moment to attach, and a denial before then is
        # lost: a tool's very first call wasn't reported (2026-10-13). It
        # prints a "Filtering the log data..." line once it's listening.
        self._ready.wait(timeout=5)

    def watch(self, pid: int) -> None:
        with self._lock:
            self._roots.add(pid)

    def collect(self, root: int, since: float, until: float | None = None) -> list[tuple[str, int]]:
        """What was blocked for `root`'s processes between `since` and `until`
        (monotonic, by when the line arrived): [(description, count)], most
        frequent first."""
        with self._lock:
            found = Counter()
            for at, owner, text in self._events:
                if owner == root and at >= since and (until is None or at <= until):
                    found[text] += 1
        return found.most_common()

    def wait_for(self, root: int, since: float, timeout: float) -> list[tuple[str, int]]:
        """collect(), waiting up to `timeout` for the first line: the kernel's
        lines arrive from at once to about 1.5 s after the denial."""
        deadline = time.monotonic() + timeout
        while True:
            found = self.collect(root, since)
            if found or time.monotonic() >= deadline:
                return found
            time.sleep(0.05)

    def close(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
            stream, self._stream = self._stream, None
        if proc is not None:
            proc.terminate()
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass

    def _read(self) -> None:
        stream = self._stream
        if stream is None:
            return
        try:
            lines = iter(stream)
            for line in lines:
                self._handle(line)
        except OSError:  # the stream closed
            return

    def _handle(self, line: str) -> None:
        self._ready.set()
        try:
            message = json.loads(line).get("eventMessage", "")
        except (ValueError, AttributeError):
            return
        match = DENIAL_RE.match(message or "")
        if not match:
            return
        text = describe(match["op"], match["target"] or "")
        if text is None:
            return
        root = self._root_of(int(match["pid"]))
        if root is None:
            return
        with self._lock:
            for _ in range(int(match["repeat"] or 1)):
                self._events.append((time.monotonic(), root, text))

    def _root_of(self, pid: int) -> int | None:
        """The watched tool process that `pid` descends from, if any. Other
        apps' sandboxes log here too: processes found not to belong to a tool
        are remembered for a minute, so they don't cost a lookup each time."""
        with self._lock:
            if time.monotonic() - self._foreign_since > 60:
                self._foreign.clear()
                self._foreign_since = time.monotonic()
            if pid in self._foreign:
                return None
            root = self._walk(pid)
        if root is None and self._refresh():
            with self._lock:
                root = self._walk(pid)
                if root is None and pid in self._parent:
                    self._foreign.add(pid)
        return root

    def _walk(self, pid: int) -> int | None:
        seen, current = set(), pid
        while current and current not in seen:
            if current in self._roots:
                return current
            seen.add(current)
            current = self._parent.get(current, 0)
        return None

    def _refresh(self) -> bool:
        """The parent of every process now, at most a few times a second."""
        now = time.monotonic()
        with self._lock:
            if now - self._snapshot_at < 0.1:
                return False
            self._snapshot_at = now
        try:
            out = subprocess.run(["/bin/ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, timeout=5,
                                 stdin=subprocess.DEVNULL).stdout
        except (OSError, subprocess.TimeoutExpired):
            return False
        parents = {}
        for line in out.splitlines():
            fields = line.split()
            if len(fields) == 2 and fields[0].isdigit() and fields[1].isdigit():
                parents[int(fields[0])] = int(fields[1])
        with self._lock:
            self._parent.update(parents)
        return True
