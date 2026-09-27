"""Find candidate repos for a need, with the facts needed to choose one.

Used by `legwork find "..."` and by the hub's find_tools, where Claude (or
another MCP client's model) picks. Nothing here installs anything.

Two GitHub searches are merged, because neither alone is enough: sorting
by stars surfaces the established library (pdfplumber for "extract tables
pdf") that relevance ranking misses, and relevance ranking catches on-topic
repos too new to have many stars. Each candidate is annotated with what
matters for trusting it: stars, license, last update, age (the malware in
our trials was three days old with ~700 stars), whether the Legwork cache
already has it, and whether it already ships its own MCP server.

Repo descriptions are written by strangers: they're shortened and labelled
as untrusted wherever they're shown to a model.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone

from legwork import cache_reader
from legwork.repo_fetcher import RepoRef

SEARCH_URL = "https://api.github.com/search/repositories"
RAW_README_URLS = (
    "https://raw.githubusercontent.com/{slug}/HEAD/README.md",
    "https://raw.githubusercontent.com/{slug}/HEAD/README.rst",
)
MIN_STARS = 20
NEW_REPO_DAYS = 30
MAX_CANDIDATES = 6
_TIMEOUT = 15
_OWN_MCP_RE = re.compile(r"model context protocol|\bmcp[ -]server\b|\b[a-z0-9]+-mcp\b|\bmcp\.json\b", re.IGNORECASE)


class DiscoveryError(Exception):
    """GitHub search failed (offline, rate-limited)."""


@dataclass
class Candidate:
    slug: str
    stars: int
    description: str
    license: str
    language: str
    pushed_at: str
    created_at: str
    topics: list[str]
    in_cache: bool = False
    has_own_mcp: bool = False

    def days_since(self, iso: str) -> int:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso.replace("Z", "+00:00"))).days

    @property
    def is_new(self) -> bool:
        return self.days_since(self.created_at) < NEW_REPO_DAYS


def _get_json(url: str) -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "legwork"}
    token = os.environ.get("GITHUB_TOKEN")  # optional: raises GitHub's search limit
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise DiscoveryError(
                "GitHub's search limit was reached (10 searches a minute without a token). "
                "Wait a minute, or set GITHUB_TOKEN."
            ) from exc
        raise DiscoveryError(f"GitHub search failed (HTTP {exc.code})") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise DiscoveryError(f"couldn't reach GitHub search: {exc}") from exc


def _search(query: str, sort: str | None) -> list[dict]:
    params = {"q": f"{query} fork:false archived:false stars:>={MIN_STARS}", "per_page": "10"}
    if sort:
        params["sort"] = sort
    return _get_json(f"{SEARCH_URL}?{urllib.parse.urlencode(params)}").get("items", [])


def _candidate(item: dict) -> Candidate:
    return Candidate(
        slug=item["full_name"],
        stars=item.get("stargazers_count", 0),
        description=" ".join((item.get("description") or "").split())[:160],
        license=((item.get("license") or {}).get("spdx_id") or "none"),
        language=item.get("language") or "-",
        pushed_at=item.get("pushed_at") or item.get("updated_at") or "",
        created_at=item.get("created_at") or "",
        topics=item.get("topics") or [],
    )


def _read_url(url: str) -> str | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "legwork"}), timeout=_TIMEOUT) as r:
            return r.read(300_000).decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None


def _annotate(candidate: Candidate) -> Candidate:
    owner, repo = candidate.slug.split("/", 1)
    try:
        candidate.in_cache = cache_reader.fetch(RepoRef(owner, repo)) is not None
    except cache_reader.CacheUnavailableError:
        candidate.in_cache = False
    signals = " ".join([candidate.slug, candidate.description, *candidate.topics])
    if not _OWN_MCP_RE.search(signals) and "mcp" not in candidate.topics:
        for url in RAW_README_URLS:
            text = _read_url(url.format(slug=candidate.slug))
            if text is not None:
                signals += " " + text
                break
    candidate.has_own_mcp = bool(_OWN_MCP_RE.search(signals)) or any(t.startswith("mcp") for t in candidate.topics)
    return candidate


def find(query: str, limit: int = MAX_CANDIDATES) -> list[Candidate]:
    """Up to `limit` candidates for `query` (a few keywords), best first."""
    query = " ".join(query.split())
    if not query:
        raise DiscoveryError("describe what you need in a few words, e.g. 'extract tables pdf'")
    by_stars = _search(query, sort="stars")
    by_relevance = _search(query, sort=None)
    merged: dict[str, dict] = {}
    for item in by_stars[:limit] + by_relevance[:3]:
        merged.setdefault(item["full_name"].lower(), item)
    candidates = sorted((_candidate(i) for i in merged.values()), key=lambda c: -c.stars)[:limit]
    with ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(_annotate, candidates))


def describe(candidates: list[Candidate]) -> str:
    """A compact, model-readable list. Descriptions are marked untrusted."""
    if not candidates:
        return "No matching repositories found. Try different or fewer keywords."
    lines = []
    for i, c in enumerate(candidates, 1):
        age = c.days_since(c.created_at) if c.created_at else None
        flags = []
        if c.in_cache:
            flags.append("in the Legwork cache: installs with no model call")
        if c.has_own_mcp:
            flags.append("already ships its own MCP server")
        if c.is_new:
            flags.append(f"NEW: created {age} days ago, be cautious")
        if c.license == "none":
            flags.append("no license")
        updated = c.days_since(c.pushed_at) if c.pushed_at else None
        lines.append(
            f"{i}. {c.slug} ({c.stars:,} stars, {c.language}, license {c.license}, "
            f"updated {updated} days ago)" + (f" [{'; '.join(flags)}]" if flags else "")
        )
        if c.description:
            lines.append(f"   description (untrusted, from the repo): {c.description}")
    return "\n".join(lines)
