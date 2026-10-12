"""Which hosts an install contacted, recorded by Legwork outside the sandbox.

Installs run with network on, because package managers need it. During an
install Legwork runs a small HTTP proxy on 127.0.0.1 and points the install
at it (HTTP_PROXY / HTTPS_PROXY, which pip, uv, npm, git, curl and
Python's and Node's download libraries honour). The proxy notes each host
and port it's asked to reach (never the contents) and relays the bytes.

The proxy runs outside the sandbox, so it must not become a way around it:
it only connects to public internet addresses. Loopback, private, link-local
and other special addresses are refused, which keeps services on this
machine and its network out of reach as the sandbox already does. It
connects to the address it checked, so a DNS answer can't change between
the check and the connection.

Code that opens its own connections can skip the proxy unless the install
is limited to it (strict mode, macOS only for now); `complete` in the record
says which it was. Raised on r/mcp, issue #5.
"""

from __future__ import annotations

import ipaddress
import json
import os
import select
import socket
import socketserver
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

MAX_HEADER_BYTES = 64 * 1024
CONNECT_TIMEOUT_SECONDS = 15
IDLE_TIMEOUT_SECONDS = 300


@dataclass
class HostStats:
    host: str
    port: int
    connections: int = 0
    bytes_out: int = 0
    bytes_in: int = 0
    refused: str = ""  # why the proxy refused, if it did


@dataclass
class _State:
    lock: threading.Lock = field(default_factory=threading.Lock)
    hosts: dict[tuple[str, int], HostStats] = field(default_factory=dict)

    def stats(self, host: str, port: int) -> HostStats:
        with self.lock:
            return self.hosts.setdefault((host.lower(), port), HostStats(host.lower(), port))


def public_address(host: str, port: int) -> str:
    """The first address `host` resolves to, if every address it resolves to
    is a public one. Raises ValueError otherwise."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"can't resolve {host}") from exc
    addresses = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise ValueError(f"{host} resolves to {ip}, which isn't a public internet address")
        addresses.append(info[4][0])
    if not addresses:
        raise ValueError(f"can't resolve {host}")
    return addresses[0]


class _Handler(socketserver.BaseRequestHandler):
    server: _Server

    def handle(self) -> None:
        client = self.request
        client.settimeout(CONNECT_TIMEOUT_SECONDS)
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = client.recv(4096)
            if not chunk:
                return
            head += chunk
            if len(head) > MAX_HEADER_BYTES:
                return self._reply(client, 431, "Request Header Fields Too Large")
        header, _, rest = head.partition(b"\r\n\r\n")
        lines = header.decode("latin-1").split("\r\n")
        try:
            method, target, version = lines[0].split(" ", 2)
        except ValueError:
            return self._reply(client, 400, "Bad Request")
        if method.upper() == "CONNECT":
            host, _, port_text = target.rpartition(":")
            host = host.strip("[]")
            upstream = self._open(client, host, port_text)
            if upstream is None:
                return
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            first = rest
        else:
            url = urllib.parse.urlsplit(target)
            if url.scheme != "http" or not url.hostname:
                return self._reply(client, 400, "Bad Request")
            host = url.hostname
            upstream = self._open(client, host, str(url.port or 80))
            if upstream is None:
                return
            path = urllib.parse.urlunsplit(("", "", url.path or "/", url.query, ""))
            kept = [h for h in lines[1:] if not h.lower().startswith(("proxy-", "connection:"))]
            first = "\r\n".join([f"{method} {path} {version}", *kept, "Connection: close", "", ""]).encode("latin-1") + rest
        stats = self.server.state.stats(host, upstream.getpeername()[1])
        with self.server.state.lock:
            stats.connections += 1
        self._relay(client, upstream, first, stats)

    def _open(self, client: socket.socket, host: str, port_text: str) -> socket.socket | None:
        try:
            port = int(port_text)
            if not 0 < port < 65536:
                raise ValueError("bad port")
            address = public_address(host, port)
            return socket.create_connection((address, port), timeout=CONNECT_TIMEOUT_SECONDS)
        except (ValueError, OSError) as exc:
            if port_text.isdigit():
                stats = self.server.state.stats(host, int(port_text))
                with self.server.state.lock:
                    stats.refused = str(exc)[:200]
            self._reply(client, 403 if isinstance(exc, ValueError) else 502, "Refused by Legwork")
            return None

    def _relay(self, client: socket.socket, upstream: socket.socket, first: bytes, stats: HostStats) -> None:
        sockets = [client, upstream]
        for s in sockets:
            s.setblocking(False)
        try:
            if first:
                upstream.setblocking(True)
                upstream.sendall(first)
                upstream.setblocking(False)
                with self.server.state.lock:
                    stats.bytes_out += len(first)
            while True:
                ready, _, _ = select.select(sockets, [], [], IDLE_TIMEOUT_SECONDS)
                if not ready:
                    return
                for s in ready:
                    data = s.recv(65536)
                    if not data:
                        return
                    other = upstream if s is client else client
                    other.setblocking(True)
                    other.sendall(data)
                    other.setblocking(False)
                    with self.server.state.lock:
                        if s is client:
                            stats.bytes_out += len(data)
                        else:
                            stats.bytes_in += len(data)
        except OSError:
            return
        finally:
            upstream.close()

    @staticmethod
    def _reply(client: socket.socket, code: int, text: str) -> None:
        try:
            client.sendall(f"HTTP/1.1 {code} {text}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())
        except OSError:
            pass


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True
    state: _State


class Recorder:
    """A recording proxy for the length of one install:

        with Recorder() as recorder:
            install(..., proxy_port=recorder.port)
        recorder.hosts()
    """

    def __init__(self) -> None:
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.state = _State()
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> Recorder:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def environment(self) -> dict[str, str]:
        """What points an install at the proxy."""
        return {
            "HTTP_PROXY": self.url, "HTTPS_PROXY": self.url, "http_proxy": self.url, "https_proxy": self.url,
            "npm_config_proxy": self.url, "npm_config_https_proxy": self.url,
            "NODE_USE_ENV_PROXY": "1",  # Node's own fetch ignores *_PROXY without it
            # NLTK refuses proxied downloads unless the proxy is SSRF-safe, which
            # this one is: public addresses only, connecting to the address it
            # checked. Without it textstat's install failed (2026-10-12).
            "NLTK_ALLOW_PROXIED_URLOPEN": "1",
            "NO_PROXY": "", "no_proxy": "",
        }

    def hosts(self) -> list[dict]:
        with self._server.state.lock:
            stats = sorted(self._server.state.hosts.values(), key=lambda s: (s.host, s.port))
            return [vars(s).copy() for s in stats]


def record(path: Path, entry: dict) -> None:
    """Append one JSON line to a tool's record. Written by Legwork, outside
    the sandbox, in a folder sandboxed code can't reach, and only ever
    appended to; links aren't followed (issue #5)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, (json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **entry}) + "\n").encode())
    finally:
        os.close(fd)


def read_records(path: Path) -> list[dict]:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
