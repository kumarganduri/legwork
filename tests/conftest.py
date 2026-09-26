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
