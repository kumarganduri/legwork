"""End to end, as a new user with no API key: start the hub, search, install
a tool from the public cache, use it on a real file, and check the grant
refusals. CI runs this on every OS it tests; it needs network (GitHub for
search and the cache, PyPI for the install).

    uv run python scripts/e2e_hub.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from legwork.mcp_stdio import StdioMCPClient

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "e2e" / "launch-plan.pdf"


def text(result: dict) -> str:
    return "\n".join(item.get("text", "") for item in result.get("content", []))


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f": {detail[:300]}" if detail and not ok else ""), flush=True)
    if not ok:
        raise SystemExit(1)


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="legwork-e2e-"))
    docs = work / "docs"
    docs.mkdir()
    shutil.copy(FIXTURE, docs / "launch-plan.pdf")
    env = dict(os.environ, LEGWORK_HOME=str(work / "home"), LEGWORK_ENV_FILE=str(work / "no-key"))
    for name in ("LEGWORK_LLM_ENDPOINT", "LEGWORK_LLM_API_KEY", "LEGWORK_LLM_MODEL"):
        env.pop(name, None)
    legwork = shutil.which("legwork") or sys.exit("legwork isn't on PATH (run under `uv run`)")
    client = StdioMCPClient([legwork, "hub", "--allow-read", str(docs)], env)
    started = time.monotonic()
    try:
        client.initialize(client_name="e2e", timeout=60)
        check("hub starts", True)

        found = text(client.call_tool("find_tools", {"query": "read pdf tables"}, timeout=60))
        first = found.split("\n1. ", 1)[-1].split("\n", 1)[0]
        check("search puts the cached pdfplumber first", first.startswith("jsvine/pdfplumber") and "Legwork cache" in first, found)

        result = client.call_tool("install_tool", {"repo": "jsvine/pdfplumber", "allow_read": [str(docs)]}, timeout=120)
        deadline = time.monotonic() + 900
        while text(result).startswith("Still installing") and time.monotonic() < deadline:
            result = client.call_tool("install_status", {"repo": "jsvine/pdfplumber"}, timeout=120)
        check("install from the cache with no key", not result.get("isError") and "is installed" in text(result), text(result))

        tables = client.call_tool("pdfplumber__extract_tables", {"path": str(docs / "launch-plan.pdf")}, timeout=300)
        check("the tool reads the granted file", "Offline sync conflicts" in text(tables), text(tables))

        for secret in ("~/.ssh", "~/Library", str(Path.home()).upper()):
            refused = client.call_tool("install_tool", {"repo": "jsvine/pdfplumber", "allow_read": [secret]}, timeout=60)
            check(f"grant refused: {secret}", bool(refused.get("isError")), text(refused))
        net = client.call_tool("install_tool", {"repo": "jsvine/pdfplumber", "allow_read": [str(docs)], "allow_net": True}, timeout=60)
        check("network refused without --allow-net", bool(net.get("isError")), text(net))
    finally:
        client.close()
        shutil.rmtree(work, ignore_errors=True)
    print(f"all checks passed in {time.monotonic() - started:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
