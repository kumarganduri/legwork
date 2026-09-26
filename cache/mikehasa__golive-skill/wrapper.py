import json
import os
import subprocess
from typing import Optional, Dict

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("golive")


def _find_golive_cli() -> list:
    """
    Returns a command list to execute the GoLive CLI.
    Prefers the locally installed package's bin; falls back to npx if not found.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    local_cli = os.path.join(here, "node_modules", "golive", "bin", "golive.mjs")
    if os.path.isfile(local_cli):
        return ["node", local_cli]
    # Fallback to npx if local install is missing
    return ["npx", "-y", "golive@alpha"]


def _run_golive_json(args: list, cwd: Optional[str] = None) -> Dict:
    cmd = _find_golive_cli() + args + ["--json"]
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"golive command failed ({proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}")
    try:
        return json.loads(proc.stdout.strip() or "{}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse golive JSON output: {e}\nRaw: {proc.stdout[:500]}")


@mcp.tool()
def golive_version() -> Dict:
    """
    Get GoLive CLI version and release identity (read-only).
    Returns the JSON produced by: golive version --json
    """
    return _run_golive_json(["version"])


@mcp.tool()
def golive_detect(path: Optional[str] = None) -> Dict:
    """
    Run GoLive's project detection (read-only).
    - path: Directory of the app repository to inspect (defaults to current directory).
    Returns the JSON produced by: golive detect --json
    """
    cwd = path or None
    return _run_golive_json(["detect"], cwd=cwd)


@mcp.tool()
def golive_help() -> str:
    """
    Return GoLive CLI help text.
    """
    cmd = _find_golive_cli() + ["help"]
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"golive help failed ({proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


if __name__ == "__main__":
    # Self-test: simplest documented command "version --json"
    data = golive_version()
    if not isinstance(data, dict):
        raise SystemExit("Self-test failed: golive_version did not return a dict")
    # Accept any of these common keys; don't assert domain-specific semantics
    if not any(k in data for k in ("version", "name", "current")):
        raise SystemExit(f"Self-test failed: unexpected version shape: keys={list(data.keys())}")
    print("Self-test passed: golive_version returned JSON with expected structure.")