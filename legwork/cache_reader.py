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
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from legwork.repo_fetcher import RepoRef

CACHE_URL_ENV = "LEGWORK_CACHE_URL"
_CACHE_URL_AT = "https://raw.githubusercontent.com/kumarganduri/legwork/{ref}/cache"
DEFAULT_CACHE_URL = _CACHE_URL_AT.format(ref="main")  # development installs only


def default_cache_url() -> str:
    """The cache as it was when this version was released (its git tag), not
    live main: a change to cache/ on main used to reach every user at once,
    with no release (pre-launch review, 2026-10-03). New cached tools ship
    with a release. Development installs without a release version use main."""
    try:
        from importlib.metadata import version

        installed = version("legwork-mcp")
    except Exception:  # noqa: BLE001 — not installed as a package
        return DEFAULT_CACHE_URL
    if not re.fullmatch(r"\d+\.\d+\.\d+", installed):
        return DEFAULT_CACHE_URL
    return _CACHE_URL_AT.format(ref=f"v{installed}")


# Entries pulled after a release. A release reads the cache at its own tag,
# which can't change, so a bad entry (a compromised upstream, a wrapper that
# misbehaves) would stay installable until everyone upgraded. revoked.json is
# read from main and can only remove entries, never add or change one. Out of
# reach, nothing is revoked: every entry passed review when it shipped.
REVOKED_FILE = "revoked.json"
_REVOKED_TTL_SECONDS = 600
_revoked_cache: tuple[float, str, frozenset[str]] | None = None


def _revoked(base: str) -> frozenset[str]:
    """Lowercase owner/repo names revoked since this release."""
    global _revoked_cache
    source = DEFAULT_CACHE_URL if base == default_cache_url() else base
    now = time.monotonic()
    if _revoked_cache and _revoked_cache[1] == source and now - _revoked_cache[0] < _REVOKED_TTL_SECONDS:
        return _revoked_cache[2]
    try:
        raw = _read(source, REVOKED_FILE)
        data = json.loads(raw) if raw else {}
    except (CacheUnavailableError, ValueError, OSError):
        data = {}
    names = frozenset(k.lower() for k in data if isinstance(k, str)) if isinstance(data, dict) else frozenset()
    _revoked_cache = (now, source, names)
    return names


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
    base = os.environ.get(CACHE_URL_ENV) or default_cache_url()
    if base.strip().lower() in ("", "off"):
        return None
    if ref.slug.lower() in _revoked(base):
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
    base = os.environ.get(CACHE_URL_ENV) or default_cache_url()
    if base.strip().lower() in ("", "off"):
        return []
    try:
        raw = _read(base, "index.json")
        entries = json.loads(raw) if raw else []
    except (CacheUnavailableError, ValueError):
        return []
    if not isinstance(entries, list):
        return []
    revoked = _revoked(base)
    return [clean for e in entries if (clean := _clean_index_entry(e)) is not None and clean["repo"].lower() not in revoked]


def _clean_index_entry(entry) -> dict | None:
    """An index entry with every field the right type, or None. One odd entry
    (a null description, stars as a string, a missing date) used to make every
    find_tools call fail (Codex and pre-launch review, 2026-10-03)."""
    if not isinstance(entry, dict) or not isinstance(entry.get("repo"), str) or entry["repo"].count("/") != 1:
        return None
    text = lambda key: entry.get(key) if isinstance(entry.get(key), str) else ""  # noqa: E731
    stars = entry.get("stars")
    return {
        "repo": entry["repo"], "what": text("what"), "about": text("about"), "license": text("license") or "none",
        "language": text("language") or "-", "created_at": text("created_at"), "pushed_at": text("pushed_at"),
        "stars": stars if isinstance(stars, int) and not isinstance(stars, bool) else 0,
        "topics": [t for t in entry.get("topics") or [] if isinstance(t, str)] if isinstance(entry.get("topics"), list) else [],
    }
