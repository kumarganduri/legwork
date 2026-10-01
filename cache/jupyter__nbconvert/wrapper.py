import os
import json
from typing import Optional, List, Dict, Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("nbconvert_mcp")

def _ensure_text(s: Any) -> bytes:
    if isinstance(s, bytes):
        return s
    elif isinstance(s, str):
        return s.encode("utf-8")
    else:
        return str(s).encode("utf-8")

def _safe_file_extension(exporter, resources) -> str:
    # Prefer exporter.file_extension if available, otherwise resources hint, fallback to .txt
    ext = getattr(exporter, "file_extension", None) or resources.get("output_extension") or ".txt"
    if not isinstance(ext, str):
        ext = ".txt"
    if not ext.startswith("."):
        ext = "." + ext
    return ext

def _derive_output_path(input_path: str, exporter, resources, output_path: Optional[str]) -> str:
    if output_path:
        out_dir = os.path.dirname(output_path)
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        return os.path.abspath(output_path)
    base, _ = os.path.splitext(os.path.basename(input_path))
    ext = _safe_file_extension(exporter, resources)
    # Input folders are read-only in Legwork's sandbox: write next to the tool.
    out_path = os.path.join(os.getcwd(), base + ext)
    return os.path.abspath(out_path)

def _clear_outputs_inplace(nbnode: Any) -> None:
    # Remove execution outputs to produce a lighter static document if requested
    for cell in nbnode.get("cells", []):
        if cell.get("cell_type") == "code":
            cell["outputs"] = []
            cell["execution_count"] = None

@mcp.tool()
def list_exporters() -> List[str]:
    """
    List available exporter short names provided by nbconvert.
    """
    try:
        from nbconvert.exporters import get_export_names
        names = sorted(get_export_names())
        if names:
            return names
    except Exception:
        pass
    # Fallback to a common subset that typically works offline
    return ["html", "markdown", "script", "rst", "latex", "notebook"]

@mcp.tool()
def convert_notebook(
    input_path: str,
    to: str = "html",
    output_path: Optional[str] = None,
    template_name: Optional[str] = None,
    exclude_outputs: bool = False
) -> Dict[str, Any]:
    """
    Convert a Jupyter notebook (.ipynb) to a static format using nbconvert.

    - input_path: path to the .ipynb file
    - to: export format (e.g., "html", "markdown", "script", "rst", "latex", "notebook")
    - output_path: optional explicit output file path; otherwise <input name>.<ext> in the tool's own folder (input folders are read-only)
    - template_name: optional nbconvert template to use
    - exclude_outputs: if True, strip code cell outputs before exporting
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input notebook not found: {input_path}")

    # Import lazily to ensure fast module import and to keep self-test simple
    import nbformat
    from nbconvert.exporters import get_exporter

    # Load notebook
    with open(input_path, "r", encoding="utf-8") as f:
        nbnode = nbformat.read(f, as_version=4)

    if exclude_outputs:
        _clear_outputs_inplace(nbnode)

    # Prepare exporter
    try:
        exporter_cls = get_exporter(to)
    except Exception as e:
        raise ValueError(f"Unknown or unavailable exporter '{to}': {e}") from e

    # Instantiate exporter, being tolerant to API differences around template_name
    try:
        exporter = exporter_cls(template_name=template_name) if template_name else exporter_cls()
    except TypeError:
        exporter = exporter_cls()
        if template_name and hasattr(exporter, "template_name"):
            try:
                exporter.template_name = template_name
            except Exception:
                pass

    # Export
    try:
        body, resources = exporter.from_notebook_node(nbnode)
    except Exception as e:
        raise RuntimeError(
            f"Export to '{to}' failed. Some formats may require external tools (e.g., TeX or pandoc). "
            f"Original error: {e}"
        ) from e

    # Determine output file path and write
    resources = resources or {}
    out_path = _derive_output_path(input_path, exporter, resources, output_path)
    data = _ensure_text(body)

    write_mode = "wb"  # write bytes to be safe for any exporter
    with open(out_path, write_mode) as f:
        f.write(data)

    return {
        "output_path": os.path.abspath(out_path),
        "format": to,
        "bytes_written": len(data),
    }

if __name__ == "__main__":
    # Self-test: create a minimal notebook, convert to HTML offline, and verify output is non-empty.
    import tempfile
    import nbformat
    from nbformat.v4 import new_notebook, new_markdown_cell, new_code_cell

    tmp_nb = new_notebook(cells=[
        new_markdown_cell("Hello from nbconvert MCP self-test"),
        new_code_cell("x = 2 + 2\nx")
    ])

    with tempfile.TemporaryDirectory() as td:
        nb_path = os.path.join(td, "test.ipynb")
        with open(nb_path, "w", encoding="utf-8") as f:
            nbformat.write(tmp_nb, f)

        result = convert_notebook(input_path=nb_path, to="html")
        out_path = result.get("output_path")
        if not out_path or not os.path.exists(out_path):
            raise RuntimeError("Self-test failed: output file was not created.")

        with open(out_path, "rb") as f:
            content = f.read()
        if not content:
            raise RuntimeError("Self-test failed: output content is empty.")

        # Simple sanity check that our marker text is present
        text = content.decode("utf-8", errors="ignore")
        if "Hello from nbconvert MCP self-test" not in text:
            raise RuntimeError("Self-test failed: expected content not found in HTML output.")

        # Also verify we can enumerate exporters (non-empty list)
        names = list_exporters()
        if not names:
            raise RuntimeError("Self-test failed: no exporters reported.")

        print(json.dumps({
            "status": "ok",
            "exporters": names[:5],
            "output_path": out_path,
            "bytes": len(content),
        }))