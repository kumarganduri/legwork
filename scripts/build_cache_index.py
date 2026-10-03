"""Write cache/index.json and the table in cache/README.md from the
manifests: one line per cached repo, so find_tools can offer cached tools
(no API key, about a minute to install) for a need even when GitHub's search
ranks them low. Run after adding, removing or changing a cache entry;
tests/test_discovery.py checks both match the manifests.

    GITHUB_TOKEN=... uv run python scripts/build_cache_index.py

Stars, language and dates are a snapshot from the GitHub API at the time
this runs (a token avoids the 60-requests-an-hour limit).
"""

from __future__ import annotations

import json
import os
import re
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
            "license": _license(info, manifest),
            "language": info.get("language") or "-",
            "stars": info.get("stargazers_count", 0),
            "created_at": info.get("created_at", ""),
            "pushed_at": info.get("pushed_at", ""),
        })
    (CACHE / "index.json").write_text(json.dumps(entries, indent=1, ensure_ascii=False) + "\n")
    write_readme_table(entries)
    print(f"wrote {len(entries)} entries to {CACHE / 'index.json'} and the table in cache/README.md", file=sys.stderr)
    return 0


def _license(info: dict, manifest: dict) -> str:
    """GitHub's SPDX id when it has one. Reading the license file ourselves
    called every GPL-3.0 repo AGPL (the GPL's own text names the Affero
    license) and missed licenses kept in a LICENSE/ folder (fresh QA,
    2026-10-03)."""
    spdx = ((info.get("license") or {}).get("spdx_id") or "").strip()
    if spdx and spdx != "NOASSERTION":
        return spdx
    own = manifest.get("source_license") or ""
    if own and own != "none" and not own.startswith("unrecognized"):
        return own
    return "other (see the repo)" if info.get("license") else "none"


def install_summary(command: str) -> str:
    """The install step, short enough for a table row."""
    lines = [line.strip() for line in command.strip().splitlines() if line.strip()]
    first = lines[0] if lines else ""
    if first.startswith(("pip install", "npm install")):
        return f"`{first}`" + (" + model/data download" if len(lines) > 1 else "")
    return "platform-specific binary download"


def write_readme_table(entries: list[dict]) -> None:
    readme = CACHE / "README.md"
    text = readme.read_text()
    head, sep, rest = text.partition("| Repo | What the wrapper does | Install | License |\n|---|---|---|---|\n")
    if not sep:
        raise SystemExit("cache/README.md has no table header to replace")
    tail = "".join(line for line in rest.splitlines(keepends=True) if not line.startswith("| ["))
    rows = []
    for e in entries:
        manifest = json.loads((CACHE / e["folder"] / "manifest.json").read_text())
        rows.append(f"| [{e['repo']}](https://github.com/{e['repo']}) | {e['what']} | {install_summary(manifest['install_command'])} | {e['license']} |\n")
    head = re.sub(r"these \d+ repos", f"these {len(entries)} repos", head)
    readme.write_text(head + sep + "".join(rows) + tail)


if __name__ == "__main__":
    sys.exit(main())
