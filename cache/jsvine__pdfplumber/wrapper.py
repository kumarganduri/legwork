from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple, Union

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("pdfplumber-mcp")


def _load_json_or_none(s: Optional[str]) -> Optional[dict]:
    if s is None or s == "":
        return None
    try:
        v = json.loads(s)
        if not isinstance(v, dict):
            raise ValueError("Expected a JSON object")
        return v
    except Exception as e:
        raise ValueError(f"Invalid JSON: {e}")


def _validate_file_exists(path: str) -> None:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")


def _page_indices(total_pages: int, page: Optional[int]) -> List[int]:
    # Convert 1-indexed page number to 0-indexed; None means all pages.
    if page is None:
        return list(range(total_pages))
    if page < 1 or page > total_pages:
        raise ValueError(f"Page out of range: {page}; PDF has {total_pages} pages")
    return [page - 1]


def _round_any(v: Any, precision: int) -> Any:
    if isinstance(v, float):
        return round(v, precision)
    if isinstance(v, list):
        return [_round_any(x, precision) for x in v]
    if isinstance(v, tuple):
        return tuple(_round_any(x, precision) for x in v)
    if isinstance(v, dict):
        return {k: _round_any(val, precision) for k, val in v.items()}
    return v


@mcp.tool()
def get_pdf_metadata(
    path: str,
    laparams_json: Optional[str] = None,
    password: Optional[str] = None,
    unicode_norm: Optional[str] = None,
    strict_metadata: bool = False,
) -> Dict[str, Any]:
    """
    Return basic metadata and page count for a PDF.

    Arguments:
    - path: Path to a local PDF file.
    - laparams_json: Optional JSON string for pdfminer.six layout analysis parameters.
    - password: Optional password for encrypted PDFs.
    - unicode_norm: Optional Unicode normalization form ("NFC","NFD","NFKC","NFKD").
    - strict_metadata: If True, raise if invalid metadata is encountered.

    Returns: { "metadata": {..}, "page_count": N }
    """
    _validate_file_exists(path)
    laparams = _load_json_or_none(laparams_json)

    import pdfplumber

    with pdfplumber.open(
        path,
        laparams=laparams,
        password=password,
        unicode_norm=unicode_norm,
        strict_metadata=strict_metadata,
    ) as pdf:
        return {"metadata": pdf.metadata or {}, "page_count": len(pdf.pages)}


@mcp.tool()
def extract_text(
    path: str,
    page: Optional[int] = None,
    layout: bool = False,
    x_tolerance: float = 3.0,
    y_tolerance: float = 3.0,
    x_tolerance_ratio: Optional[float] = None,
    laparams_json: Optional[str] = None,
    password: Optional[str] = None,
    unicode_norm: Optional[str] = None,
    strict_metadata: bool = False,
) -> Dict[str, Any]:
    """
    Extract text from a PDF (all pages or a single 1-indexed page).

    Arguments:
    - path: Path to a local PDF.
    - page: If provided, extract only this 1-indexed page; otherwise all pages.
    - layout: If True, attempt to preserve layout (experimental in pdfplumber).
    - x_tolerance, y_tolerance, x_tolerance_ratio: Passed to Page.extract_text(...).
    - laparams_json: Optional JSON of pdfminer.six LAParams to pass to pdfplumber.open().
    - password, unicode_norm, strict_metadata: Passed to pdfplumber.open().

    Returns: { "pages": [ { "page_number": int, "text": str|null } ] }
    """
    _validate_file_exists(path)
    laparams = _load_json_or_none(laparams_json)
    import pdfplumber

    with pdfplumber.open(
        path,
        laparams=laparams,
        password=password,
        unicode_norm=unicode_norm,
        strict_metadata=strict_metadata,
    ) as pdf:
        idxs = _page_indices(len(pdf.pages), page)
        results = []
        for i in idxs:
            p = pdf.pages[i]
            txt = p.extract_text(
                x_tolerance=x_tolerance,
                x_tolerance_ratio=x_tolerance_ratio,
                y_tolerance=y_tolerance,
                layout=layout,
            )
            results.append({"page_number": p.page_number, "text": txt})
        return {"pages": results}


