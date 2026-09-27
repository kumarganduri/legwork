"""The hub for real: install reverify from the public cache (no model key),
call it through the hub, and check a served tool reads exactly what was
granted. Needs the sandbox and the network (pip)."""

from __future__ import annotations

import os
import shutil
import socket
import time
from pathlib import Path

import pytest

from legwork import hub, sandbox_runner

CACHE = Path(__file__).parent.parent / "cache"
CLAIM = {"kind": "emulate_result", "code": "b805000000b90300000001c8c3", "arch": "x86", "expect_registers": {"eax": 8}}


def _network() -> bool:
    try:
        socket.create_connection(("pypi.org", 443), timeout=5).close()
        return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.skipif(not sandbox_runner.available(), reason="no sandbox backend on this machine"),
    pytest.mark.skipif(not _network(), reason="needs PyPI"),
]


def test_install_from_the_cache_and_read_only_what_was_granted(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGWORK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("LEGWORK_CACHE_URL", str(CACHE))
    base = Path.home() / f".legwork-hub-test-{os.getpid()}"
    granted, other = base / "granted", base / "other"
    granted.mkdir(parents=True)
    other.mkdir()
    for folder in (granted, other):
        (folder / "blob.bin").write_bytes(bytes.fromhex("b805000000c3"))
    sent: list[dict] = []
    h = hub.Hub((granted,), False, write=sent.append)
    try:
        reply = h.install_tool("2akouwu/reverify", allow_read=[str(granted)])
        for _ in range(120):
            if not reply.startswith("Still installing"):
                break
            time.sleep(3)
            reply = h.install_status("2akouwu/reverify")
        assert "2akouwu/reverify is installed" in reply, reply
        assert "reverify__re_verify_claim" in reply
        assert {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"} in sent

        result = h.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "reverify__re_verify_claim", "arguments": {"target": "00", "claim": CLAIM}}})["result"]
        assert not result["isError"] and '"verified": 1' in result["content"][0]["text"]

        inside = h.use_tool("2akouwu/reverify", "re_verify_claim", {"target": str(granted / "blob.bin"), "claim": CLAIM})
        outside = h.use_tool("2akouwu/reverify", "re_verify_claim", {"target": str(other / "blob.bin"), "claim": CLAIM})
        assert not inside["isError"], inside
        # Byte-identical files; only the grant differs. How the denial shows
        # depends on the tool: reverify can't see the path, so it tries to
        # parse it as hex and fails. The grant is the only difference.
        assert outside["isError"], outside
    finally:
        h.close()
        shutil.rmtree(base)
