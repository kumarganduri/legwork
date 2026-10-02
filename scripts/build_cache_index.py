"""Write cache/index.json: one line per cached repo, so find_tools can
offer cached tools (no API key, about a minute to install) for a need even
when GitHub's search ranks them low. Run after adding or removing a cache
entry; tests/test_cache_index.py checks the index matches the folders.

    GITHUB_TOKEN=... uv run python scripts/build_cache_index.py

Stars, language and dates are a snapshot from the GitHub API at the time
this runs (a token avoids the 60-requests-an-hour limit).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

CACHE = Path(__file__).resolve().parents[1] / "cache"


def _github(slug: str) -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "legwork"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    with urllib.request.urlopen(urllib.request.Request(f"https://api.github.com/repos/{slug}", headers=headers), timeout=15) as r:
        return json.loads(r.read())


def main() -> int:
    entries = []
    for folder in sorted(p for p in CACHE.iterdir() if (p / "manifest.json").is_file()):
        manifest = json.loads((folder / "manifest.json").read_text())
        slug = manifest["source_repo_url"].removeprefix("https://github.com/")
        info = _github(slug)
        entries.append({
            "repo": info.get("full_name") or slug,
            "folder": folder.name,
            "what": " ".join(manifest["entrypoint"].split()),
            # GitHub's own words for it: search terms a user types ("youtube",
            # "audio", "chart") are often only here.
            "about": " ".join((info.get("description") or "").split())[:300],
            "topics": info.get("topics") or [],
            "license": manifest.get("source_license") or "none",
            "language": info.get("language") or "-",
            "stars": info.get("stargazers_count", 0),
            "created_at": info.get("created_at", ""),
            "pushed_at": info.get("pushed_at", ""),
        })
    (CACHE / "index.json").write_text(json.dumps(entries, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {len(entries)} entries to {CACHE / 'index.json'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
