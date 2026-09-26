"""Turns a local build into a public-cache entry: wrapper.py + manifest.json
in a folder, ready for a manually opened PR.

Design doc: docs/designs/legwork-jit-ai-runtime.md, T7 (manifest schema in
the Distribution Plan; errors in the Error & Rescue Registry).

    cache/<owner>__<repo>/
        wrapper.py
        manifest.json

One entry per repo. A newer synthesis replaces the old one and git history
keeps every version; a bad entry is removed with a plain `git revert`.

Checks run in this order, and every one runs before anything is written:

1. Verbatim copy — the wrapper must not contain a long run of text copied
   from the source repo (README or code). Blocks: VerbatimCopyDetectedError.
2. Smoke test — the wrapper's self-test runs again in the sandbox (network
   off), so the manifest records a result measured now, not remembered.
   Blocks: SmokeTestFailedError.
3. Secret scrub — every output (wrapper, manifest, PR description) is
   checked for key-shaped strings and for the configured LLM key itself.
   Blocks the whole write: SecretScrubTriggeredError.

Staleness is a warning, not a block (T8): on each contribution the repo's
current commit is compared with the entry already in the cache and with
the build being contributed. There's no polling — an entry nobody
contributes again stays silently stale.

The license is a flag, not a block: GPL/AGPL, no license file, or one we
can't recognize gets `license_flag` in the manifest and a warning line in
the PR description, for the human who merges it to decide.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from legwork import cache_reader, codegen, repo_fetcher, sandbox_runner
from legwork.local_store import BuildRecord, NoBuildError
from legwork.repo_fetcher import RepoRef

WRAPPER_LANGUAGE = "python"
DEFAULT_CACHE_DIR = Path("cache")

# A wrapper naturally quotes short things from the README — a command line,
# a tool name, a flag. 50 words in a row is a copied paragraph or function.
VERBATIM_MIN_WORDS = 50
# Bigger files are data or lockfiles, not prose or code worth copying.
_MAX_SOURCE_FILE_BYTES = 2 * 1024 * 1024

# Names only ever appear in error messages — never the matched text.
_SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "sk- style API key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
    "AWS access key ID": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}"),
    "Slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "Hugging Face token": re.compile(r"\bhf_[A-Za-z0-9]{30,}"),
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}

_LICENSE_FILE_RE = re.compile(r"^(?:LICEN[CS]E|COPYING)(?:[-._].*)?$", re.IGNORECASE)
# First match wins, so the more specific GNU licenses come before plain GPL.
_LICENSE_SIGNATURES: list[tuple[str, str]] = [
    ("gnu affero general public license", "AGPL-3.0"),
    ("gnu lesser general public license", "LGPL"),
    ("gnu library general public license", "LGPL"),
    ("gnu general public license version 3", "GPL-3.0"),
    ("gnu general public license version 2", "GPL-2.0"),
    ("gnu general public license", "GPL"),
    ("apache license", "Apache-2.0"),
    ("mozilla public license", "MPL-2.0"),
    ("permission is hereby granted, free of charge", "MIT"),
    ("redistribution and use in source and binary forms", "BSD"),
    ("permission to use, copy, modify, and/or distribute this software for any purpose", "ISC"),
    ("this is free and unencumbered software released into the public domain", "Unlicense"),
]
_COPYLEFT = ("AGPL-3.0", "GPL-3.0", "GPL-2.0", "GPL")


class VerbatimCopyDetectedError(Exception):
    """The wrapper copies a long run of the source repo's text."""


class SmokeTestFailedError(Exception):
    """The wrapper's self-test no longer passes."""


class SecretScrubTriggeredError(Exception):
    """A key-shaped string is in something about to be written."""


class StaleCacheEntryWarning(UserWarning):
    """Returned, never raised: the repo has commits newer than an entry."""


@dataclass(frozen=True)
class LicenseInfo:
    id: str
    flag: str | None


@dataclass(frozen=True)
class CacheEntry:
    path: Path
    manifest: dict
    pr_description: str
    warnings: list[StaleCacheEntryWarning]


# --- license ----------------------------------------------------------------


