import subprocess
from typing import List, Optional, Literal

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("csvkit-mcp")


@mcp.tool()
def csvkit_run(
    command: Literal[
        "csvclean",
        "csvcut",
        "csvformat",
        "csvgrep",
        "csvjoin",
        "csvjson",
        "csvlook",
        "csvsort",
        "csvsql",
        "csvstack",
        "csvstat",
        "in2csv",
        "sql2csv",
    ],
    args: List[str] = [],
    input_data: Optional[str] = None,
) -> str:
    """
    Run a csvkit subcommand with optional arguments and optional stdin.

    - command: one of csvkit's CLI tools (e.g., "csvcut", "csvstat", "in2csv").
    - args: list of additional CLI arguments, e.g., ["-c", "name,age"] or ["--help"].
    - input_data: if provided, will be piped to the command's stdin (useful for commands that accept stdin).
    """
    try:
        proc = subprocess.run(
            [command] + list(args),
            input=input_data,
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as e:
        raise RuntimeError(f"Command '{command}' not found on PATH. Is csvkit installed?") from e

    if proc.returncode != 0:
        # Prefer stderr for errors, fall back to stdout if needed.
        msg = proc.stderr.strip() or proc.stdout.strip() or f"{command} exited with code {proc.returncode}"
        raise RuntimeError(msg)

    # Some commands print help/version to stdout; ensure we return something meaningful.
    output = proc.stdout if proc.stdout else proc.stderr
    return output


if __name__ == "__main__":
    # Self-test: run a simple offline command that should always produce some output.
    out = csvkit_run("csvcut", ["--help"])
    if not out or not out.strip():
        raise SystemExit("Self-test failed: csvcut --help produced no output.")
    print("Self-test passed: csvcut --help returned output.")