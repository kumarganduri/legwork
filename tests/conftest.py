import os

import pytest

from legwork import sandbox_runner


def pytest_sessionstart(session):
    # In CI a missing or broken sandbox must fail loudly: otherwise every
    # sandbox test skips and the run still looks green.
    if os.environ.get("LEGWORK_REQUIRE_SANDBOX") and not sandbox_runner.available():
        try:
            sandbox_runner._check_backend_available()
        except sandbox_runner.SandboxUnavailableError as exc:
            pytest.exit(f"LEGWORK_REQUIRE_SANDBOX is set but the sandbox is unavailable: {exc}", returncode=1)


@pytest.fixture(autouse=True)
def _no_live_cache(monkeypatch):
    """Builds in tests must never read the public cache on GitHub: that
    would change what a test exercises depending on what's been pushed,
    and a pre-push fetch gets a 404 that GitHub's CDN then caches for
    everyone. Tests that cover the cache point LEGWORK_CACHE_URL at a
    local folder themselves."""
    monkeypatch.setenv("LEGWORK_CACHE_URL", "off")
    from legwork import cache_reader

    monkeypatch.setattr(cache_reader, "_revoked_cache", None)


@pytest.fixture(autouse=True)
def _no_real_key_file(monkeypatch, tmp_path):
    """Never read the developer's own ~/.legwork.env: a test expecting a
    missing key would pass or fail depending on whose machine it ran on."""
    monkeypatch.setenv("LEGWORK_ENV_FILE", str(tmp_path / "no-key-file.env"))


@pytest.fixture(autouse=True)
def _no_live_repo_check(monkeypatch):
    """The no-key message checks GitHub that the repo exists; tests never call
    GitHub for that. Tests of the not-found path patch it themselves."""
    from legwork import builder
    from legwork.repo_fetcher import parse_repo_url

    monkeypatch.setattr(builder.repo_fetcher, "check_repo", parse_repo_url)
