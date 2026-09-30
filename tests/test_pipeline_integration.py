"""Integration tests spanning T2 (repo_fetcher) and T10 (obfuscation_scanner):
confirm the scanner correctly examines exactly what the fetcher's clone
produces, matching the actual pipeline order (fetch, then scan, before
README parse or anything else touches the clone).

These hit the real network (a real `git clone`) rather than mocks — cloning
is safe even for the malicious repo: `git clone` only copies git objects and
checks out files, it never imports or executes any Python in the process.
The risk in the original spike was running `python extract.py`, not cloning
it. Kept separate from the pure unit test files since they're slower and
depend on GitHub being reachable.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from legwork.obfuscation_scanner import ObfuscatedPayloadDetectedError, scan
from legwork.repo_fetcher import RepoAccessError, RepoNotFoundError, fetch


@pytest.fixture
def workdir():
    d = Path(tempfile.mkdtemp(prefix="legwork-integration-"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _network_available() -> bool:
    try:
        fetch("2akouwu/reverify", Path(tempfile.mkdtemp()) / "probe")
        return True
    except RepoAccessError:
        return False


@pytest.mark.skipif(not _network_available(), reason="GitHub unreachable")
def test_clean_real_repo_clones_and_scans_without_blocking(workdir):
    """reverify (the validation spike's success case) is real, legitimate
    code — fetch() then scan() should complete without raising."""
    cloned = fetch("2akouwu/reverify", workdir / "reverify")
    scan(cloned.path)  # must not raise


@pytest.mark.skipif(not _network_available(), reason="GitHub unreachable")
def test_real_malicious_repo_clones_and_scan_blocks_it(workdir):
    """The actual repo from the validation spike. Clone is safe (no code
    execution); scan() must block it before anything downstream would ever
    import or run it."""
    try:
        cloned = fetch("kruzovic7/ai-data-extractor", workdir / "ai-data-extractor")
    except RepoNotFoundError:
        # GitHub took it down (gone by 2026-09-30). test_obfuscation_scanner.py
        # still covers the same payload through the defanged fixture.
        pytest.skip("the malicious repo has been removed from GitHub")
    with pytest.raises(ObfuscatedPayloadDetectedError, match="extract.py"):
        scan(cloned.path)
