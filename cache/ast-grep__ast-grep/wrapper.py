import os
import shutil
import subprocess
from typing import Optional, List

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ast-grep")

def _find_ast_grep() -> str:
    """
    Locate the ast-grep binary.

    Preference order:
    1) ast-grep from PATH (e.g., provided by pip install ast-grep-cli)
    2) ./node_modules/.bin/ast-grep (if installed via local npm)
    """
    exe = shutil.which("ast-grep")
    if exe:
        return exe
    # Fallback to local npm install location if present
    local_npm_bin = os.path.join(".", "node_modules", ".bin", "ast-grep")
    if os.path.exists(local_npm_bin) and os.access(local_npm_bin, os.X_OK):
        return local_npm_bin
    raise FileNotFoundError(
        "ast-grep executable not found. Please ensure 'pip install ast-grep-cli' "
        "or 'npm install @ast-grep/cli' (local) has been run."
        )

def _run_sg(args: List[str], cwd: Optional[str] = None) -> str:
    """
    Run ast-grep with given arguments and return its combined output.
    Raises RuntimeError on non-zero exit codes.
    """
    binary = _find_ast_grep()
    cmd = [binary] + args
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(f"ast-grep failed: {' '.join(cmd)}\n{out.strip()}")
    return out.strip() or "OK"

@mcp.tool()
def sg_version() -> str:
    """
    Get ast-grep version string.
    """
    return _run_sg(["--version"])

@mcp.tool()
def sg_search(
    pattern: str,
    lang: str,
    path: str = ".",
    json_output: bool = False,
) -> str:
    """
    Search for AST pattern within a path.

    - pattern: ast-grep pattern, e.g., 'var code = $PATTERN'
    - lang: language for parsing (e.g., ts, js, py, rs, etc.)
    - path: directory or file to search (default '.')
    - json_output: output matches in JSON format
    """
    # No ignore-case option: ast-grep matches syntax trees and has none. The
    # old one passed -i, which is --interactive (known-answer test, 2026-10-11).
    args = ["-p", pattern, "-l", lang, path]
    if json_output:
        args.append("--json")
    return _run_sg(args)

@mcp.tool()
def sg_rewrite(
    pattern: str,
    rewrite: str,
    lang: str,
    path: str = ".",
    dry_run: bool = True,
) -> str:
    """
    Preview or perform rewrite using an AST pattern.

    - pattern: match pattern
    - rewrite: replacement template
    - lang: language for parsing
    - path: directory or file to operate on
    - dry_run: if True, only previews changes (does not write). If False, attempts to write changes.
    """
    args = ["-p", pattern, "-l", lang, "-r", rewrite, path]
    # By default ast-grep previews changes; --update-all (-U) applies them.
    # There is no --write flag in ast-grep 0.45.
    if not dry_run:
        args.append("--update-all")
    return _run_sg(args)

if __name__ == "__main__":
    # Self-test: simplest offline check - ensure version returns something non-empty
    ver = sg_version()
    if not ver:
        raise SystemExit("ast-grep --version returned empty output")
    print("Self-test OK: ast-grep version:", ver.splitlines()[0])