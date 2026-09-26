"""A minimal MCP client over stdio (newline-delimited JSON-RPC), just enough
to do what Claude Code does when it launches `legwork serve`: initialize,
list tools, call one. Legwork itself doesn't depend on the MCP SDK, so the
tests talk the wire protocol directly rather than pulling it in."""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time


class StdioMCPClient:
    def __init__(self, command: list[str], env: dict[str, str]):
        self.proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._next_id = 0
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        for line in self.proc.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def _send(self, message: dict) -> None:
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None, timeout: float = 60) -> dict:
        self._next_id += 1
        want = self._next_id
        message = {"jsonrpc": "2.0", "id": want, "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no reply to {method} within {timeout}s")
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError(f"no reply to {method} within {timeout}s") from exc
            if line is None:
                raise EOFError(f"server exited before replying to {method}: {self.proc.stderr.read()[-2000:]}")
            reply = json.loads(line)
            if reply.get("id") == want:
                return reply

    def notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def initialize(self) -> dict:
        reply = self.request(
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "legwork-tests", "version": "0"}},
        )
        self.notify("notifications/initialized")
        return reply

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
