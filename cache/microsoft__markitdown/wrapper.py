from __future__ import annotations

import subprocess
from typing import Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("markitdown-wrapper")


@mcp.tool()
def convert_to_markdown(path_or_uri: str, enable_plugins: bool = False) -> str:
    """
    Convert a local file path or URL to Markdown using MarkItDown.
    Returns the Markdown string.

    Parameters:
    - path_or_uri: Path to a local file or a URL supported by MarkItDown (e.g., YouTube).
    - enable_plugins: If True, enables any installed MarkItDown plugins during conversion.
    """
    from markitdown import MarkItDown  # Imported here to keep module import lightweight

    md = MarkItDown(enable_plugins=enable_plugins)
    result = md.convert(path_or_uri)
    markdown = getattr(result, "markdown", None)
    if not markdown:
        # Fallback: some future versions might return printable objects
        markdown = str(result) if result is not None else ""
    if not markdown:
        raise RuntimeError("MarkItDown returned empty output")
    return markdown


@mcp.tool()
def list_plugins() -> str:
    """
    List installed MarkItDown plugins (equivalent to `markitdown --list-plugins`).
    """
    proc = subprocess.run(
      ["markitdown", "--list-plugins"],
      check=True,
      capture_output=True,
      text=True,
    )
    out = (proc.stdout or "").strip()
    if not out:
        out = (proc.stderr or "").strip()
    return out or "(no plugins output)"


@mcp.tool()
def cli_help() -> str:
    """
    Return the MarkItDown CLI help text (equivalent to `markitdown --help`).
    Useful for diagnostics and to discover CLI flags.
    """
    proc = subprocess.run(
        ["markitdown", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    out = (proc.stdout or "").strip()
    if not out:
        out = (proc.stderr or "").strip()
    return out or "(no help output)"


def _self_test() -> None:
    # Offline, quick check: ensure the CLI is installed and responsive.
    proc = subprocess.run(
        ["markitdown", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    output = (proc.stdout or proc.stderr or "").strip()
    if not output:
        raise RuntimeError("Self-test failed: `markitdown --help` produced no output.")


if __name__ == "__main__":
    _self_test()
    print("Self-test passed: markitdown is available and responsive.")