def detect_license(repo_root: Path) -> LicenseInfo:
    """Read the repo's top-level LICENSE/COPYING file(s). Offline, so what's
    recorded is the license of the exact commit the wrapper was built from."""
    files = sorted(p for p in repo_root.iterdir() if p.is_file() and _LICENSE_FILE_RE.match(p.name))
    if not files:
        return LicenseInfo("none", "missing: no license file, so redistribution terms are unknown")

    ids = []
    for path in files:
        text = " ".join(path.read_text(errors="replace")[:64 * 1024].lower().split())
        found = next((spdx for signature, spdx in _LICENSE_SIGNATURES if signature in text), None)
        if found == "BSD":
            found = "BSD-3-Clause" if "neither the name" in text else "BSD-2-Clause"
        ids.append(found or f"unrecognized ({path.name})")
    license_id = " OR ".join(dict.fromkeys(ids))

    copyleft = [i for i in ids if i in _COPYLEFT]
    if copyleft:
        return LicenseInfo(license_id, f"copyleft: {', '.join(copyleft)}; confirm redistribution is OK")
    if any(i.startswith("unrecognized") for i in ids):
        return LicenseInfo(license_id, "unrecognized: a human should read the license file")
    return LicenseInfo(license_id, None)


# --- verbatim copy ------------------------------------------------------------


def _words(text: str) -> list[str]:
    # Words only: reflowing, re-indenting or re-punctuating copied text
    # doesn't hide it.
    return re.findall(r"\w+", text.lower())


def _source_files(repo_root: Path):
    for path in sorted(repo_root.rglob("*")):
        if ".git" in path.relative_to(repo_root).parts or not path.is_file() or path.is_symlink():
            continue
        if path.stat().st_size > _MAX_SOURCE_FILE_BYTES:
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue  # binary
        yield path, data.decode("utf-8", errors="replace")


def check_verbatim_copy(wrapper_code: str, repo_root: Path, min_words: int = VERBATIM_MIN_WORDS) -> None:
    """Raise VerbatimCopyDetectedError if `min_words` consecutive words of the
    wrapper also appear consecutively in any file of the source repo."""
    wrapper_words = _words(wrapper_code)
    runs = {tuple(wrapper_words[i : i + min_words]) for i in range(len(wrapper_words) - min_words + 1)}
    if not runs:
        return
    # Cheap first look before building a full-length tuple for every
    # position in every source file.
    openings = {run[:3] for run in runs}

    for path, text in _source_files(repo_root):
        words = _words(text)
        for i in range(len(words) - min_words + 1):
            if tuple(words[i : i + 3]) in openings and tuple(words[i : i + min_words]) in runs:
                excerpt = " ".join(words[i : i + 12])
                raise VerbatimCopyDetectedError(
                    f"Wrapper appears to copy source content verbatim — not added to cache. "
                    f"{min_words}+ words match {path.relative_to(repo_root)}, starting: \"{excerpt} ...\""
                )


# --- smoke test ---------------------------------------------------------------


