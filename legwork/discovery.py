"""Find candidate repos for a need, with the facts needed to choose one.

Used by `legwork find "..."` and by the hub's find_tools, where Claude (or
another MCP client's model) picks. Nothing here installs anything.

Two GitHub searches are merged, because neither alone is enough: sorting
by stars surfaces the established library (pdfplumber for "extract tables
pdf") that relevance ranking misses, and relevance ranking, which also reads
READMEs, catches other wordings and on-topic repos too new to have many
stars. Each candidate is annotated with what
matters for trusting it: stars, license, last update, age (the malware in
our trials was three days old with ~700 stars), whether the Legwork cache
already has it, and whether it already ships its own MCP server.

Repo descriptions are written by strangers: they're shortened and labelled
as untrusted wherever they're shown to a model.
"""

from __future__ import annotations

import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone

from legwork import cache_reader, llm_client
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
# GitHub search needs every word to match, with no stemming: "extract tables
# from pdf" misses pdfplumber over the word "from", and "read pdf tables"
# returned a hand-detection repo (OpenClaw test, 2026-10-01). Filler words a
# model adds go before searching.
# Models also write web-search queries ("GitHub open-source tool extract tables
# from PDF best maintained stars license", gpt-5.4-mini in OpenClaw).
_FILLER = frozenset(
    "a an and any app application best can cli command create do for from generate get github good i in "
    "into library make me need offline something thing turn want help file files find search look "
    "license line maintained my of on open open-source or package popular program read repo "
    "repository some source stars that the this to tool tools use using which with "
    # "Pull the tables out of report.pdf" (README wording, fresh QA 2026-10-03)
    "pull out please just all via".split()
)
_FILE_EXT_RE = re.compile(
    r"^[\w~-]+\.(pdf|docx?|xlsx?|pptx?|csv|tsv|json|ya?ml|xml|html?|md|txt|ics|ipynb|svg|png|jpe?g|gif|webp|heic|tiff?"
    r"|m4a|mp3|wav|flac|ogg|opus|aac|mp4|mov|mkv|webm|avi|zip|epub|sqlite|db)[.,!?]?$"
)
MAX_TERMS = 4  # every word must match, so long queries find little or nothing
MIN_RESULTS = 3  # fewer than this and the last word is dropped, down to two words
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

    def days_since(self, iso: str) -> int | None:
        try:
            return (datetime.now(timezone.utc) - datetime.fromisoformat(iso.replace("Z", "+00:00"))).days
        except (ValueError, TypeError, AttributeError):
            return None  # missing or malformed date

    @property
    def is_new(self) -> bool:
        age = self.days_since(self.created_at)
        return age is not None and age < NEW_REPO_DAYS


def _get_json(url: str) -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "legwork"}
    token = llm_client.github_token()  # optional: raises GitHub's search limit
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise DiscoveryError(
                "GitHub's search limit was reached: without a token GitHub allows 10 searches a minute, "
                "and each find uses 2 or 3. Wait a minute, or add GITHUB_TOKEN=<a GitHub token with no "
                "scopes> to ~/.legwork.env for 30 a minute."
            ) from exc
        raise DiscoveryError(f"GitHub search failed (HTTP {exc.code})") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise DiscoveryError(f"couldn't reach GitHub search: {exc}") from exc


def _search(query: str, sort: str | None, in_readme: bool = False) -> list[dict]:
    where = "in:name,description,topics,readme " if in_readme else ""
    params = {"q": f"{query} {where}fork:false archived:false stars:>={MIN_STARS}", "per_page": "10"}
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


def _annotate(candidate: Candidate, cached: set[str] | None = None) -> Candidate:
    owner, repo = candidate.slug.split("/", 1)
    if cached:
        candidate.in_cache = candidate.slug.lower() in cached
    else:
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


def search_terms(query: str) -> str:
    """The words worth sending to GitHub: lowercased, punctuation and filler
    removed. Falls back to all the words if nothing else is left."""
    # Tokens with a colon are search qualifiers (site:, stars:, user:): dropped,
    # so a query can't undo the star floor or narrow results to one account.
    raw = [t for t in query.lower().split() if ":" not in t]
    # A path or file name says what kind of file, not what to search for:
    # "~/Downloads/report.pdf" is "pdf" (fresh QA, 2026-10-03).
    raw = [_FILE_EXT_RE.sub(r"\1", t.rsplit("/", 1)[-1]) for t in raw]
    words = [w for w in re.findall(r"[\w.+#-]+", " ".join(raw)) if w.strip(".-")]
    kept = [w for w in words if w not in _FILLER]
    return " ".join((kept or words)[:MAX_TERMS])


@dataclass
class Found:
    terms: str  # what was actually searched, after cleanup and narrowing
    candidates: list[Candidate]
    note: str = ""  # shown above the results, e.g. when GitHub search was unavailable


