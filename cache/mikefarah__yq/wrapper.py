import json
import os
import shutil
import subprocess
import tempfile
from typing import List, Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("yq-mcp")


def _yq_path() -> str:
    # Allow override via env var; default to local ./bin/yq installed by the install step
    candidate = os.environ.get("YQ_BINARY", os.path.join(".", "bin", "yq"))
    abs_path = os.path.abspath(candidate)
    if not (os.path.exists(abs_path) and os.access(abs_path, os.X_OK)):
        raise FileNotFoundError(
            f"yq binary not found or not executable at {abs_path}. "
            "Run the provided install step to download it, or set YQ_BINARY to a valid path."
        )
    return abs_path


def _run_yq(args: List[str], stdin_data: Optional[bytes] = None) -> str:
    yq = _yq_path()
    # Disable colors to keep output clean; default unwrapScalar behavior is left as-is unless specified
    cmd = [yq, "-M"] + args
    proc = subprocess.run(
        cmd,
        input=stdin_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        # Include stderr for better error messages
        raise RuntimeError(proc.stderr.decode("utf-8", errors="replace") or f"yq failed with code {proc.returncode}")
    return proc.stdout.decode("utf-8", errors="replace")


@mcp.tool()
def yq_version() -> str:
    """
    Get yq version string.
    """
    out = _run_yq(["--version"])
    return out.strip()


@mcp.tool()
def yq_eval_files(
    expression: str,
    files: Optional[List[str]] = None,
    input_format: Optional[str] = None,
    output_format: Optional[str] = None,
    pretty_print: bool = False,
    unwrap_scalar: bool = False,
    null_input: bool = False,
) -> str:
    """
    Evaluate a yq expression against one or more files.

    - expression: jq-like expression to run.
    - files: list of file paths to process; if omitted, reads from STDIN (use yq_eval_string instead).
    - input_format: one of [auto|yaml|json|kyaml|props|csv|tsv|xml|base64|uri|toml|hcl|lua|ini].
    - output_format: one of [auto|yaml|json|kyaml|props|csv|tsv|xml|base64|uri|toml|hcl|shell|lua|ini].
    - pretty_print: pretty print output (shorthand for '... style = ""' for yaml).
    - unwrap_scalar: print just the scalar value with no quotes/colors/comments (equivalent to -r).
    - null_input: do not read input, just evaluate expression (-n).
    """
    args: List[str] = []
    if null_input:
        args.append("-n")
    if input_format:
        args.extend(["-p", input_format])
    if output_format:
        args.extend(["-o", output_format])
    if pretty_print:
        args.append("-P")
    if unwrap_scalar:
        args.append("-r")

    # Expression must be a single argument
    args.append(expression)

    file_list = files or []
    args.extend(file_list)

    return _run_yq(args)


@mcp.tool()
def yq_eval_string(
    expression: str,
    content: str,
    input_format: Optional[str] = None,
    output_format: Optional[str] = None,
    pretty_print: bool = False,
    unwrap_scalar: bool = False,
) -> str:
    """
    Evaluate a yq expression on in-memory content provided via STDIN.

    - expression: jq-like expression to run.
    - content: the input document(s) as a string.
    - input_format: parse format (yaml/json/xml/etc.). Defaults to auto if not set.
    - output_format: output format (yaml/json/xml/etc.). Defaults to auto if not set.
    - pretty_print: pretty print output (for yaml pretty-print).
    - unwrap_scalar: print just the scalar value without quotes/colors/comments (-r).
    """
    args: List[str] = []
    if input_format:
        args.extend(["-p", input_format])
    if output_format:
        args.extend(["-o", output_format])
    if pretty_print:
        args.append("-P")
    if unwrap_scalar:
        args.append("-r")

    args.append(expression)
    return _run_yq(args, stdin_data=content.encode("utf-8"))


@mcp.tool()
def yq_convert(
    content: str,
    input_format: str,
    output_format: str,
    pretty_print: bool = True,
) -> str:
    """
    Convert content from one format to another using yq. Equivalent to running '.' as the expression.

    - content: the input document(s) as a string.
    - input_format: parse format (e.g., json, yaml, xml, ini, toml, hcl, csv, tsv, props).
    - output_format: output format (e.g., json, yaml, xml, ini, toml, hcl, csv, tsv, props).
    - pretty_print: pretty print output when applicable (e.g., yaml).
    """
    args: List[str] = ["-p", input_format, "-o", output_format]
    if pretty_print:
        args.append("-P")
    args.append(".")
    return _run_yq(args, stdin_data=content.encode("utf-8"))


if __name__ == "__main__":
    # Self-test: ensure yq is present and runnable offline
    ver = yq_version()
    if not ver:
        raise SystemExit("yq --version returned empty output")

    # Also test a trivial evaluation on a small YAML string
    sample = "a: 1\nb: 2\n"
    result = yq_eval_string(".a", sample, input_format="yaml", output_format="yaml", unwrap_scalar=True)
    if not result.strip():
        raise SystemExit("yq eval on sample content returned empty output")

    print("Self-test passed: yq is available and basic evaluation works.")