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

### Known answers

Every entry needs a known answer: a fixed input and the output a person
expects from it. A wrapper's self-test is written by the same model as the
wrapper, so a wrapper that misread the README can pass its own wrong test
([#3](https://github.com/kumarganduri/legwork/issues/3)). The known answer
is the check it can't write for itself.

Put the input in `cache/owner__repo/fixtures/` (small files, under 2 MB in
all, no links) and add this to `manifest.json`:

```json
"known_answer": {
  "tool": "extract_tables",
  "arguments": {"path": "{fixtures}/launch-plan.pdf"},
  "expect_contains": ["Offline sync conflicts", "Last-writer-wins plus a conflict inbox"],
  "derived_from": "launch-plan.pdf was made for Legwork's tests with a three-row risk table; these are cells of that table"
}
```

- `tool` and `arguments` are the call; `{fixtures}` becomes the folder's path.
  The tool can read only that folder, with no network.
- `expect_contains` lists text that must be in the reply. For a tool that
  returns a PNG, `expect_image` can check its size: `{"width": 290, "height": 290}`.
- `derived_from` says why the answer is right. **The expected answer must
  follow from how you made the input**, not from running the tool and
  copying what it printed: a CSV whose column you made sum to 42, a voice
  memo saying a sentence you wrote, a Python file with an unused import
  that a linter must flag. If the tool disagrees, the wrapper may be wrong.
- Tools that need the internet set `"needs_network": true` in the manifest.
  CI skips their known answer (YouTube blocks cloud machines); run it
  yourself with `uv run python scripts/check_cache_entry.py cache/owner__repo --network`.

`legwork contribute` keeps an entry's known answer and fixtures when you
rebuild it.

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
publishing. [docs/RELEASING.md](docs/RELEASING.md) has the checklist and how
to roll back a release or a cache entry.

## Security issues

See [SECURITY.md](SECURITY.md); please report sandbox escapes privately.