def find(query: str, limit: int = MAX_CANDIDATES) -> Found:
    """Up to `limit` candidates for `query` (a few keywords), best first."""
    terms = search_terms(query).split()
    if not terms:
        raise DiscoveryError("describe what you need in a few words, e.g. 'extract tables pdf'")
    # Cached tools that fit the need go first: they install in about a minute
    # with no API key and were reviewed. GitHub's ranking often misses them:
    # "speech to text transcribe" didn't list faster-whisper, so a client with
    # no key picked openai/whisper and gave up (QA, Claude Code, 2026-10-02).
    # They're matched locally, so they survive GitHub's search limit and
    # outages too (fresh QA, 2026-10-03).
    index = cache_reader.fetch_index()
    cached_slugs = {e["repo"].lower() for e in index}
    lifted = cache_matches(search_terms(query).split(), index)
    try:
        by_stars = _search(" ".join(terms), sort="stars")
        while len(by_stars) < MIN_RESULTS and len(terms) > 2:
            terms = terms[:-1]
            by_stars = _search(" ".join(terms), sort="stars")
        # READMEs only for relevance: they use more word forms ("Table extraction"
        # finds pdfplumber), but sorted by stars they surface awesome-lists.
        by_relevance = _search(" ".join(terms), sort=None, in_readme=True)
    except DiscoveryError as exc:
        if not lifted:
            raise
        return Found(" ".join(terms), lifted, note=f"GitHub search is unavailable ({exc}); showing matching tools from the Legwork cache only.")
    merged: dict[str, dict] = {}
    for item in by_stars[:limit] + by_relevance[:4]:
        merged.setdefault(item["full_name"].lower(), item)
    lifted_slugs = {c.slug.lower() for c in lifted}
    others = sorted((_candidate(i) for i in merged.values() if i["full_name"].lower() not in lifted_slugs), key=lambda c: -c.stars)
    others = others[: max(limit - len(lifted), 3)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        others = list(pool.map(lambda c: _annotate(c, cached_slugs), others))
    return Found(" ".join(terms), lifted + others)


MAX_CACHE_MATCHES = 3


def _stem(term: str) -> str:
    return term if len(term) <= 4 else term[: max(4, len(term) - 3)]


def cache_matches(terms: list[str], index: list[dict], limit: int = MAX_CACHE_MATCHES) -> list[Candidate]:
    """Cached repos whose name, description or topics fit the search terms.
    A term matches a word that starts with its stem, so "transcribe" finds
    "transcription" (but not "transform") and "tables" finds "table"."""
    if not terms:
        return []
    scored = []
    # Where a word matches says how much it means: the repo's name or what
    # Legwork's wrapper does (2), GitHub's description and topics (1). Rare
    # words count more than common ones (IDF): unweighted, "find secrets in
    # code" ranked prettier above gitleaks and "transcribe audio" ranked
    # yt-dlp first (fresh QA, 2026-10-03).
    parsed = []
    for entry in index:
        fields = (
            (2, f"{entry['repo']} {entry.get('what', '')}"),
            (1, " ".join([entry.get("about", ""), *map(str, entry.get("topics") or [])])),
        )
        parsed.append((entry, [(weight, re.findall(r"[a-z0-9]+", text.lower())) for weight, text in fields]))
    df = {t: sum(1 for _, fs in parsed if any(_word_matches(t, w) for _, ws in fs for w in ws)) for t in terms}
    idf = {t: math.log((len(parsed) + 1) / (df[t] + 1)) + 1 for t in terms}
    for entry, fields in parsed:
        weights = [max((weight for weight, ws in fields if any(_word_matches(t, w) for w in ws)), default=0) for t in terms]
        matched = sum(1 for w in weights if w)
        # Most of the query, weighted by how rare each word is, and at least one
        # word in the repo's name or Legwork's own reviewed description. One
        # common word lifted unrelated tools: "speech to text" gave shellcheck,
        # "make qr code" gave ruff (fresh QA, 2026-10-03).
        share = sum(idf[t] for w, t in zip(weights, terms) if w) / sum(idf.values())
        if share >= 0.5 and 2 in weights:
            scored.append((matched, sum(w * idf[t] for w, t in zip(weights, terms)), entry.get("stars", 0), entry))
    scored.sort(key=lambda x: (-x[0], -x[1], -x[2]))
    return [
        Candidate(
            slug=e["repo"], stars=int(e.get("stars") or 0), description=" ".join(e.get("what", "").split())[:160],
            license=e.get("license") or "none", language=e.get("language") or "-", pushed_at=e.get("pushed_at") or "",
            created_at=e.get("created_at") or "", topics=[], in_cache=True,
        )
        for _, _, _, e in scored[:limit]
    ]


def _word_matches(term: str, word: str) -> bool:
    """`word` is a form of `term`: "transcription" for "transcribe", "pdf"
    for "pdfs". The other way round only for near-whole words: "for" matched
    "forecast" and lifted gitleaks for "weather forecast" (fresh QA, 2026-10-03)."""
    return word.startswith(_stem(term)) or (term.startswith(word) and len(word) >= 4 and len(term) - len(word) <= 2)


def describe(found: Found) -> str:
    """A compact, model-readable list. Descriptions are marked untrusted."""
    header = f"Searched GitHub for: {found.terms}\n" + (f"{found.note}\n" if found.note else "") + "\n"
    candidates = found.candidates
    if not candidates:
        return header + (
            "No matching repositories found. GitHub search needs every word to match: "
            "try fewer or different keywords (e.g. 'pdf tables')."
        )
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
            + (f"updated {updated} days ago)" if updated is not None else "update date unknown)")
            + (f" [{'; '.join(flags)}]" if flags else "")
        )
        if c.description:
            lines.append(f"   description (untrusted, from the repo): {c.description}")
    return header + "\n".join(lines)
