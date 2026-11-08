from __future__ import annotations

from typing import Any, Dict, List, Optional
from mcp.server.fastmcp import FastMCP
import markdown

mcp = FastMCP("python-markdown")


@mcp.tool()
def render_markdown(
    text: str,
    extensions: Optional[List[str]] = None,
    extension_configs: Optional[Dict[str, Dict[str, Any]]] = None,
    output_format: str = "xhtml",
) -> Dict[str, str]:
    """
    Render Markdown text to HTML using the Python-Markdown library.

    Args:
        text: The Markdown source text to convert.
        extensions: Optional list of extension names to enable (e.g., ["extra", "toc"]).
        extension_configs: Optional dict of per-extension configuration.
        output_format: HTML output format (e.g., "html", "xhtml"). Passed to markdown.markdown.

    Returns:
        A dict with a single key 'html' containing the rendered HTML string.
    """
    html = markdown.markdown(
        text,
        extensions=extensions or [],
        extension_configs=extension_configs,
        output_format=output_format,
    )
    return {"html": html}


if __name__ == "__main__":
    # Offline self-test: simple local conversion, check that something is produced.
    sample = "# Title\n\nHello *world*."
    result = render_markdown(sample)
    out = result.get("html", "")
    if not out:
        raise RuntimeError("Self-test failed: markdown conversion returned empty output.")
    print(f"Self-test OK; produced {len(out)} characters of HTML.")