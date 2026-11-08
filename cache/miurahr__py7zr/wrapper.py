import os
import sys
import json
from typing import List, Optional, Dict, Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("py7zr-mcp")

def _abs(path: str) -> str:
    return os.path.abspath(path)

@mcp.tool()
def list_7z(archive_path: str, password: Optional[str] = None) -> Dict[str, Any]:
    """
    List entries in a 7z archive.
    - archive_path: Path to .7z archive.
    - password: Optional password for encrypted archives.
    Returns:
      {"archive": <abs path>, "entries": [<names>]}
    """
    import py7zr

    archive_path = _abs(archive_path)
    if not os.path.isfile(archive_path):
        raise FileNotFoundError(f"Archive not found: {archive_path}")

    with py7zr.SevenZipFile(archive_path, mode="r", password=password) as z:
        names = z.getnames()
    return {"archive": archive_path, "entries": names}


@mcp.tool()
def extract_7z(
    archive_path: str,
    dest_dir: str,
    targets: Optional[List[str]] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """
    Extract a 7z archive.
    - archive_path: Path to .7z archive.
    - dest_dir: Destination directory (will be created if missing).
    - targets: Optional list of specific paths inside the archive to extract.
    - password: Optional password for encrypted archives.
    Returns:
      {"archive": <abs path>, "extracted_to": <abs dest>, "items": [<names_extracted>]}
    """
    import py7zr

    archive_path = _abs(archive_path)
    dest_dir = _abs(dest_dir)

    if not os.path.isfile(archive_path):
        raise FileNotFoundError(f"Archive not found: {archive_path}")

    os.makedirs(dest_dir, exist_ok=True)

    with py7zr.SevenZipFile(archive_path, mode="r", password=password) as z:
        if targets:
            # Extract only selected targets into dest_dir by temporarily changing CWD.
            old_cwd = os.getcwd()
            try:
                os.chdir(dest_dir)
                z.extract(targets=targets)
            finally:
                os.chdir(old_cwd)
            extracted_items = list(targets)
        else:
            z.extractall(path=dest_dir)
            extracted_items = z.getnames()

    return {"archive": archive_path, "extracted_to": dest_dir, "items": extracted_items}


@mcp.tool()
def create_7z(
    archive_path: str,
    source_path: str,
    arcname: Optional[str] = None,
    password: Optional[str] = None,
    filters: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Create a 7z archive from a file or directory.
    - archive_path: Output .7z file path (will be overwritten if exists).
    - source_path: Source file or directory to add.
    - arcname: Optional root name inside the archive; defaults to basename of source.
    - password: Optional password for encryption.
    - filters: Optional list of filter dicts (e.g., [{"id": py7zr.FILTER_ZSTD}] when supported).
    Returns:
      {"archive": <abs path>, "source": <abs source>, "size_bytes": <int>}
    """
    import py7zr

    archive_path = _abs(archive_path)
    source_path = _abs(source_path)
    if not os.path.exists(source_path):
        raise FileNotFoundError(f"Source not found: {source_path}")

    # Ensure parent directory exists for the archive file
    os.makedirs(os.path.dirname(archive_path) or ".", exist_ok=True)

    # Decide archive root name if not provided
    root_name = arcname if arcname is not None else os.path.basename(source_path.rstrip(os.sep)) or "."

    with py7zr.SevenZipFile(archive_path, mode="w", password=password, filters=filters) as z:
        if os.path.isdir(source_path):
            z.writeall(source_path, root_name)
        else:
            # py7zr supports adding single files via write()
            z.write(source_path, root_name)

    size = os.path.getsize(archive_path)
    return {"archive": archive_path, "source": source_path, "size_bytes": size}


if __name__ == "__main__":
    # Self-test: minimal offline functionality
    # 1) Create a small directory with a file
    import tempfile
    import py7zr

    tmp_root = tempfile.mkdtemp(prefix="py7zr_mcp_test_")
    src_dir = os.path.join(tmp_root, "src")
    os.makedirs(src_dir, exist_ok=True)
    with open(os.path.join(src_dir, "hello.txt"), "w", encoding="utf-8") as f:
        f.write("hello py7zr\n")

    # 2) Create an archive
    archive = os.path.join(tmp_root, "test.7z")
    res_create = create_7z(archive, src_dir)
    if not res_create or not os.path.isfile(res_create["archive"]):
        raise RuntimeError("create_7z failed or returned empty result")

    # 3) List archive contents
    res_list = list_7z(archive)
    if not res_list or not res_list.get("entries"):
        raise RuntimeError("list_7z failed or returned no entries")

    # 4) Extract archive
    out_dir = os.path.join(tmp_root, "out")
    res_extract = extract_7z(archive, out_dir)
    if not res_extract or not os.path.isdir(res_extract["extracted_to"]):
        raise RuntimeError("extract_7z failed or destination missing")

    print(json.dumps({
        "create": res_create,
        "list": {"entries_count": len(res_list.get("entries", []))},
        "extract": {"dest": res_extract.get("extracted_to")}
    }, indent=2))