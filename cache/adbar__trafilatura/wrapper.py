import json
import os
from typing import Any, Dict, Union

from mcp.server.fastmcp import FastMCP
from trafilatura import extract, __version__ as trafilatura_version


mcp = FastMCP("trafilatura_mcp")


def _do_extract(
    html_or_bytes: Union[str, bytes],
    output_format: str = "txt",
    with_metadata: bool = False,
) -> Union[str, Dict[str, Any]]:
    """Internal helper to run Trafilatura extract and post-process the result."""
    # Normalize output_format
    fmt = (output_format or "txt").lower()
    res = extract(html_or_bytes, output_format=fmt, with_metadata=with_metadata)
    if not res:
        raise RuntimeError("Trafilatura could not extract any content from the provided input.")
    if fmt == "json":
        # Try to return parsed JSON
        try:
            return json.loads(res)
        except Exception:
            # Fall back to returning the raw string wrapped
            return {"raw": res}
    return res


@mcp.tool()
def extract_from_html(
    html: str,
    output_format: str = "txt",
    with_metadata: bool = False,
) -> Union[str, Dict[str, Any]]:
    """
    Extract main text (and optionally metadata) from an HTML string.

    Args:
      html: A string containing the full HTML document to parse.
      output_format: One of "txt", "json", "xml", "html", "markdown". Defaults to "txt".
      with_metadata: If True and output_format="json", include metadata fields when available.

    Returns:
      - If output_format="json": a dict with extracted content (and metadata when available).
      - Otherwise: a string with the extracted content in the chosen format.

    Raises:
      RuntimeError if no content could be extracted.
    """
    if not isinstance(html, str) or not html.strip():
        raise ValueError("Parameter 'html' must be a non-empty HTML string.")
    return _do_extract(html, output_format=output_format, with_metadata=with_metadata)


@mcp.tool()
def extract_from_file(
    file_path: str,
    output_format: str = "txt",
    with_metadata: bool = False,
) -> Union[str, Dict[str, Any]]:
    """
    Extract main text (and optionally metadata) from a local HTML file.

    Args:
      file_path: Path to a local HTML file (read-only).
      output_format: One of "txt", "json", "xml", "html", "markdown". Defaults to "txt".
      with_metadata: If True and output_format="json", include metadata fields when available.

    Returns:
      - If output_format="json": a dict with extracted content (and metadata when available).
      - Otherwise: a string with the extracted content in the chosen format.

    Raises:
      FileNotFoundError if the file does not exist.
      RuntimeError if no content could be extracted.
    """
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
    with open(file_path, "rb") as f:
        data = f.read()
    return _do_extract(data, output_format=output_format, with_metadata=with_metadata)


@mcp.tool()
def version() -> str:
    """
    Return the installed Trafilatura version.
    """
    return trafilatura_version


if __name__ == "__main__":
    # Self-test: run a minimal, fully offline extraction on a small HTML snippet
    sample_html = """
    <html>
      <head><title>Sample</title></head>
      <body>
        <article>
          <h1>Example Article</h1>
          <p>Hello world. This is a short test.</p>
          <p>Second paragraph.</p>
        </article>
      </body>
    </html>
    """.strip()

    # Plain text extraction
    txt = extract_from_html(sample_html)
    if not txt or not isinstance(txt, str):
        raise SystemExit("Self-test failed: empty or invalid TXT extraction result.")

    # JSON extraction (with metadata when available)
    js = extract_from_html(sample_html, output_format="json", with_metadata=True)
    if not js:
        raise SystemExit("Self-test failed: empty JSON extraction result.")
    # Ensure we got either a dict or a non-empty string (should be dict)
    if isinstance(js, dict):
        if not js:
            raise SystemExit("Self-test failed: empty dict from JSON extraction.")
    elif isinstance(js, str):
        if not js.strip():
            raise SystemExit("Self-test failed: empty string from JSON extraction.")
    else:
        raise SystemExit("Self-test failed: unexpected JSON extraction return type.")

    ver = version()
    if not ver:
        raise SystemExit("Self-test failed: could not retrieve Trafilatura version.")

    print("Self-test completed successfully.")