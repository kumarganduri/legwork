"""A small MCP client over stdio (newline-delimited JSON-RPC): initialize,
list tools, call one. The hub uses it to talk to the tools it installs;
the tests use it to talk to Legwork. No MCP SDK needed.

Thread-safe per client (the hub may call a tool while listing another), and
it keeps reading the child's stderr: served tools log there constantly, and
an unread pipe fills up and freezes the child.
"""

from __future__ import annotations

import collections
import json
import queue
import subprocess
import threading
import time

PROTOCOL_VERSION = "2025-06-18"


class MCPClientError(Exception):
    """The server exited, didn't answer in time, or answered with an error."""


class StdioMCPClient:
    def __init__(self, command: list[str], env: dict[str, str] | None = None):
        self.proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._stderr: collections.deque[str] = collections.deque(maxlen=200)
        self._next_id = 0
        self._lock = threading.Lock()
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

    def _pump_stdout(self) -> None:
        for line in self.proc.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def _pump_stderr(self) -> None:
        for line in self.proc.stderr:
            self._stderr.append(line)

    def stderr_tail(self, lines: int = 20) -> str:
        return "".join(list(self._stderr)[-lines:])

    def _send(self, message: dict) -> None:
        try:
            self.proc.stdin.write(json.dumps(message) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise MCPClientError(f"server exited: {self.stderr_tail()}") from exc

    def request(self, method: str, params: dict | None = None, timeout: float = 60) -> dict:
        with self._lock:
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
                    raise MCPClientError(f"no reply to {method} within {timeout:.0f}s")
                try:
                    line = self._lines.get(timeout=remaining)
                except queue.Empty as exc:
                    raise MCPClientError(f"no reply to {method} within {timeout:.0f}s") from exc
                if line is None:
                    raise MCPClientError(f"server exited before replying to {method}: {self.stderr_tail()}")
                try:
                    reply = json.loads(line)
                except ValueError:
                    continue  # not ours to parse; a well-behaved server never does this
                if reply.get("id") == want:
                    return reply

    def notify(self, method: str, params: dict | None = None) -> None:
        with self._lock:
            message = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                message["params"] = params
            self._send(message)

    def initialize(self, client_name: str = "legwork", timeout: float = 60) -> dict:
        reply = self.request(
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": client_name, "version": "0"}},
            timeout=timeout,
        )
        if "error" in reply:
            raise MCPClientError(f"initialize failed: {reply['error']}")
        self.notify("notifications/initialized")
        return reply

    def list_tools(self, timeout: float = 60) -> list[dict]:
        reply = self.request("tools/list", timeout=timeout)
        if "error" in reply:
            raise MCPClientError(f"tools/list failed: {reply['error']}")
        tools = (reply.get("result") or {}).get("tools") if isinstance(reply.get("result"), dict) else None
        if not isinstance(tools, list):
            raise MCPClientError("tools/list returned no list of tools")
        # A served tool is untrusted code: a nameless or malformed entry used to
        # crash the hub's own tools/list (pre-launch review, 2026-10-03).
        return [
            {**t, "inputSchema": t["inputSchema"] if isinstance(t.get("inputSchema"), dict) else {"type": "object"}}
            for t in tools
            if isinstance(t, dict) and isinstance(t.get("name"), str) and t["name"]
        ]

    def call_tool(self, name: str, arguments: dict, timeout: float = 300) -> dict:
        reply = self.request("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
        if "error" in reply:
            raise MCPClientError(f"{name} failed: {reply['error'].get('message', reply['error'])}")
        return reply["result"]

    def alive(self) -> bool:
        return self.proc.poll() is None

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
