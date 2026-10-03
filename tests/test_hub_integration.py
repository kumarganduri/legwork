"""The hub for real: install pdfplumber from the public cache (no model key),
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
PDF = Path(__file__).parent / "fixtures" / "e2e" / "launch-plan.pdf"


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
        shutil.copy(PDF, folder / "plan.pdf")
    sent: list[dict] = []
    h = hub.Hub((granted,), False, write=sent.append)
    try:
        reply = h.install_tool("jsvine/pdfplumber", allow_read=[str(granted)])
        for _ in range(120):
            if not reply.startswith("Still installing"):
                break
            time.sleep(3)
            reply = h.install_status("jsvine/pdfplumber")
        assert "jsvine/pdfplumber is installed" in reply, reply
        assert "extract_tables" in reply and "pdfplumber__" in reply
        assert {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"} in sent

        result = h.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "pdfplumber__extract_tables", "arguments": {"path": str(granted / "plan.pdf")}}})["result"]
        assert not result["isError"] and "Offline sync conflicts" in result["content"][0]["text"], result

        # Byte-identical files; only the grant differs.
        outside = h.use_tool("jsvine/pdfplumber", "extract_tables", {"path": str(other / "plan.pdf")})
        assert outside["isError"] or "Offline sync conflicts" not in str(outside), outside
    finally:
        h.close()
        shutil.rmtree(base)
