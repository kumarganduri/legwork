import os
import sys
import shutil
import subprocess
from typing import List, Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("prettier-wrapper")


def _prettier_bin() -> str:
    # Resolve local prettier binary installed via npm
    bin_name = "prettier.cmd" if os.name == "nt" else "prettier"
    local_bin = os.path.join(os.getcwd(), "node_modules", ".bin", bin_name)
    if os.path.isfile(local_bin) and os.access(local_bin, os.X_OK):
        return local_bin
    # Fallback to PATH if available (e.g., when run inside environments that expose it)
    found = shutil.which("prettier")
    if found:
        return found
    raise FileNotFoundError(
        "Prettier CLI not found. Install it locally with: npm install prettier"
    )


def _run_prettier(args: List[str], input_text: Optional[str] = None) -> subprocess.CompletedProcess:
    cmd = [_prettier_bin(), *args]
    # We don't use check=True so we can return output even on non-zero exit (e.g., --check failures)
    return subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=True,
    )


@mcp.tool()
def prettier_version() -> str:
    """
    Get the installed Prettier CLI version string.
    """
    proc = _run_prettier(["--version"])
    out = (proc.stdout or proc.stderr or "").strip()
    if not out:
        raise RuntimeError("Prettier returned empty version output")
    return out


@mcp.tool()
def prettier_format_code(
    code: str,
    parser: Optional[str] = None,
    filepath_hint: Optional[str] = None,
    config_path: Optional[str] = None,
    print_width: Optional[int] = None,
    tab_width: Optional[int] = None,
    use_tabs: Optional[bool] = None,
    single_quote: Optional[bool] = None,
    trailing_comma: Optional[str] = None,
    semi: Optional[bool] = None,
) -> str:
    """
    Format a code string with Prettier and return the formatted code.

    Parameters:
    - code: the source code to format
    - parser: optional Prettier parser (e.g., 'babel', 'typescript', 'json', 'markdown', 'html', 'css', 'yaml')
    - filepath_hint: optional pseudo filename to help Prettier infer the parser (e.g., 'file.ts', 'notes.md')
    - config_path: path to a Prettier config file to use
    - print_width, tab_width: numeric formatting options
    - use_tabs, single_quote, semi: boolean formatting options
    - trailing_comma: string option (e.g., 'none', 'es5', 'all')
    """
    args: List[str] = []

    # Use stdin; hint parser via --stdin-filepath if provided, else explicit --parser if provided
    if filepath_hint:
        args += ["--stdin-filepath", filepath_hint]
    elif parser:
        args += ["--parser", parser]

    if config_path:
        args += ["--config", config_path]
    if print_width is not None:
        args += ["--print-width", str(print_width)]
    if tab_width is not None:
        args += ["--tab-width", str(tab_width)]
    if use_tabs is True:
        args += ["--use-tabs"]
    elif use_tabs is False:
        args += ["--no-use-tabs"]
    if single_quote is True:
        args += ["--single-quote"]
    elif single_quote is False:
        args += ["--no-single-quote"]
    if semi is True:
        args += ["--semi"]
    elif semi is False:
        args += ["--no-semi"]
    if trailing_comma:
        args += ["--trailing-comma", trailing_comma]

    proc = _run_prettier(args, input_text=code)
    if proc.returncode != 0:
        combined = (proc.stdout or "") + (proc.stderr or "")
        raise RuntimeError(f"Prettier failed (exit {proc.returncode}): {combined.strip()}")
    return proc.stdout


@mcp.tool()
def prettier_check_files(paths: List[str]) -> str:
    """
    Check files for Prettier formatting issues without writing changes.
    Returns Prettier's combined output (stdout/stderr).
    """
    if not paths:
        raise ValueError("Provide at least one file path")
    args = ["--check", *paths]
    proc = _run_prettier(args)
    # Return combined output regardless of exit code; caller can inspect text
    return ((proc.stdout or "") + (proc.stderr or "")).strip()


@mcp.tool()
def prettier_write_files(paths: List[str]) -> str:
    """
    Format and write changes to the given files in-place.
    Returns Prettier's output summary.
    """
    if not paths:
        raise ValueError("Provide at least one file path")
    args = ["--write", *paths]
    proc = _run_prettier(args)
    # Return combined output; Prettier usually exits 0 on write
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return output or "Done"


if __name__ == "__main__":
    # Self-test: verify Prettier is callable and returns a non-empty version string
    ver = prettier_version()
    print("Prettier version:", ver)
    if not ver:
        raise SystemExit("Self-test failed: empty version string")
    # Additionally exercise format on a tiny snippet offline
    sample = "const x=1;"
    formatted = prettier_format_code(sample, parser="babel")
    if not formatted:
        raise SystemExit("Self-test failed: empty formatted output")
    print("Formatted sample OK")