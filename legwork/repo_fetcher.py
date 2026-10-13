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
    r"(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?"
    # A link to a branch, file or folder is still that repo (fresh QA, 2026-10-03).
    r"(?:/(?:tree|blob)/\S*|/)?$"
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
        if exc.code in (403, 429):
            # The anonymous API allows 60 requests an hour per IP, which
            # shared networks and CI runners exhaust (first 0.2.0 release
            # run). `git ls-remote` answers "is this a reachable public
            # repo?" with no API quota at all.
            try:
                remote_head_sha(ref.clone_url)
            except RepoAccessError:
                raise RepoAccessError(
                    f"GitHub API rate-limited or blocked access to {ref.slug}, "
                    "and the repo isn't reachable with git either"
                ) from exc
            return ref
        raise RepoAccessError(
            f"Unexpected GitHub API response ({exc.code}) for {ref.slug}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RepoAccessError(
            f"Couldn't reach GitHub to check {ref.slug}: {exc.reason}"
        ) from exc


_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_REF_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")


def check_ref_name(name: str) -> str:
    """A full commit SHA or a tag/branch name, safe to hand to git: never an
    option (a leading '-'), never a revision expression ('..', '^', '~')."""
    name = name.strip()
    if _SHA_RE.match(name.lower()):
        return name.lower()
    if not _REF_NAME_RE.match(name) or ".." in name or name.endswith((".lock", "/", ".")):
        raise InvalidRepoURLError(
            f"commit {name!r}: give a full 40-character commit SHA or a tag name, e.g. v1.2.0"
        )
    return name


def resolve_ref(ref: RepoRef, name: str) -> str:
    """The commit SHA a full SHA or tag/branch name points at, without cloning."""
    name = check_ref_name(name)
    if _SHA_RE.match(name):
        return name
    try:
        result = subprocess.run(
            ["git", "ls-remote", ref.clone_url, f"refs/tags/{name}", f"refs/tags/{name}^{{}}", f"refs/heads/{name}"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except subprocess.TimeoutExpired as exc:
        raise RepoAccessError(f"Looking up {name} in {ref.slug} timed out") from exc
    shas = dict(reversed(line.split("\t", 1)) for line in result.stdout.splitlines() if "\t" in line)
    for key in (f"refs/tags/{name}^{{}}", f"refs/tags/{name}", f"refs/heads/{name}"):  # a tag's commit, not the tag object
        if key in shas:
            return shas[key]
    raise RepoAccessError(f"{ref.slug} has no tag or branch named {name!r}")


def clone_repo(ref: RepoRef, dest: Path, commit: str | None = None) -> Path:
    """Shallow-clone `ref` into `dest`, at `commit` (a full SHA or a tag)
    when given. Assumes check_repo(ref) already passed."""
    if commit is not None:
        return _clone_at(ref, dest, check_ref_name(commit))
    dest.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", ref.clone_url, str(dest)],
            stdin=subprocess.DEVNULL,
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


def _clone_at(ref: RepoRef, dest: Path, name: str) -> Path:
    """Exactly one commit, by full SHA or tag name (issue #4)."""
    dest.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    steps = [
        ["git", "init", "-q", str(dest)],
        ["git", "-C", str(dest), "remote", "add", "origin", ref.clone_url],
        ["git", "-C", str(dest), "fetch", "-q", "--depth", "1", "origin", name],
        ["git", "-C", str(dest), "checkout", "-q", "--detach", "FETCH_HEAD"],
    ]
    for step in steps:
        try:
            result = subprocess.run(step, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                    timeout=CLONE_TIMEOUT_SECONDS, check=False, env=env)
        except subprocess.TimeoutExpired as exc:
            raise RepoAccessError(f"Clone of {ref.slug} at {name} exceeded {CLONE_TIMEOUT_SECONDS}s") from exc
        if result.returncode != 0:
            raise RepoAccessError(
                f"Couldn't fetch {ref.slug} at {name}: {result.stderr.strip()[:300]} "
                "(use a full 40-character commit SHA or a tag name)"
            )
    return dest


def fetch(url: str, dest: Path, commit: str | None = None) -> ClonedRepo:
    """The T2 entrypoint: check, then clone (at `commit` when given). Raises
    the same exceptions as check_repo and clone_repo."""
    ref = check_repo(url)
    path = clone_repo(ref, dest, commit)
    return ClonedRepo(ref=ref, path=path)


def remote_head_sha(url: str) -> str:
    """The commit the repo's default branch is at right now, without cloning."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", url, "HEAD"],
            stdin=subprocess.DEVNULL,
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
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        raise RepoAccessError(f"Couldn't read the commit of {clone}: {result.stderr.strip()}")
    return result.stdout.strip()
