import os
import shutil
import subprocess
from typing import List, Dict, Optional
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("magpie")


def _ensure_path_env(env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    e = dict(os.environ if env is None else env)
    # Common user-local bin dirs where installers place binaries
    candidate_bins = [
        os.path.join(os.path.expanduser("~"), ".local", "bin"),
        os.path.join(os.path.expanduser("~"), "bin"),
    ]
    current_path = e.get("PATH", "")
    parts = current_path.split(os.pathsep) if current_path else []
    for p in candidate_bins:
        if os.path.isdir(p) and p not in parts:
            parts.insert(0, p)
    e["PATH"] = os.pathsep.join(parts) if parts else current_path
    return e


def _find_magpie() -> str:
    env = _ensure_path_env()
    path = shutil.which("magpie", path=env.get("PATH"))
    if path:
        return path
    # Try likely default install locations explicitly
    candidates = [
        os.path.join(os.path.expanduser("~"), ".local", "bin", "magpie"),
        os.path.join(os.path.expanduser("~"), "bin", "magpie"),
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    raise FileNotFoundError(
        "magpie CLI not found on PATH. Ensure installation succeeded (curl -fsSL https://usemagpie.ai/install.sh | sh) "
        "and that your ~/.local/bin or ~/bin is on PATH."
    )


def _run_magpie(args: List[str], timeout: Optional[float] = None) -> Dict[str, object]:
    cmd = [_find_magpie()] + args
    env = _ensure_path_env()
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        timeout=timeout,
    )
    return {
        "args": args,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


@mcp.tool()
def magpie_run(args: List[str]) -> Dict[str, object]:
    """
    Run an arbitrary magpie CLI subcommand.
    Example args: ["presets"], ["models"], ["ls"], ["sync"]
    Returns: dict with keys: args, returncode, stdout, stderr
    """
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise TypeError("args must be a list of strings")
    return _run_magpie(args)


@mcp.tool()
def list_presets() -> Dict[str, object]:
    """
    List known provider presets (equivalent to `magpie presets`).
    Returns: dict with keys: args, returncode, stdout, stderr
    """
    return _run_magpie(["presets"])


@mcp.tool()
def list_models() -> Dict[str, object]:
    """
    Show the catalog of models agents can see (equivalent to `magpie models`).
    Returns: dict with keys: args, returncode, stdout, stderr
    """
    return _run_magpie(["models"])


@mcp.tool()
def list_agents() -> Dict[str, object]:
    """
    List detected agents and their current settings (equivalent to `magpie ls`).
    Returns: dict with keys: args, returncode, stdout, stderr
    """
    return _run_magpie(["ls"])


if __name__ == "__main__":
    # Self-test: call the simplest documented command (`magpie presets`)
    out = list_presets()
    # Basic shape checks only
    if not isinstance(out, dict):
        raise SystemExit("self-test failed: result is not a dict")
    for key in ("args", "returncode", "stdout", "stderr"):
        if key not in out:
            raise SystemExit(f"self-test failed: missing key {key}")
    if not isinstance(out["returncode"], int):
        raise SystemExit("self-test failed: returncode is not int")
    if not isinstance(out["stdout"], str):
        raise SystemExit("self-test failed: stdout is not str")
    if not isinstance(out["stderr"], str):
        raise SystemExit("self-test failed: stderr is not str")
    print("self-test passed")