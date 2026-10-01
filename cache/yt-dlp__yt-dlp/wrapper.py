from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Union

from mcp.server.fastmcp import FastMCP

# MCP server instance
mcp = FastMCP("yt-dlp")


def _ensure_installed():
    try:
        import yt_dlp  # noqa: F401
    except Exception as e:
        raise RuntimeError(
            "yt-dlp is not installed. Please run: pip install 'yt-dlp[default]'"
        ) from e


def _get_version_str() -> str:
    import yt_dlp

    # Prefer version module if available, else fallback to package attr
    ver = getattr(getattr(yt_dlp, "version", yt_dlp), "__version__", None)
    if not ver:
        # As a last resort, try CLI --version quickly
        import subprocess, sys

        try:
            out = subprocess.check_output(
                [sys.executable, "-m", "yt_dlp", "--version"], stderr=subprocess.STDOUT
            )
            ver = out.decode().strip()
        except Exception:
            ver = ""
    return ver


@mcp.tool()
def yt_dlp_version() -> str:
    """
    Get yt-dlp version string.
    """
    _ensure_installed()
    ver = _get_version_str()
    if not ver:
        raise RuntimeError("Could not determine yt-dlp version")
    return ver


@mcp.tool()
def yt_dlp_list_extractors() -> List[str]:
    """
    List all supported extractor names.
    """
    _ensure_installed()
    from yt_dlp.extractor import gen_extractor_classes

    names: List[str] = []
    for ie in gen_extractor_classes():
        name = getattr(ie, "IE_NAME", None)
        if not name and hasattr(ie, "ie_key"):
            try:
                name = ie.ie_key()
            except Exception:
                name = None
        if name:
            names.append(name)
    names = sorted(set(names))
    return names


class _ListLogger:
    def __init__(self) -> None:
        self.logs: List[str] = []

    # yt_dlp calls these methods on the logger
    def debug(self, msg: str) -> None:
        self.logs.append(str(msg))

    def info(self, msg: str) -> None:
        self.logs.append(str(msg))

    def warning(self, msg: str) -> None:
        self.logs.append(str(msg))

    def error(self, msg: str) -> None:
        self.logs.append(str(msg))


_SUMMARY_KEYS = ("id", "title", "uploader", "duration", "upload_date", "webpage_url", "ext", "width", "height", "filesize")


def _flatten_infos(info: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten a result that may contain entries into a flat list."""
    if not isinstance(info, dict):
        return []
    if "entries" in info and isinstance(info["entries"], list):
        flat: List[Dict[str, Any]] = []
        for e in info["entries"]:
            if isinstance(e, dict) and "entries" in e:
                flat.extend(_flatten_infos(e))
            elif isinstance(e, dict):
                flat.append(e)
        return flat
    return [info]


@mcp.tool()
def yt_dlp_download(
    urls: List[str],
    output_dir: Optional[str] = None,
    output_template: Optional[str] = None,
    format: Optional[str] = None,
    simulate: bool = False,
    flat_playlist: bool = False,
    restrict_filenames: bool = False,
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    Download media or extract info using yt-dlp.

    - urls: One or more URLs to process
    - output_dir: Directory to place outputs (default: current directory)
    - output_template: Custom output template (default: "%(title)s [%(id)s].%(ext)s")
    - format: Optional format selector (e.g., "bestvideo+bestaudio/best")
    - simulate: If True, do not download; only extract info and compute filenames
    - flat_playlist: If True, do not resolve playlist entries fully
    - restrict_filenames: If True, restrict filenames to ASCII and avoid special chars
    - verbose: If True, emit verbose logs
    """
    _ensure_installed()
    import yt_dlp

    logger = _ListLogger()

    if not urls:
        raise ValueError("No URLs provided")

    if output_dir is None:
        output_dir = os.getcwd()

    if output_template is None:
        output_template = "%(title)s [%(id)s].%(ext)s"

    # Build options for yt_dlp
    ydl_opts: Dict[str, Any] = {
        "quiet": not verbose,
        "noprogress": True,
        "logger": logger,
        "restrictfilenames": restrict_filenames,
        "simulate": simulate,
        "extract_flat": bool(flat_playlist),
        # Ensure deterministic output location
        "outtmpl": os.path.join(output_dir, output_template),
    }

    if format:
        ydl_opts["format"] = format

    results: List[Dict[str, Any]] = []
    filenames: List[str] = []
    errors: List[str] = []

    def _prepare_filename_safe(ydl: yt_dlp.YoutubeDL, info: Dict[str, Any]) -> Optional[str]:
        try:
            return ydl.prepare_filename(info)
        except Exception:
            return None

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            for url in urls:
                try:
                    info = ydl.extract_info(url, download=not simulate)
                except Exception as e:
                    errors.append(str(e))
                    continue
                flat_infos = _flatten_infos(info)
                # The full info dict lists every format (hundreds of entries):
                # keep what a person or model needs.
                results.extend({k: fi.get(k) for k in _SUMMARY_KEYS if fi.get(k) is not None} for fi in flat_infos)
                for fi in flat_infos:
                    fn = _prepare_filename_safe(ydl, fi)
                    if fn:
                        filenames.append(os.path.abspath(fn))
    except Exception as e:
        errors.append(str(e))

    return {
        "version": _get_version_str(),
        "logs": logger.logs,
        "entries": results,
        "filenames": filenames,
        "errors": errors,
    }


if __name__ == "__main__":
    # Self-test: version check (offline, fast)
    _ensure_installed()
    v = yt_dlp_version()
    if not v:
        raise SystemExit("Self-test failed: empty version")
    print(f"yt-dlp version: {v}")