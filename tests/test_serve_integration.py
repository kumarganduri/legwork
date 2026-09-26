"""End to end: `legwork build` then `legwork serve`, with a real MCP client
talking to the served wrapper over stdio — the same thing Claude Code does
after `claude mcp add`.

Real: GitHub clone, obfuscation scan, sandboxed venv + `pip install`,
self-test, the separate `legwork serve` process, sandbox-exec with network
off, the MCP handshake, and a real tool call that runs reverify. Mocked: only
the model's reply (hand-written to the codegen contract), so the suite
doesn't spend API credit. The live runs cover the real model."""

from __future__ import annotations

import json
import os
import shutil
import sys
from unittest.mock import patch

import pytest

from legwork import cli, local_store
from legwork.repo_fetcher import RepoRef
from tests.mcp_stdio_client import StdioMCPClient

FASTMCP_REVERIFY_REPLY = '''\
ENTRYPOINT: `reverify auto <binary> --json` auto-triages a binary file

```install
pip install "reverify[full]"
```

```python
import json
import subprocess

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("reverify")


@mcp.tool()
def reverify_auto(binary_path: str) -> dict:
    """Auto-triage a binary: format, sections, top strings."""
    result = subprocess.run(
        ["reverify", "auto", binary_path, "--json"], capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


if __name__ == "__main__":
    out = reverify_auto("/bin/ls")
    if not isinstance(out, dict) or "filename" not in out:
        raise AssertionError(f"unexpected shape: {out!r}")
    print("self-test passed")
```
'''


@pytest.mark.skipif(shutil.which("sandbox-exec") is None, reason="sandbox-exec is macOS-only")
def test_build_then_serve_over_stdio_with_a_real_mcp_client(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGWORK_HOME", str(tmp_path))
    monkeypatch.setenv("LEGWORK_LLM_ENDPOINT", "https://unused.example.com/v1")
    monkeypatch.setenv("LEGWORK_LLM_API_KEY", "unused")
    monkeypatch.setenv("LEGWORK_LLM_MODEL", "unused")

    with patch("legwork.retry_loop.complete", return_value=FASTMCP_REVERIFY_REPLY):
        assert cli.cmd_build("2akouwu/reverify") == 0
    local_store.load_current(RepoRef("2akouwu", "reverify"))  # recorded and complete

    env = {**os.environ, "LEGWORK_HOME": str(tmp_path)}
    client = StdioMCPClient([sys.executable, "-m", "legwork.cli", "serve", "2akouwu/reverify"], env)
    try:
        init = client.initialize()
        assert init["result"]["serverInfo"]["name"] == "reverify"

        tools = client.request("tools/list")["result"]["tools"]
        assert [t["name"] for t in tools] == ["reverify_auto"]

        call = client.request("tools/call", {"name": "reverify_auto", "arguments": {"binary_path": "/bin/ls"}}, timeout=120)
        assert call["result"].get("isError") is False, call
        text = call["result"]["content"][0]["text"]
        assert json.loads(text)["filename"] == "ls"
    finally:
        client.close()