def run_smoke_test(record: BuildRecord) -> dict:
    """Run the wrapper's own self-test again, sandboxed with the network off —
    the same check that passed at build time."""
    python = Path(record.python_path)
    env = {"PATH": f"{python.parent}:{sandbox_runner.MINIMAL_PATH}"}
    try:
        sandbox_runner.invoke([str(python), record.wrapper_path], Path(record.attempt_dir), extra_env=env)
    except (sandbox_runner.WrapperRuntimeError, sandbox_runner.TimeoutExceeded) as exc:
        raise SmokeTestFailedError(f"Smoke test failed — not added to cache. {exc}") from exc
    return {
        "result": "passed",
        "check": "wrapper self-test (python wrapper.py) in the Legwork sandbox, network off",
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# --- secret scrub -------------------------------------------------------------


def find_secrets(text: str, known_secrets: list[str]) -> list[str]:
    """Names of every secret pattern found in `text`. Never the match itself."""
    found = [name for name, pattern in _SECRET_PATTERNS.items() if pattern.search(text)]
    if any(secret and secret in text for secret in known_secrets):
        found.append("the configured LEGWORK_LLM_API_KEY")
    return found


def _known_secrets() -> list[str]:
    # The user's own key, whatever its format. Too-short values would match
    # ordinary text, and a real key is never that short.
    key = os.environ.get("LEGWORK_LLM_API_KEY", "")
    return [key] if len(key) >= 8 else []


# --- staleness -------------------------------------------------------------------


def _entry_sha(entry_dir: Path) -> str | None:
    try:
        return json.loads((entry_dir / "manifest.json").read_text())["commit_sha"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def check_staleness(ref: RepoRef, build_sha: str, entry_dir: Path) -> tuple[list[StaleCacheEntryWarning], bool]:
    """Compare the repo's current commit with the existing cache entry and
    with this build. Returns the warnings and whether this build is behind."""
    try:
        current = repo_fetcher.remote_head_sha(ref.clone_url)
    except repo_fetcher.RepoAccessError as exc:
        return [StaleCacheEntryWarning(f"Couldn't check {ref.slug} for new commits, so staleness is unknown. {exc}")], False

    warnings = []
    cached = _entry_sha(entry_dir)
    if cached and cached != current:
        warnings.append(
            StaleCacheEntryWarning(
                f"Cache entry may be stale — repo has new commits. The existing entry is from "
                f"{cached[:12]}; {ref.slug} is now at {current[:12]}."
            )
        )
    build_behind = build_sha != current
    if build_behind:
        warnings.append(
            StaleCacheEntryWarning(
                f"This build may be stale — repo has new commits. It's from {build_sha[:12]}; "
                f"{ref.slug} is now at {current[:12]}. Rebuild with `legwork {ref.slug}` to contribute a current wrapper."
            )
        )
    return warnings, build_behind


# --- manifest + PR --------------------------------------------------------------


def build_manifest(
    ref: RepoRef, record: BuildRecord, commit_sha: str, license_info: LicenseInfo, smoke_test: dict
) -> dict:
    return {
        "source_repo_url": f"https://github.com/{ref.slug}",
        "commit_sha": commit_sha,
        "synthesis_date": record.built_at,
        "wrapper_language": WRAPPER_LANGUAGE,
        "source_license": license_info.id,
        "license_flag": license_info.flag,
        "llm_model": record.model,
        "smoke_test": smoke_test,
        # Not provenance, but needed to run the wrapper from the cache.
        "entrypoint": record.entrypoint,
        "install_command": record.install_command,
        "mcp_sdk": codegen.MCP_SDK_PIN,
    }


def pr_description(ref: RepoRef, manifest: dict, stale_warning: str | None = None) -> str:
    lines = [
        f"Add Legwork wrapper for {ref.slug}",
        "",
        f"- Source: {manifest['source_repo_url']} @ {manifest['commit_sha'][:12]}",
        f"- What it wraps: {manifest['entrypoint']}",
        f"- Synthesized by {manifest['llm_model']} on {manifest['synthesis_date']}",
        f"- Smoke test: {manifest['smoke_test']['result']} ({manifest['smoke_test']['ran_at']})",
        f"- Source license: {manifest['source_license']}",
    ]
    if manifest["license_flag"]:
        lines += ["", f"⚠️ License warning — {manifest['license_flag']}. Please check before merging."]
    if stale_warning:
        lines += ["", f"⚠️ {stale_warning}"]
    return "\n".join(lines) + "\n"


# --- write ----------------------------------------------------------------------


def _write_atomically(entry_dir: Path, files: dict[str, str]) -> None:
    """All files appear together or not at all, and a previous entry is only
    replaced once the new one is fully on disk."""
    entry_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{entry_dir.name}.", dir=entry_dir.parent))
    try:
        for name, content in files.items():
            (staging / name).write_text(content)
        if entry_dir.exists():
            retired = Path(tempfile.mkdtemp(prefix=f".{entry_dir.name}.old.", dir=entry_dir.parent))
            entry_dir.rename(retired / entry_dir.name)
            staging.rename(entry_dir)
            shutil.rmtree(retired)
        else:
            staging.rename(entry_dir)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def write_entry(ref: RepoRef, record: BuildRecord, cache_dir: Path = DEFAULT_CACHE_DIR) -> CacheEntry:
    """The T7 entrypoint. Runs every check, then writes the entry to
    `cache_dir/<owner>__<repo>/`. Raises VerbatimCopyDetectedError,
    SmokeTestFailedError or SecretScrubTriggeredError with nothing written.
    Staleness comes back as warnings on the entry."""
    clone = Path(record.attempt_dir).parent / "repo"
    if not clone.is_dir():
        raise NoBuildError(f"The source clone for {ref.slug} is gone ({clone}) — rebuild with `legwork {ref.slug}`.")
    wrapper_code = Path(record.wrapper_path).read_text()

    check_verbatim_copy(wrapper_code, clone)
    smoke_test = run_smoke_test(record)
    build_sha = repo_fetcher.head_sha(clone)
    entry_dir = cache_dir / cache_reader.entry_name(ref)
    warnings, build_behind = check_staleness(ref, build_sha, entry_dir)
    manifest = build_manifest(ref, record, build_sha, detect_license(clone), smoke_test)
    pr_text = pr_description(ref, manifest, str(warnings[-1]) if build_behind else None)

    outputs = {
        "wrapper.py": wrapper_code,
        "manifest.json": json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    }
    known = _known_secrets()
    for name, content in [*outputs.items(), ("the PR description", pr_text)]:
        hits = find_secrets(content, known)
        if hits:
            raise SecretScrubTriggeredError(
                f"Possible API key detected in output — write blocked. Found a {' and a '.join(hits)} in {name}."
            )

    _write_atomically(entry_dir, outputs)
    return CacheEntry(path=entry_dir, manifest=manifest, pr_description=pr_text, warnings=warnings)
