from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("sqlfluff-mcp")


def _run_sqlfluff(args: list[str]) -> str:
    exe = shutil.which("sqlfluff")
    if not exe:
        raise RuntimeError("sqlfluff CLI not found on PATH. Ensure it is installed.")
    proc = subprocess.run(
        [exe] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    # For lint, a non-zero exit code can simply indicate lint errors.
    output = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    return output.strip()


@mcp.tool()
def get_version() -> str:
    """
    Return the installed sqlfluff version string.
    """
    out = _run_sqlfluff(["--version"])
    if not out:
        raise RuntimeError("Empty response from sqlfluff --version")
    return out


@mcp.tool()
def lint_sql(sql: str, dialect: str = "ansi") -> str:
    """
    Lint a SQL string using sqlfluff and return the CLI output as text.

    Args:
        sql: The SQL code to lint.
        dialect: SQL dialect to use (e.g., 'ansi', 'postgres', 'snowflake', etc.).
    """
    # Write to a temporary .sql file within the current working directory
    # to avoid sandbox write restrictions.
    tmp_dir = os.getcwd()
    fd, path = tempfile.mkstemp(suffix=".sql", dir=tmp_dir, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(sql)
        out = _run_sqlfluff(["lint", path, "--dialect", dialect])
        if not out:
            raise RuntimeError("sqlfluff lint produced no output")
        return out
    finally:
        try:
            os.remove(path)
        except Exception:
            pass


if __name__ == "__main__":
    # Self-test: call the simplest documented/standard commands offline.
    v = get_version()
    if not v.strip():
        raise SystemExit("Self-test failed: empty version output")

    sample = "SELECT 1"
    lint_out = lint_sql(sample, dialect="ansi")
    if not lint_out.strip():
        raise SystemExit("Self-test failed: empty lint output")

    print("SELF-TEST OK")