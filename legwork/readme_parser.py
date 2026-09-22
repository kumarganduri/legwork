"""Locate and parse a cloned repo's README, with a size cap on what gets
sent to the LLM.

Design doc: docs/designs/legwork-jit-ai-runtime.md, T4 and "Decided"
section. Scope note: this module owns locating/reading/truncating the
README. Composing what actually goes into a retry prompt (README content
plus, on retry, only the immediately prior attempt's failure — never a
growing transcript) is the retry loop's job (T6, not yet built) using this
module's output; not solved here, since building that composition without
the surrounding retry loop it belongs to would be speculative.

Truncation is section-aware, not a flat byte cut (validation spike finding,
2026-09-22): a flat cut can retain the wrong part of a README entirely —
e.g. keeping a complex, service-dependent install path while cutting a
simpler, self-contained alternative mentioned later. Sections whose heading
looks like "Quick Start"/"Usage"/"Installation" are kept intact first;
everything else (badges, long feature lists, changelogs) gets cut before
those do.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

README_MAX_BYTES = 50 * 1024

# Case-insensitive; matched against a heading's text with punctuation
# stripped. Order doesn't matter — these are treated as a set.
_PRIORITY_HEADING_KEYWORDS = {
    "quick start",
    "quickstart",
    "usage",
    "getting started",
    "installation",
    "install",
    "setup",
}

_README_FILENAMES = [
    "README.md",
    "README.rst",
    "README.txt",
    "README",
    "Readme.md",
    "readme.md",
]

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)


class InsufficientReadmeError(Exception):
    """No README file exists in the repo at all."""


class ReadmeTruncatedWarning(Exception):
    """Not raised by parse_readme directly — parse_readme surfaces
    truncation via ParsedReadme.truncated instead, since it's a non-fatal
    condition the caller continues past, not a control-flow error. Defined
    here so callers (T6) that want to log/report it alongside real
    exceptions have a consistent class to construct, matching the design
    doc's Error & Rescue Registry naming."""


@dataclass(frozen=True)
class ParsedReadme:
    path: Path
    content: str  # possibly truncated
    original_bytes: int
    truncated: bool


@dataclass(frozen=True)
class _Section:
    heading_text: str | None  # None for content before the first heading
    text: str  # the full section text, including its own heading line


def find_readme(repo_root: Path) -> Path | None:
    """Case-insensitive search for a README at the repo root — the
    overwhelming GitHub convention. Does not search subdirectories; a
    README that only exists nested (e.g. docs/README.md) isn't picked up
    in v1."""
    try:
        entries = {p.name: p for p in repo_root.iterdir() if p.is_file()}
    except FileNotFoundError:
        return None
    for candidate in _README_FILENAMES:
        if candidate in entries:
            return entries[candidate]
    # Fall back to a fully case-insensitive match for anything named
    # README* the exact-name list above didn't cover.
    for name, path in entries.items():
        if name.lower().startswith("readme"):
            return path
    return None


def _normalize_heading(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def _split_sections(text: str) -> list[_Section]:
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [_Section(heading_text=None, text=text)]

    sections: list[_Section] = []
    if matches[0].start() > 0:
        sections.append(_Section(heading_text=None, text=text[: matches[0].start()]))

    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append(_Section(heading_text=match.group(2), text=text[start:end]))
    return sections


_PRIORITY_KEYWORD_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in _PRIORITY_HEADING_KEYWORDS) + r")"
)


def _is_priority(section: _Section) -> bool:
    if section.heading_text is None:
        return False
    # Word-boundary prefix match, not exact equality: real-world headings
    # are rarely a bare keyword ("Install") — they're "Install (agent
    # skill)", "Installing from source", "Quick Start Guide". An
    # exact-match check against a README this shape
    # (guillaumemeyer/watermarks-remover, the real oversized README from
    # the validation spike) matched ZERO sections, including "Install
    # (agent skill)" — silently defeating the entire section-aware
    # truncation feature on the exact file it exists to fix. Found by
    # smoke-testing against that real file, not a synthetic one.
    # \b prefix (not a full-word match) so "install" also catches
    # "installing"/"installation", but a word boundary before it so
    # "uninstall" (no boundary before "install" mid-word) doesn't false-hit.
    normalized = _normalize_heading(section.heading_text)
    return bool(_PRIORITY_KEYWORD_RE.search(normalized))


def _truncate_section_aware(text: str, max_bytes: int) -> str:
    sections = _split_sections(text)
    # Priority sections claim the byte budget first (in their original
    # relative order among themselves); everything else fills what's left.
    # Track kept text by ORIGINAL INDEX, not by matching text content —
    # a partially-truncated section's text won't equal anything in
    # `sections`, so content-based matching silently loses it.
    priority_indices = [i for i, s in enumerate(sections) if _is_priority(s)]
    rest_indices = [i for i, s in enumerate(sections) if not _is_priority(s)]

    kept_by_index: dict[int, str] = {}
    budget = max_bytes

    for i in priority_indices:
        if budget <= 0:
            break
        encoded = sections[i].text.encode("utf-8")
        if len(encoded) <= budget:
            kept_by_index[i] = sections[i].text
            budget -= len(encoded)
        else:
            kept_by_index[i] = encoded[:budget].decode("utf-8", errors="ignore")
            budget = 0

    for i in rest_indices:
        if budget <= 0:
            break
        encoded = sections[i].text.encode("utf-8")
        if len(encoded) <= budget:
            kept_by_index[i] = sections[i].text
            budget -= len(encoded)
        else:
            kept_by_index[i] = encoded[:budget].decode("utf-8", errors="ignore")
            budget = 0

    # Re-assemble in original document order — priority sections just got
    # first claim on the byte budget, they don't physically move.
    return "".join(kept_by_index[i] for i in sorted(kept_by_index))


def parse_readme(repo_root: Path) -> ParsedReadme:
    """Locate, read, and (if needed) section-aware-truncate the repo's
    README. Raises InsufficientReadmeError if no README file exists at
    all — a repo with no README isn't a truncation case, it's a
    can't-even-start case."""
    path = find_readme(repo_root)
    if path is None:
        raise InsufficientReadmeError(f"No README found at the root of {repo_root}")

    raw = path.read_text(encoding="utf-8", errors="replace")
    original_bytes = len(raw.encode("utf-8"))

    if original_bytes <= README_MAX_BYTES:
        return ParsedReadme(path=path, content=raw, original_bytes=original_bytes, truncated=False)

    truncated_content = _truncate_section_aware(raw, README_MAX_BYTES)
    return ParsedReadme(path=path, content=truncated_content, original_bytes=original_bytes, truncated=True)
