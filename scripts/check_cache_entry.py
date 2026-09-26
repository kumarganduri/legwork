"""Check one proposed cache/ entry the way a user's build will use it.

Run by .github/workflows/cache-check.yml on every PR that touches cache/,
with this script taken from main (never from the PR). Every user who
builds that repo runs the entry's install command with network on and its
wrapper on their machine, so an entry has to pass everything here, and a
human still reviews the install command it prints.

    uv run python scripts/check_cache_entry.py cache/<owner>__<repo>
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from legwork import (
    cache_reader,
    cache_writer,
    obfuscation_scanner,
    repo_fetcher,
    retry_loop,
)


def check(entry: Path) -> tuple[list[str], list[str]]:
    """(problems, summary lines). No problems means the entry passes."""
    problems: list[str] = []
    try:
        manifest = json.loads((entry / "manifest.json").read_text())
        wrapper = (entry / "wrapper.py").read_text()
    except (OSError, ValueError) as exc:
        return [f"can't read the entry: {exc}"], []
    extra = sorted(p.name for p in entry.iterdir() if p.name not in ("manifest.json", "wrapper.py"))
    if extra:
        problems.append(f"unexpected files: {', '.join(extra)} (an entry is wrapper.py + manifest.json)")

    missing = [f for f in cache_reader._REQUIRED_FIELDS if not manifest.get(f)]
    if missing:
        return problems + [f"manifest is missing {', '.join(missing)}"], []
    try:
        ref = repo_fetcher.parse_repo_url(manifest["source_repo_url"])
    except repo_fetcher.InvalidRepoURLError as exc:
        return problems + [f"source_repo_url: {exc}"], []
    if entry.name != cache_reader.entry_name(ref):
        problems.append(f"folder should be named {cache_reader.entry_name(ref)} for {ref.slug}")

    for name, text in (("wrapper.py", wrapper), ("manifest.json", json.dumps(manifest))):
        hits = cache_writer.find_secrets(text, [])
        if hits:
            problems.append(f"{name} contains a {' and a '.join(hits)}")
    try:
        obfuscation_scanner.scan(entry)
    except obfuscation_scanner.ObfuscatedPayloadDetectedError as exc:
        problems.append(f"wrapper looks obfuscated: {exc}")
    if problems:
        return problems, []

    summary = [
        f"**{ref.slug}** @ `{manifest['commit_sha'][:12]}`",
        f"- Install command (runs with network on for every user; **review it**): `{manifest['install_command']}`",
        f"- What it wraps: {manifest['entrypoint']}",
        f"- Written by: {manifest['llm_model']}",
        f"- Source license: {manifest.get('source_license', '?')}"
        + (f" ⚠️ {manifest['license_flag']}" if manifest.get("license_flag") else ""),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        try:
            result = retry_loop.run_cached(ref.slug, workdir, manifest["install_command"], wrapper)
        except (repo_fetcher.RepoNotFoundError, repo_fetcher.RepoAccessError, obfuscation_scanner.ObfuscatedPayloadDetectedError) as exc:
            return [f"{type(exc).__name__}: {exc}"], summary
        if not result.success:
            return [f"the wrapper failed its sandboxed install/self-test: {result.final_error}"], summary
        try:
            cache_writer.check_verbatim_copy(wrapper, workdir / "repo")
        except cache_writer.VerbatimCopyDetectedError as exc:
            return [str(exc)], summary
    summary.append("- Scan, sandboxed install and self-test: passed")
    try:
        current = repo_fetcher.remote_head_sha(ref.clone_url)
        if current != manifest["commit_sha"]:
            summary.append(f"- Note: the repo has newer commits (now `{current[:12]}`); fine if the self-test passed")
    except repo_fetcher.RepoAccessError:
        pass
    return [], summary


def main(argv: list[str]) -> int:
    failed = False
    report = []
    for arg in argv:
        entry = Path(arg)
        problems, summary = check(entry)
        report += summary
        if problems:
            failed = True
            report += [f"❌ `{entry}`:"] + [f"  - {p}" for p in problems]
        else:
            report.append(f"✅ `{entry}` passed")
        report.append("")
    text = "\n".join(report)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write("## Cache entry check\n\n" + text + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
