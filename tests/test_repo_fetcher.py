from __future__ import annotations

import subprocess
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from legwork.repo_fetcher import (
    ClonedRepo,
    InvalidRepoURLError,
    RepoAccessError,
    RepoNotFoundError,
    RepoRef,
    check_repo,
    clone_repo,
    fetch,
)


# --- URL parsing / InvalidRepoURLError -------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not a url at all",
        "https://gitlab.com/owner/repo",  # wrong host — Legwork targets GitHub
        "https://github.com/only-owner",  # missing repo segment
        "https://github.com/owner/",  # missing repo segment
        "owner/repo/extra",  # too many segments for shorthand
        "owner//repo",  # empty owner segment
    ],
)
def test_malformed_url_raises_invalid(url):
    with pytest.raises(InvalidRepoURLError):
        check_repo(url)


@pytest.mark.parametrize(
    "url,expected_owner,expected_repo",
    [
        ("https://github.com/2akouwu/reverify", "2akouwu", "reverify"),
        ("https://github.com/2akouwu/reverify.git", "2akouwu", "reverify"),
        ("https://github.com/2akouwu/reverify/", "2akouwu", "reverify"),
        ("github.com/2akouwu/reverify", "2akouwu", "reverify"),
        ("2akouwu/reverify", "2akouwu", "reverify"),
    ],
)
def test_valid_url_forms_parse_to_same_ref(url, expected_owner, expected_repo):
    fake_response = MagicMock()
    fake_response.status = 200
    fake_response.__enter__.return_value = fake_response
    with patch("legwork.repo_fetcher.urllib.request.urlopen", return_value=fake_response):
        ref = check_repo(url)
    assert ref == RepoRef(owner=expected_owner, repo=expected_repo)


# --- GitHub API status-code branching ---------------------------------------


def test_404_raises_repo_not_found_not_access_error():
    """GitHub returns 404 for both nonexistent AND private repos when
    unauthenticated — RepoNotFoundError's message should say so, not claim
    certainty it doesn't have."""
    http_error = urllib.error.HTTPError(
        url="", code=404, msg="Not Found", hdrs=None, fp=None
    )
    with patch("legwork.repo_fetcher.urllib.request.urlopen", side_effect=http_error):
        with pytest.raises(RepoNotFoundError, match="not found or private"):
            check_repo("owner/repo")


def test_403_raises_repo_access_error():
    http_error = urllib.error.HTTPError(
        url="", code=403, msg="Forbidden", hdrs=None, fp=None
    )
    with patch("legwork.repo_fetcher.urllib.request.urlopen", side_effect=http_error):
        with pytest.raises(RepoAccessError, match="rate-limited or blocked"):
            check_repo("owner/repo")


def test_network_failure_raises_repo_access_error():
    url_error = urllib.error.URLError("DNS lookup failed")
    with patch("legwork.repo_fetcher.urllib.request.urlopen", side_effect=url_error):
        with pytest.raises(RepoAccessError, match="Couldn't reach GitHub"):
            check_repo("owner/repo")


def test_200_returns_repo_ref():
    fake_response = MagicMock()
    fake_response.status = 200
    fake_response.__enter__.return_value = fake_response
    with patch("legwork.repo_fetcher.urllib.request.urlopen", return_value=fake_response):
        ref = check_repo("owner/repo")
    assert ref == RepoRef(owner="owner", repo="repo")


# --- clone_repo --------------------------------------------------------------


def test_clone_repo_success(tmp_path):
    ref = RepoRef(owner="owner", repo="repo")
    dest = tmp_path / "clone"
    fake_result = MagicMock(returncode=0, stderr="")
    with patch("legwork.repo_fetcher.subprocess.run", return_value=fake_result) as run:
        result = clone_repo(ref, dest)
    assert result == dest
    assert dest.exists()
    args = run.call_args[0][0]
    assert args[:3] == ["git", "clone", "--depth"]
    assert ref.clone_url in args


def test_clone_repo_failure_raises_repo_access_error(tmp_path):
    ref = RepoRef(owner="owner", repo="repo")
    dest = tmp_path / "clone"
    fake_result = MagicMock(returncode=128, stderr="fatal: repository not found")
    with patch("legwork.repo_fetcher.subprocess.run", return_value=fake_result):
        with pytest.raises(RepoAccessError, match="Clone of owner/repo failed"):
            clone_repo(ref, dest)


def test_clone_repo_timeout_raises_repo_access_error(tmp_path):
    ref = RepoRef(owner="owner", repo="repo")
    dest = tmp_path / "clone"
    with patch(
        "legwork.repo_fetcher.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="git clone", timeout=120),
    ):
        with pytest.raises(RepoAccessError, match="exceeded 120s"):
            clone_repo(ref, dest)


# --- fetch() orchestration ---------------------------------------------------


def test_fetch_checks_before_cloning(tmp_path):
    """fetch() must call check_repo before clone_repo, so a bad URL never
    pays for a git clone (the reason T2 exists as a separate precondition
    step)."""
    dest = tmp_path / "clone"
    with (
        patch("legwork.repo_fetcher.check_repo") as mock_check,
        patch("legwork.repo_fetcher.clone_repo") as mock_clone,
    ):
        mock_check.return_value = RepoRef(owner="owner", repo="repo")
        mock_clone.return_value = dest
        result = fetch("owner/repo", dest)

    mock_check.assert_called_once_with("owner/repo")
    mock_clone.assert_called_once_with(RepoRef(owner="owner", repo="repo"), dest)
    assert result == ClonedRepo(ref=RepoRef(owner="owner", repo="repo"), path=dest)


def test_fetch_propagates_invalid_url_without_cloning(tmp_path):
    dest = tmp_path / "clone"
    with patch("legwork.repo_fetcher.clone_repo") as mock_clone:
        with pytest.raises(InvalidRepoURLError):
            fetch("not a url", dest)
        mock_clone.assert_not_called()
