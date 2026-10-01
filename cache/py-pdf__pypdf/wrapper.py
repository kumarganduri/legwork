import os
from typing import List, Optional, Dict

from mcp.server.fastmcp import FastMCP
from pypdf import PdfReader, PdfWriter

mcp = FastMCP("pypdf-tools")


def _abs(path: str) -> str:
    return os.path.abspath(path)


def _ensure_file(path: str) -> None:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")


@mcp.tool()
def extract_text(pdf_path: str, page: Optional[int] = None) -> str:
    """
    Extract text from a PDF.
    - If page is None, returns concatenated text from all pages.
    - If page is provided (0-based), returns text from that page.
    Returns an empty string if no extractable text is found.
    """
    _ensure_file(pdf_path)
    reader = PdfReader(pdf_path)

    def _page_text(p) -> str:
        t = p.extract_text()
        return (t or "").strip()

    if page is None:
        texts = []
        for p in reader.pages:
            t = _page_text(p)
            if t:
                texts.append(t)
        return "\n\n".join(texts)
    else:
        if page < 0 or page >= len(reader.pages):
            raise IndexError(f"page index out of range: {page} (pages: {len(reader.pages)})")
        return _page_text(reader.pages[page])


@mcp.tool()
def get_metadata(pdf_path: str) -> Dict[str, str]:
    """
    Return PDF document metadata as a simple dict of string keys/values.
    Keys are normalized without the leading '/' when present.
    """
    _ensure_file(pdf_path)
    reader = PdfReader(pdf_path)
    md = reader.metadata or {}
    out: Dict[str, str] = {}
    for k, v in md.items():
        key = str(k)
        if key.startswith("/"):
            key = key[1:]
        out[key] = "" if v is None else str(v)
    return out


@mcp.tool()
def split_pdf(pdf_path: str, output_dir: Optional[str] = None) -> List[str]:
    """
    Split a PDF into single-page PDFs.
    - output_dir: directory to write outputs (created if needed). Defaults to ./split_<basename>.
    Returns a list of absolute paths to the created PDFs.
    """
    _ensure_file(pdf_path)
    reader = PdfReader(pdf_path)
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    out_dir = output_dir or f"split_{base}"
    os.makedirs(out_dir, exist_ok=True)

    output_paths: List[str] = []
    for i, page in enumerate(reader.pages, start=1):
        writer = PdfWriter()
        writer.add_page(page)
        out_path = os.path.join(out_dir, f"{base}_page_{i}.pdf")
        with open(out_path, "wb") as f:
            writer.write(f)
        output_paths.append(_abs(out_path))
    return output_paths


@mcp.tool()
def merge_pdfs(pdf_paths: List[str], output_path: Optional[str] = None) -> str:
    """
    Merge multiple PDFs into a single PDF.
    - pdf_paths: list of input PDF file paths (in order).
    - output_path: path for merged PDF. Defaults to ./merged.pdf.
    Returns the absolute path to the merged PDF.
    """
    if not pdf_paths:
        raise ValueError("pdf_paths must not be empty")
    for p in pdf_paths:
        _ensure_file(p)

    writer = PdfWriter()
    for p in pdf_paths:
        reader = PdfReader(p)
        for page in reader.pages:
            writer.add_page(page)

    out_path = output_path or "merged.pdf"
    with open(out_path, "wb") as f:
        writer.write(f)
    return _abs(out_path)


def _create_dummy_pdf(path: str, title: Optional[str] = None, num_pages: int = 1) -> str:
    """
    Create a simple PDF with blank pages and optional title metadata.
    """
    writer = PdfWriter()
    # 8.5 x 11 inches in points (72 dpi): 612 x 792
    for _ in range(max(1, num_pages)):
        writer.add_blank_page(width=612, height=792)
    if title:
        writer.add_metadata({"/Title": title})
    with open(path, "wb") as f:
        writer.write(f)
    return _abs(path)


if __name__ == "__main__":
    # Self-test: create tiny PDFs, merge, read metadata, and split. All offline.
    a = _create_dummy_pdf("test_a.pdf", title="Test PDF A", num_pages=1)
    b = _create_dummy_pdf("test_b.pdf", title="Test PDF B", num_pages=1)

    merged = merge_pdfs([a, b])
    if not os.path.isfile(merged):
        raise RuntimeError("Merged PDF was not created")

    md = get_metadata(a)
    if not md:
        raise RuntimeError("Metadata extraction returned empty result")

    parts = split_pdf(merged)
    if not parts:
        raise RuntimeError("Split produced no output files")

    # Print minimal confirmation
    print("OK:", {
        "merged": merged,
        "metadata_keys": list(md.keys())[:3],
        "split_count": len(parts),
    })