@mcp.tool()
def extract_tables(
    path: str,
    page: Optional[int] = None,
    table_settings_json: Optional[str] = None,
    laparams_json: Optional[str] = None,
    password: Optional[str] = None,
    unicode_norm: Optional[str] = None,
    strict_metadata: bool = False,
) -> Dict[str, Any]:
    """
    Extract tables from a PDF using pdfplumber's table extraction.

    Arguments:
    - path: Path to a local PDF.
    - page: If provided, extract only this 1-indexed page; otherwise all pages.
    - table_settings_json: Optional JSON for table settings (see README "Table-extraction settings").
    - laparams_json: Optional JSON of pdfminer.six LAParams to pass to pdfplumber.open().
    - password, unicode_norm, strict_metadata: Passed to pdfplumber.open().

    Returns:
    {
      "pages": [
        {
          "page_number": int,
          "tables": [
            [ [cell_str_or_null, ...], ... ]  # rows
          ]
        }
      ]
    }
    """
    _validate_file_exists(path)
    laparams = _load_json_or_none(laparams_json)
    table_settings = _load_json_or_none(table_settings_json)
    import pdfplumber

    with pdfplumber.open(
        path,
        laparams=laparams,
        password=password,
        unicode_norm=unicode_norm,
        strict_metadata=strict_metadata,
    ) as pdf:
        idxs = _page_indices(len(pdf.pages), page)
        out_pages = []
        for i in idxs:
            p = pdf.pages[i]
            tables = p.extract_tables(table_settings=table_settings or {})
            out_pages.append({"page_number": p.page_number, "tables": tables})
        return {"pages": out_pages}


@mcp.tool()
def get_page_objects(
    path: str,
    page: int,
    types: Optional[List[str]] = None,
    precision: Optional[int] = None,
    laparams_json: Optional[str] = None,
    password: Optional[str] = None,
    unicode_norm: Optional[str] = None,
    strict_metadata: bool = False,
) -> Dict[str, Any]:
    """
    Return low-level objects for a single page: chars, lines, rects, curves, images, annots, hyperlinks.

    Arguments:
    - path: Path to a local PDF.
    - page: 1-indexed page number.
    - types: Optional subset of object types to include. Allowed values (singular): "char", "line", "rect", "curve", "image", "annot", "hyperlink".
             If omitted, includes all.
    - precision: If provided, round floating-point numbers to this many decimal places.
    - laparams_json: Optional JSON of pdfminer.six LAParams to pass to pdfplumber.open().
    - password, unicode_norm, strict_metadata: Passed to pdfplumber.open().

    Returns:
    {
      "page_number": int,
      "objects": {
        "chars": [...],
        "lines": [...],
        "rects": [...],
        "curves": [...],
        "images": [...],
        "annots": [...],
        "hyperlinks": [...]
      }
    }
    """
    _validate_file_exists(path)
    laparams = _load_json_or_none(laparams_json)
    include_types = None
    if types:
        include_types = {t.strip().lower() for t in types}

    import pdfplumber

    with pdfplumber.open(
        path,
        laparams=laparams,
        password=password,
        unicode_norm=unicode_norm,
        strict_metadata=strict_metadata,
    ) as pdf:
        idxs = _page_indices(len(pdf.pages), page)
        i = idxs[0]
        p = pdf.pages[i]

        mapping = {
            "char": "chars",
            "line": "lines",
            "rect": "rects",
            "curve": "curves",
            "image": "images",
            "annot": "annots",
            "hyperlink": "hyperlinks",
        }

        objects: Dict[str, Any] = {}
        for singular, attr in mapping.items():
            if include_types is not None and singular not in include_types:
                continue
            # Not all PDFs expose all attributes; use getattr with default empty list.
            val = getattr(p, attr, [])
            if precision is not None:
                val = _round_any(val, precision)
            objects[attr] = val

        result = {"page_number": p.page_number, "objects": objects}
        return result


if __name__ == "__main__":
    # Self-test: verify pdfplumber is importable and reports a non-empty version string.
    import pdfplumber

    ver = getattr(pdfplumber, "__version__", None)
    if not ver:
        raise RuntimeError("pdfplumber did not expose a version")
    print(f"pdfplumber version: {ver}")