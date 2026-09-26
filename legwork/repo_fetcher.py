"""Reachability/access precondition check and shallow clone for a target
GitHub repo.

Design doc: docs/designs/legwork-jit-ai-runtime.md, T2. Order matters: the
reachability check runs before any clone, so a bad URL or an inaccessible
repo fails fast without paying for a git clone or consuming a
wrapper-repair retry attempt. The clone itself happens here (not lazily at
install time) because T10's obfuscation scanner and README parsing both
need the real source tree, not just an API-fetched README (eng review
pass 2, 2026-09-22 — see design doc "Clone timing").
"""

from __future__ import annotations

import os
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

GITHUB_API = "https://api.github.com/repos/{owner}/{repo}"
CLONE_TIMEOUT_SECONDS = 120  # shares the install-phase timeout budget

_FULL_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/"
    r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)/"
    r"(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
_SHORTHAND_RE = re.compile(
    r"^(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)/"
    r"(?P<repo>[A-Za-z0-9_.-]+)$"
)


class InvalidRepoURLError(Exception):
    """The given string isn't a well-formed GitHub repo URL or owner/repo shorthand."""


class RepoAccessError(Exception):
    """The repo is private, rate-limited, or otherwise inaccessible.

    Also covers network-level failures reaching GitHub's API (DNS, connection
    refused, timeout) — from the caller's perspective those are just another
    way the repo turned out to be inaccessible right now.
    """


class RepoNotFoundError(Exception):
    """GitHub returned 404 for this repo.

    GitHub's API returns 404 (not 403) for private repos when the request
    is unauthenticated or lacks access — this is deliberate on GitHub's
    part, to avoid leaking whether a private repo exists. So "not found"
    and "private" are genuinely indistinguishable here without a token that
    has access; the user-facing message says so rather than guessing.
    """


@dataclass(frozen=True)
class RepoRef:
    owner: str
    repo: str

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}.git"

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"


@dataclass(frozen=True)
class ClonedRepo:
    ref: RepoRef
    path: Path


def parse_repo_url(url: str) -> RepoRef:
    url = url.strip()
    match = _FULL_URL_RE.match(url) or _SHORTHAND_RE.match(url)
    if not match:
        raise InvalidRepoURLError(
            f"Invalid repo URL: {url!r} — expected a github.com URL or "
            "'owner/repo' shorthand"
        )
    return RepoRef(owner=match.group("owner"), repo=match.group("repo"))


def check_repo(url: str) -> RepoRef:
    """Validate the URL and confirm the repo is reachable and public.

    Raises InvalidRepoURLError, RepoNotFoundError, or RepoAccessError.
    Does not clone — this is the cheap precondition check that runs before
    paying for a git clone.
    """
    ref = parse_repo_url(url)
    api_url = GITHUB_API.format(owner=ref.owner, repo=ref.repo)
    request = urllib.request.Request(
        api_url, headers={"Accept": "application/vnd.github+json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status == 200:
                return ref
            raise RepoAccessError(
                f"Unexpected GitHub API response ({response.status}) for {ref.slug}"
            )
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise RepoNotFoundError(
                f"Repo not found or private: {ref.slug}"
            ) from exc
        if exc.code == 403:
            raise RepoAccessError(
                f"GitHub API rate-limited or blocked access to {ref.slug}"
            ) from exc
        raise RepoAccessError(
            f"Unexpected GitHub API response ({exc.code}) for {ref.slug}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RepoAccessError(
            f"Couldn't reach GitHub to check {ref.slug}: {exc.reason}"
        ) from exc


def clone_repo(ref: RepoRef, dest: Path) -> Path:
    """Shallow-clone `ref` into `dest`. Assumes check_repo(ref) already passed."""
    dest.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", ref.clone_url, str(dest)],
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT_SECONDS,
            check=False,  # exit code inspected manually below
        )
    except subprocess.TimeoutExpired as exc:
        raise RepoAccessError(
            f"Clone of {ref.slug} exceeded {CLONE_TIMEOUT_SECONDS}s"
        ) from exc
    if result.returncode != 0:
        raise RepoAccessError(f"Clone of {ref.slug} failed: {result.stderr.strip()}")
    return dest


def fetch(url: str, dest: Path) -> ClonedRepo:
    """The T2 entrypoint: check, then clone. Raises the same exceptions as
    check_repo and clone_repo."""
    ref = check_repo(url)
    path = clone_repo(ref, dest)
    return ClonedRepo(ref=ref, path=path)


def remote_head_sha(url: str) -> str:
    """The commit the repo's default branch is at right now, without cloning."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", url, "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            # A missing repo must fail, not stop and ask for a username.
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except subprocess.TimeoutExpired as exc:
        raise RepoAccessError(f"Checking {url} for new commits timed out") from exc
    fields = result.stdout.split()
    if result.returncode != 0 or not fields:
        raise RepoAccessError(f"Couldn't read the current commit of {url}: {result.stderr.strip() or 'no HEAD'}")
    return fields[0]


def head_sha(clone: Path) -> str:
    """The commit a clone is at — what a wrapper was synthesized against."""
    result = subprocess.run(
        ["git", "-C", str(clone), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        raise RepoAccessError(f"Couldn't read the commit of {clone}: {result.stderr.strip()}")
    return result.stdout.strip()
