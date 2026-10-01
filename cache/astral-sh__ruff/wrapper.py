import subprocess
import shlex
import shutil
import sys
from typing import List, Optional, Dict, Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ruff-mcp")


def _ruff_cmd() -> List[str]:
    """
    Resolve the Ruff command. Prefer the console script "ruff" if available,
    else fall back to "python -m ruff".
    """
    exe = shutil.which("ruff")
    if exe:
        return [exe]
    # Fallback to python -m ruff
    return [sys.executable, "-m", "ruff"]


def _run_ruff(args: List[str]) -> Dict[str, Any]:
    cmd = _ruff_cmd() + args
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return {
        "command": " ".join(shlex.quote(part) for part in cmd),
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


@mcp.tool()
def ruff_version() -> str:
    """
    Get the Ruff version string.
    """
    result = _run_ruff(["--version"])
    if result["stdout"]:
        return result["stdout"].strip()
    # Some versions might print to stderr
    if result["stderr"]:
        return result["stderr"].strip()
    return ""


@mcp.tool()
def ruff_check(
    paths: Optional[List[str]] = None,
    fix: bool = False,
    select: Optional[List[str]] = None,
    ignore: Optional[List[str]] = None,
    config: Optional[str] = None,
    preview: bool = False,
    quiet: bool = False,
    extra_args: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Run 'ruff check' to lint Python files.

    - paths: files or directories to lint; defaults to current directory if not provided.
    - fix: apply automatic fixes (equivalent to --fix).
    - select: list of rule codes to select (e.g., ["F401", "E501"]).
    - ignore: list of rule codes to ignore.
    - config: inline configuration or path to a ruff config file (passed via --config).
    - preview: enable preview mode (--preview).
    - quiet: reduce output verbosity (--quiet).
    - extra_args: additional raw arguments passed through as-is.
    """
    args: List[str] = ["check"]

    if fix:
        args.append("--fix")
    if preview:
        args.append("--preview")
    if quiet:
        args.append("--quiet")

    if select:
        for code in select:
            args.extend(["--select", code])
    if ignore:
        for code in ignore:
            args.extend(["--ignore", code])
    if config:
        args.extend(["--config", config])

    if extra_args:
        args.extend(extra_args)

    if paths:
        args.extend(paths)
    else:
        args.append(".")

    return _run_ruff(args)


@mcp.tool()
def ruff_format(
    paths: Optional[List[str]] = None,
    check: bool = False,
    diff: bool = False,
    quiet: bool = False,
    preview: bool = False,
    extra_args: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Run 'ruff format' to format Python files.

    - paths: files or directories to format; defaults to current directory if not provided.
    - check: don't write changes; exit non-zero if files would be reformatted (--check).
    - diff: print the diff of changes that would be made (--diff).
    - quiet: reduce output verbosity (--quiet).
    - preview: enable preview mode (--preview).
    - extra_args: additional raw arguments passed through as-is.
    """
    args: List[str] = ["format"]

    if check:
        args.append("--check")
    if diff:
        args.append("--diff")
    if quiet:
        args.append("--quiet")
    if preview:
        args.append("--preview")

    if extra_args:
        args.extend(extra_args)

    if paths:
        args.extend(paths)
    else:
        args.append(".")

    return _run_ruff(args)


if __name__ == "__main__":
    # Self-test: verify Ruff is invocable and returns a non-empty version string.
    ver = ruff_version()
    if not ver:
        raise SystemExit("Self-test failed: Ruff returned an empty version string.")
    print(f"Self-test passed: {ver}")