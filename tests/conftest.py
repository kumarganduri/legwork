import pytest


@pytest.fixture(autouse=True)
def _no_live_cache(monkeypatch):
    """Builds in tests must never read the public cache on GitHub: that
    would change what a test exercises depending on what's been pushed,
    and a pre-push fetch gets a 404 that GitHub's CDN then caches for
    everyone. Tests that cover the cache point LEGWORK_CACHE_URL at a
    local folder themselves."""
    monkeypatch.setenv("LEGWORK_CACHE_URL", "off")
