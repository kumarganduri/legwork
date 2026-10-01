import json
import os
import shutil
import subprocess
from typing import List, Optional, Dict, Any, Union

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("shellcheck-wrapper")

def _shellcheck_path() -> str:
    # Prefer local binary installed by the install step
    here = os.path.dirname(os.path.abspath(__file__))
    local_path = os.path.join(here, "bin", "shellcheck")
    if os.path.exists(local_path) and os.access(local_path, os.X_OK):
        return local_path
    # Fallback to PATH
    found = shutil.which("shellcheck")
    if found:
        return found
    raise RuntimeError("ShellCheck binary not found. Ensure install step downloaded it to ./bin/shellcheck or it's on PATH.")

def _run_shellcheck(
    args: List[str],
    stdin_data: Optional[str] = None,
) -> Dict[str, Any]:
    cmd = [_shellcheck_path()] + args
    # Don't use check=True since nonzero exit codes indicate findings
    proc = subprocess.run(
        cmd,
        input=stdin_data if stdin_data is not None else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout = proc.stdout or ""
    # ShellCheck prints diagnostics as JSON to stdout with -f json
    if not stdout.strip():
        # If no output, raise with stderr to aid debugging
        raise RuntimeError(f"ShellCheck produced no output. Stderr: {proc.stderr.strip()}")
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse ShellCheck JSON output: {e}\nRaw output:\n{stdout[:1000]}")
    # Normalize to a list of comments regardless of legacy/different shapes
    if isinstance(data, dict):
        comments = data.get("comments", [])
    elif isinstance(data, list):
        comments = data
    else:
        raise RuntimeError("Unexpected ShellCheck JSON structure")
    issues = []
    for c in comments:
        if not isinstance(c, dict):
            continue
        issues.append({
            "file": c.get("file"),
            "line": c.get("line"),
            "endLine": c.get("endLine"),
            "column": c.get("column"),
            "endColumn": c.get("endColumn"),
            "level": c.get("level"),
            "code": c.get("code"),
            "message": c.get("message"),
        })
    # Simple summary
    summary: Dict[str, int] = {}
    for it in issues:
        lvl = it.get("level") or "unknown"
        summary[lvl] = summary.get(lvl, 0) + 1
    return {"issues": issues, "summary": summary}

def _build_args(
    shell: Optional[str],
    severity: Optional[str],
    exclude_codes: Optional[List[str]],
    include_dirs: Optional[List[str]],
    allow_external_sources: bool,
) -> List[str]:
    args = ["-f", "json"]
    if shell:
        args += ["-s", shell]
    if severity:
        args += ["-S", severity]
    if exclude_codes:
        # ShellCheck supports comma-separated codes
        joined = ",".join(exclude_codes)
        args += ["-e", joined]
    if include_dirs:
        for d in include_dirs:
            if d:
                args += ["-I", d]
    if allow_external_sources:
        args.append("-x")
    return args

@mcp.tool()
def shellcheck_text(
    script: str,
    shell: Optional[str] = None,
    severity: Optional[str] = None,
    exclude_codes: Optional[List[str]] = None,
    include_dirs: Optional[List[str]] = None,
    allow_external_sources: bool = False,
) -> Dict[str, Any]:
    """
    Lint a shell script provided as text and return structured diagnostics.
    - script: the shell script contents
    - shell: e.g. "bash", "sh", "dash", "ksh"
    - severity: minimum severity to display: "error", "warning", "info", or "style"
    - exclude_codes: list of rule codes (e.g. ["SC2086","SC2046"]) to suppress
    - include_dirs: list of directories to search for 'source'd files (-I)
    - allow_external_sources: if True, allow external sources (-x)
    """
    args = _build_args(shell, severity, exclude_codes, include_dirs, allow_external_sources)
    args.append("-")  # read from stdin
    result = _run_shellcheck(args, stdin_data=script)
    # For stdin, ensure file is labeled consistently for clients
    for it in result["issues"]:
        if not it.get("file"):
            it["file"] = "(stdin)"
    return result

@mcp.tool()
def shellcheck_files(
    paths: List[str],
    shell: Optional[str] = None,
    severity: Optional[str] = None,
    exclude_codes: Optional[List[str]] = None,
    include_dirs: Optional[List[str]] = None,
    allow_external_sources: bool = False,
) -> Dict[str, Any]:
    """
    Lint one or more shell script files and return structured diagnostics.
    - paths: list of file paths to analyze
    - shell: e.g. "bash", "sh", "dash", "ksh"
    - severity: minimum severity to display: "error", "warning", "info", or "style"
    - exclude_codes: list of rule codes (e.g. ["SC2086","SC2046"]) to suppress
    - include_dirs: list of directories to search for 'source'd files (-I)
    - allow_external_sources: if True, allow external sources (-x)
    """
    if not paths:
        raise ValueError("paths must not be empty")
    for p in paths:
        if not os.path.isfile(p):
            raise FileNotFoundError(f"File not found: {p}")
    args = _build_args(shell, severity, exclude_codes, include_dirs, allow_external_sources)
    args += paths
    return _run_shellcheck(args)

@mcp.tool()
def shellcheck_version() -> str:
    """
    Return ShellCheck version string (shellcheck --version).
    """
    proc = subprocess.run([_shellcheck_path(), "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out = proc.stdout.strip() or proc.stderr.strip()
    if not out:
        raise RuntimeError("Failed to get ShellCheck version")
    return out

if __name__ == "__main__":
    # Self-test: verify shellcheck is callable and returns a non-empty version string.
    ver = shellcheck_version()
    if not ver.strip():
        raise RuntimeError("ShellCheck version output was empty")
    # Also perform a quick lint to ensure JSON path works (no assertions on findings)
    demo_script = "var = 42\n"
    _ = shellcheck_text(script=demo_script)
    print("OK:", ver.splitlines()[0])