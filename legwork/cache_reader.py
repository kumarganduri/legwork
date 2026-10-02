"""Reads the public wrapper cache: `cache/<owner>__<repo>/` in the Legwork
repo, written by `legwork contribute` and merged by PR (see cache_writer).

A cached wrapper is still never trusted blindly. The build that uses it
clones and scans the source repo as usual, scans the wrapper itself, and
installs and self-tests it in the sandbox; if any of that fails, the
build falls back to writing a fresh wrapper. What the cache saves is the
model call: repos already in it build without an API key.

LEGWORK_CACHE_URL points somewhere else — another https base URL, or a
local folder (a checkout of the cache) — and "off" disables the cache.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from legwork.repo_fetcher import RepoRef

CACHE_URL_ENV = "LEGWORK_CACHE_URL"
DEFAULT_CACHE_URL = "https://raw.githubusercontent.com/kumarganduri/legwork/main/cache"
FETCH_TIMEOUT_SECONDS = 15
_MAX_BYTES = 1024 * 1024
_REQUIRED_FIELDS = ("source_repo_url", "commit_sha", "install_command", "entrypoint", "llm_model")


class CacheUnavailableError(Exception):
    """The cache couldn't be read (offline, server error, a malformed entry).
    Never fatal: the build just writes a fresh wrapper."""


@dataclass(frozen=True)
class CachedWrapper:
    manifest: dict
    wrapper_code: str

    @property
    def install_command(self) -> str:
        return self.manifest["install_command"]

    @property
    def entrypoint(self) -> str:
        return self.manifest["entrypoint"]


def entry_name(ref: RepoRef) -> str:
    # GitHub names are case-insensitive; the cache folder is lowercase so
    # `Owner/Repo` and `owner/repo` find the same entry.
    return f"{ref.owner}__{ref.repo}".lower()


def _read(base: str, relpath: str) -> str | None:
    """The file's text, or None if it doesn't exist."""
    if base.startswith(("https://", "http://")):
        url = f"{base.rstrip('/')}/{relpath}"
        try:
            with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_SECONDS) as response:
                return response.read(_MAX_BYTES + 1)[:_MAX_BYTES].decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise CacheUnavailableError(f"the cache returned HTTP {exc.code} for {relpath}") from exc
        except (urllib.error.URLError, TimeoutError, UnicodeDecodeError) as exc:
            raise CacheUnavailableError(f"couldn't read the cache: {exc}") from exc
    path = Path(base).expanduser() / relpath
    return path.read_text() if path.is_file() else None


def fetch(ref: RepoRef) -> CachedWrapper | None:
    """The cache entry for `ref`, or None when there isn't one (or the cache
    is off). Raises CacheUnavailableError when it can't tell."""
    base = os.environ.get(CACHE_URL_ENV, DEFAULT_CACHE_URL)
    if base.strip().lower() in ("", "off"):
        return None
    name = entry_name(ref)
    raw_manifest = _read(base, f"{name}/manifest.json")
    if raw_manifest is None:
        return None
    wrapper = _read(base, f"{name}/wrapper.py")
    try:
        manifest = json.loads(raw_manifest)
    except ValueError as exc:
        raise CacheUnavailableError(f"the cached manifest for {ref.slug} isn't valid JSON") from exc
    missing = [f for f in _REQUIRED_FIELDS if not isinstance(manifest, dict) or not manifest.get(f)]
    if wrapper is None or missing:
        raise CacheUnavailableError(
            f"the cache entry for {ref.slug} is incomplete ({'wrapper.py' if wrapper is None else ', '.join(missing)})"
        )
    if manifest["source_repo_url"].lower() != f"https://github.com/{ref.slug}".lower():
        raise CacheUnavailableError(f"the cache entry {name} is for {manifest['source_repo_url']}, not {ref.slug}")
    return CachedWrapper(manifest=manifest, wrapper_code=wrapper)


def fetch_index() -> list[dict]:
    """The cache's index.json: one entry per cached repo (repo, what it
    does, license, a stars snapshot). Empty when the cache is off or out of
    reach: search still works without it, it just can't lift cached tools."""
    base = os.environ.get(CACHE_URL_ENV, DEFAULT_CACHE_URL)
    if base.strip().lower() in ("", "off"):
        return []
    try:
        raw = _read(base, "index.json")
        entries = json.loads(raw) if raw else []
    except (CacheUnavailableError, ValueError):
        return []
    return [e for e in entries if isinstance(e, dict) and isinstance(e.get("repo"), str)] if isinstance(entries, list) else []
