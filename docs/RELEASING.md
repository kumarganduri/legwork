# Releasing and rolling back

## Release

1. `uv run pytest -q` and `uv run python scripts/e2e_hub.py` pass locally.
2. Bump `version` in `pyproject.toml`, commit, push to main, and wait for
   CI to pass on every OS.
3. Tag that commit and push the tag: `git tag -a v0.7.9 -m "..." && git push origin v0.7.9`.
   The `publish` workflow tests again, builds, and publishes to PyPI with
   trusted publishing.
4. Check it's live: `uvx --refresh legwork-mcp@latest --version`.
5. `gh release create v0.7.9 --verify-tag --notes-file notes.md`. Credit
   anyone who reported an escape.

The tag also fixes the cache that release reads
(`raw.githubusercontent.com/kumarganduri/legwork/v0.7.9/cache`). A tool
added to `cache/` on main reaches users with the next release, not before.
So **never move or delete a release tag**: it would change what installed
copies of that release install.

## Roll back

**A bad release.** Yank it on PyPI (project → Manage → the release →
Options → Yank), with the reason. `uvx legwork-mcp@latest` and new
installs skip a yanked version; anyone who pinned it keeps it. Then fix
forward: revert on main, bump the version, release. Never re-upload the
same version number; PyPI refuses it.

**A bad cache entry** (a compromised upstream, a wrapper that misbehaves).
Add it to `cache/revoked.json` on main, with the reason and date:

```json
{ "owner/repo": "upstream release 1.2.3 compromised, 2026-10-10" }
```

Every release reads this file from main, so within about ten minutes no
installed version will install that entry from the cache or list it as
cached. It can only remove entries. Then remove or fix the entry itself
with a normal PR. It isn't retroactive: a tool someone already installed
stays installed until they remove it, so say so in the advisory.

**The whole cache.** Users can turn it off with `LEGWORK_CACHE_URL=off` in
their MCP config's `env`; builds then need a model key.

**A sandbox escape in a released version.** Fix, release, yank the
affected versions, and publish a GitHub security advisory naming the
affected and fixed versions. Add it to the table in
[THREAT_MODEL.md](../THREAT_MODEL.md).
