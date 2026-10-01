import io
import os
from typing import Optional, Dict, Any

from mcp.server.fastmcp import FastMCP

# MCP server instance
mcp = FastMCP("xhtml2pdf-tools")

@mcp.tool()
def html_to_pdf(html: str, output_pdf_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Convert an HTML string to a PDF file.

    Args:
        html: The HTML content to convert.
        output_pdf_path: Optional path for the output PDF. If not provided, a file will be created under ./outputs/.

    Returns:
        A dict with:
            - pdf_path: absolute path to the generated PDF
            - size_bytes: file size in bytes
            - errors: number of conversion errors reported by xhtml2pdf (0 indicates none)
    """
    if not isinstance(html, str) or not html.strip():
        raise ValueError("html must be a non-empty string")

    out_dir = os.path.abspath("./outputs")
    os.makedirs(out_dir, exist_ok=True)
    if output_pdf_path:
        out_path = os.path.abspath(output_pdf_path)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    else:
        out_path = os.path.join(out_dir, "output.pdf")

    from xhtml2pdf import pisa  # import here to keep import-time light

    # Write directly to file to avoid keeping large PDFs in memory
    with open(out_path, "wb") as f:
        doc = pisa.CreatePDF(src=html, dest=f, encoding="utf-8")

    if not os.path.exists(out_path):
        raise RuntimeError("PDF generation did not produce an output file")

    size = os.path.getsize(out_path)
    return {"pdf_path": os.path.abspath(out_path), "size_bytes": size, "errors": int(getattr(doc, "err", 0))}


@mcp.tool()
def html_file_to_pdf(html_file_path: str, output_pdf_path: Optional[str] = None, base_url: Optional[str] = None) -> Dict[str, Any]:
    """
    Convert a local HTML file to a PDF file.

    Args:
        html_file_path: Path to a local .html/.xhtml file.
        output_pdf_path: Optional path for the output PDF. If not provided, a file will be created under ./outputs/.
        base_url: Optional base URL or directory for resolving relative links (defaults to the input file's directory).

    Returns:
        A dict with:
            - pdf_path: absolute path to the generated PDF
            - size_bytes: file size in bytes
            - errors: number of conversion errors reported by xhtml2pdf (0 indicates none)
    """
    html_file_path = os.path.abspath(html_file_path)
    if not os.path.isfile(html_file_path):
        raise FileNotFoundError(f"HTML file not found: {html_file_path}")

    # Determine base URL for resource resolution
    if base_url is None:
        base_url = os.path.dirname(html_file_path) or "."

    out_dir = os.path.abspath("./outputs")
    os.makedirs(out_dir, exist_ok=True)
    if output_pdf_path:
        out_path = os.path.abspath(output_pdf_path)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    else:
        stem = os.path.splitext(os.path.basename(html_file_path))[0] or "output"
        out_path = os.path.join(out_dir, f"{stem}.pdf")

    from xhtml2pdf import pisa  # import inside function
    with open(html_file_path, "r", encoding="utf-8") as f_in:
        html = f_in.read()

    with open(out_path, "wb") as f_out:
        # base_url helps resolve relative paths to images, css, etc.
        doc = pisa.CreatePDF(src=html, dest=f_out, encoding="utf-8", link_callback=None, default_css=None, base_url=base_url)

    if not os.path.exists(out_path):
        raise RuntimeError("PDF generation did not produce an output file")

    size = os.path.getsize(out_path)
    return {"pdf_path": os.path.abspath(out_path), "size_bytes": size, "errors": int(getattr(doc, "err", 0))}


if __name__ == "__main__":
    # Self-test: generate a tiny PDF from a simple HTML string and verify it produced a non-empty file.
    test_html = """
    <html>
      <head><title>Self Test</title></head>
      <body>
        <h1>Hello, xhtml2pdf</h1>
        <p>This is a quick offline self-test.</p>
      </body>
    </html>
    """.strip()

    result = html_to_pdf(test_html, "./outputs/selftest.pdf")
    pdf_path = result.get("pdf_path")
    size = result.get("size_bytes", 0)

    if not pdf_path or not os.path.isfile(pdf_path) or size <= 0:
        raise SystemExit("Self-test failed: PDF not created or empty.")
    print(f"Self-test OK: {pdf_path} ({size} bytes)")