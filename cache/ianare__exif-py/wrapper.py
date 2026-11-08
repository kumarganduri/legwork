import base64
import json
import os
import subprocess
from typing import Optional, Dict, Any

import exifread
from mcp.server.fastmcp import FastMCP, Image

mcp = FastMCP("exifread")


def _ensure_file(path: str) -> str:
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")
    return path


def _tags_to_jsonable(tags: Dict[str, Any], include_thumbnail_bytes: bool) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in tags.items():
        # Thumbnail bytes (or any bytes) need special handling to keep JSON-serializable
        if isinstance(v, (bytes, bytearray)):
            if include_thumbnail_bytes:
                out[k] = {
                    "encoding": "base64",
                    "data": base64.b64encode(v).decode("ascii"),
                    "size": len(v),
                }
            else:
                out[k] = {
                    "size": len(v),
                    "note": "binary data omitted",
                }
        else:
            # If not builtin types (IfdTag, etc.), convert to string
            try:
                json.dumps(v)
                out[k] = v
            except TypeError:
                out[k] = str(v)
    return out


@mcp.tool()
def read_exif(
    file_path: str,
    details: bool = False,
    builtin_types: bool = True,
    extract_thumbnail: bool = False,
    stop_tag: Optional[str] = None,
    strict: bool = False,
    include_thumbnail_bytes: bool = False,
) -> Dict[str, Any]:
    """
    Extract EXIF metadata from an image file.

    - file_path: Path to the image.
    - details: Process makernotes (slower) if True. For faster processing, set False.
    - builtin_types: Return built-in Python types (JSON-friendly) if True; else stringify non-JSON values.
    - extract_thumbnail: If True, also parse any embedded thumbnail into the tags dict.
    - stop_tag: Stop after encountering this tag name (without IFD prefix), e.g., "DateTimeOriginal".
    - strict: If True, raise on invalid tags.
    - include_thumbnail_bytes: If True and thumbnail is present, include base64-encoded bytes in output.
    """
    path = _ensure_file(file_path)
    with open(path, "rb") as fh:
        tags = exifread.process_file(
            fh,
            details=details,
            stop_tag=stop_tag,
            strict=strict,
            extract_thumbnail=extract_thumbnail,
            builtin_types=builtin_types,
        )

    result = _tags_to_jsonable(tags, include_thumbnail_bytes=include_thumbnail_bytes)
    return {
        "file": os.path.abspath(path),
        "count": len(result),
        "tags": result,
    }


@mcp.tool()
def extract_thumbnail(
    file_path: str,
    output_dir: Optional[str] = None,
    output_basename: Optional[str] = None,
) -> Image:
    """
    Extract the embedded thumbnail (if any) from an image and save it to a file.

    - file_path: Path to the image file.
    - output_dir: Directory to write the thumbnail; defaults to ./thumbnails.
    - output_basename: Base name (without extension) for the output file; defaults to image base name + '_thumb'.

    Returns an Image that references the written thumbnail path.
    """
    path = _ensure_file(file_path)
    with open(path, "rb") as fh:
        tags = exifread.process_file(
            fh,
            details=False,
            extract_thumbnail=True,
            builtin_types=True,
        )

    thumb_bytes: Optional[bytes] = None
    ext = None
    if "JPEGThumbnail" in tags and isinstance(tags["JPEGThumbnail"], (bytes, bytearray)):
        thumb_bytes = bytes(tags["JPEGThumbnail"])
        ext = ".jpg"
    elif "TIFFThumbnail" in tags and isinstance(tags["TIFFThumbnail"], (bytes, bytearray)):
        thumb_bytes = bytes(tags["TIFFThumbnail"])
        ext = ".tiff"

    if not thumb_bytes:
        raise ValueError("No embedded thumbnail found in the provided image.")

    if output_dir is None:
        output_dir = os.path.join(os.getcwd(), "thumbnails")
    os.makedirs(output_dir, exist_ok=True)

    if output_basename is None:
        base = os.path.splitext(os.path.basename(path))[0]
        output_basename = f"{base}_thumb"

    out_path = os.path.abspath(os.path.join(output_dir, output_basename + ext))
    with open(out_path, "wb") as f:
        f.write(thumb_bytes)

    # Determine format hint for clients
    fmt = "jpeg" if ext.lower() in (".jpg", ".jpeg") else "tiff"
    return Image(path=out_path, format=fmt)


@mcp.tool()
def cli_help() -> str:
    """
    Return the command-line help text of EXIF.py.
    """
    # Prefer the documented CLI name "EXIF.py -h"
    try:
        proc = subprocess.run(["EXIF.py", "-h"], capture_output=True, text=True, check=False)
        text = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode == 0 and text.strip():
            return text
    except FileNotFoundError:
        pass

    # Fallback: try python -m exifread -h
    proc = subprocess.run(
        ["python", "-m", "exifread", "-h"], capture_output=True, text=True, check=False
    )
    text = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0 or not text.strip():
        raise RuntimeError("Failed to obtain EXIF.py help text.")
    return text


if __name__ == "__main__":
    # Self-test: invoke the simplest documented command that works offline: EXIF.py -h
    help_text = cli_help()
    if not help_text or not help_text.strip():
        raise SystemExit("Self-test failed: empty help text from EXIF.py -h")
    print("Self-test OK: EXIF.py help text retrieved.")