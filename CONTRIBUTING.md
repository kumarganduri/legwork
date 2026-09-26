# Contributing

Thanks for helping. Two kinds of contribution are especially welcome:
wrappers for the public cache, and fixes found by running Legwork against
real repos.

## Contribute a wrapper

```sh
uvx legwork-mcp owner/repo              # build and verify it
uvx legwork-mcp contribute owner/repo   # writes cache/owner__repo/ and prints a PR description
```

Run `contribute` inside your fork of this repo, commit the new
`cache/owner__repo/` folder, and open a PR with the printed description.
`contribute` refuses to write an entry that copies the source repo's text
verbatim, fails its self-test, or contains anything shaped like an API key.
A license warning in the description doesn't block the PR; it's there for
the reviewer.

A `cache-check` job then re-checks the entry on a fresh Linux machine the
way a user's build would use it, and posts its install command in the job
summary. Reviewers: read that install command. It runs, with network on,
for everyone who builds that repo.

## Work on Legwork

```sh
git clone https://github.com/kumarganduri/legwork && cd legwork
uv sync
uv run pytest
```

- The sandbox and integration tests need macOS (`sandbox-exec`) or Linux
  with bubblewrap (see the README), and some need network access to
  GitHub. CI runs everything on macOS and Ubuntu; set
  `LEGWORK_REQUIRE_SANDBOX=1` to make a missing sandbox fail the run
  instead of skipping those tests.
- Tests use the real sandbox, not mocks, for anything about isolation.
- `tests/fixtures/ai_data_extractor_payload.py` is a real malicious file
  with its payload destroyed. Tests only parse it with `ast`; keep it that
  way.
- Found a repo Legwork handles badly? An issue with the repo name, the
  command, and the `attempts.log` from the build folder is the fastest way
  to a fix. Check it for anything private first.

## Releases

Bump `version` in `pyproject.toml`, then push a matching tag (`v0.1.1`).
The `publish` workflow tests, builds and publishes to PyPI with trusted
publishing.

## Security issues

See [SECURITY.md](SECURITY.md); please report sandbox escapes privately.
