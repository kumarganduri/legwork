import json
import os
import subprocess
import sys
from typing import List, Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("gitleaks-mcp")

def _gitleaks_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(here, "gitleaks-bin", "gitleaks")
    if not (os.path.exists(p) and os.access(p, os.X_OK)):
        raise FileNotFoundError(f"gitleaks binary not found at {p}. Run the install step first.")
    return p

def _run_scan(args: List[str], stdin_data: Optional[str] = None) -> dict:
    cmd = [_gitleaks_path()] + args + ["--no-banner", "--no-color", "-f", "json", "-r", "-"]
    proc = subprocess.run(
        cmd,
        input=stdin_data,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out = proc.stdout.strip()
    # gitleaks returns non-zero exit code when leaks found; that's not an error for us.
    if out == "":
        # No findings yields empty list; but if JSON failed to print, include stderr to aid debugging.
        if proc.returncode not in (0, 1):  # 1 can mean leaks or error; prefer to try to parse anyway
            raise RuntimeError(f"gitleaks failed: rc={proc.returncode}\n{proc.stderr.strip()}")
        return []
    try:
        data = json.loads(out)
        if isinstance(data, list):
            return data
        # Some outputs may be an object; wrap to list for consistency
        return [data]
    except json.JSONDecodeError:
        # If JSON parse fails, surface combined output
        raise RuntimeError(f"Failed to parse gitleaks JSON output.\nSTDOUT:\n{out}\nSTDERR:\n{proc.stderr.strip()}")

_KEEP = ("RuleID", "Description", "File", "StartLine", "EndLine", "Secret", "Match", "Commit", "Author", "Date")


def _report(findings: List[dict]) -> dict:
    """An explicit count, so "nothing found" never looks like an empty reply."""
    return {"count": len(findings), "leaks": [{k: f.get(k) for k in _KEEP if f.get(k) not in (None, "")} for f in findings]}


def _common_flags(
    config_path: Optional[str],
    enable_rules: Optional[List[str]],
    redact: Optional[int],
    baseline_path: Optional[str],
    max_decode_depth: Optional[int],
    max_archive_depth: Optional[int],
    timeout_seconds: Optional[int],
    verbose: bool,
) -> List[str]:
    flags: List[str] = []
    if config_path:
        flags += ["-c", config_path]
    if enable_rules:
        for rid in enable_rules:
            flags += ["--enable-rule", rid]
    if redact is not None:
        # --redact takes an optional value: "--redact 100" made 100 the scan
        # path and gitleaks scanned the tool's own folder instead.
        flags += [f"--redact={redact}"]
    if baseline_path:
        flags += ["--baseline-path", baseline_path]
    if max_decode_depth is not None and max_decode_depth > 0:
        flags += ["--max-decode-depth", str(max_decode_depth)]
    if max_archive_depth is not None and max_archive_depth > 0:
        flags += ["--max-archive-depth", str(max_archive_depth)]
    if timeout_seconds is not None and timeout_seconds > 0:
        flags += ["--timeout", str(timeout_seconds)]
    if verbose:
        flags += ["-v"]
    return flags

@mcp.tool()
def gitleaks_version() -> str:
    """
    Get the gitleaks version string.
    """
    proc = subprocess.run([_gitleaks_path(), "version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out = (proc.stdout or proc.stderr or "").strip()
    if not out:
        raise RuntimeError("gitleaks returned empty version string")
    return out

@mcp.tool()
def scan_directory(
    path: str = ".",
    config_path: Optional[str] = None,
    enable_rules: Optional[List[str]] = None,
    redact: Optional[int] = 100,
    baseline_path: Optional[str] = None,
    max_decode_depth: Optional[int] = 0,
    max_archive_depth: Optional[int] = 0,
    timeout_seconds: Optional[int] = None,
    verbose: bool = False,
) -> dict:
    """
    Scan a directory or file path for secrets and return findings as a JSON list.
    """
    args = ["dir"]
    args += _common_flags(config_path, enable_rules, redact, baseline_path, max_decode_depth, max_archive_depth, timeout_seconds, verbose)
    if path:
        args.append(path)
    return _report(_run_scan(args))

@mcp.tool()
def scan_git_repository(
    repo_path: str = ".",
    log_opts: Optional[str] = None,
    config_path: Optional[str] = None,
    enable_rules: Optional[List[str]] = None,
    redact: Optional[int] = 100,
    baseline_path: Optional[str] = None,
    max_decode_depth: Optional[int] = 0,
    max_archive_depth: Optional[int] = 0,
    timeout_seconds: Optional[int] = None,
    verbose: bool = False,
) -> dict:
    """
    Scan a git repository for secrets and return findings as a JSON list.
    """
    args = ["git"]
    if log_opts:
        args += ["--log-opts", log_opts]
    args += _common_flags(config_path, enable_rules, redact, baseline_path, max_decode_depth, max_archive_depth, timeout_seconds, verbose)
    if repo_path:
        args.append(repo_path)
    return _report(_run_scan(args))

@mcp.tool()
def scan_stdin(
    data: str,
    config_path: Optional[str] = None,
    enable_rules: Optional[List[str]] = None,
    redact: Optional[int] = 100,
    max_decode_depth: Optional[int] = 0,
    timeout_seconds: Optional[int] = None,
    verbose: bool = False,
) -> dict:
    """
    Scan the provided string for secrets via stdin and return findings as a JSON list.
    """
    args = ["stdin"]
    # Archive depth not applicable to stdin; baseline also not applicable
    args += _common_flags(config_path, enable_rules, redact, None, max_decode_depth, 0, timeout_seconds, verbose)
    return _report(_run_scan(args, stdin_data=data))

if __name__ == "__main__":
    # Self-test: ensure gitleaks binary is callable offline and returns a non-empty version string.
    v = gitleaks_version()
    if not v:
        raise SystemExit("Self-test failed: empty version output")
    print(f"gitleaks version: {v}")
    # Also run a trivial scan on an empty temp file to ensure JSON path works.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        test_file = os.path.join(td, "test.txt")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("hello world")
        findings = scan_directory(td, verbose=False)
        # We don't assert on pass/fail; just ensure the call succeeded and returned a report.
        if findings is None:
            raise SystemExit("Self-test failed: scan_directory returned None")
        print(f"scan_directory returned {findings['count']} findings")