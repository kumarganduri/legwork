# Legwork public cache

Wrappers here are used by `legwork owner/repo` without a model call, so
these repos build with no API key. Each one is still scanned, installed and
self-tested on your machine before it's used.

| Repo | What the wrapper does | Install | License |
|---|---|---|---|
| [2akouwu/reverify](https://github.com/2akouwu/reverify) | Verify claims about binaries using Reverify’s deterministic tools (wraps `reverify verify --json`) | `pip install reverify` | MIT |
| [mikehasa/golive-skill](https://github.com/mikehasa/golive-skill) | Run the GoLive CLI (npm package "golive") to fetch version info and other read-only commands | `npm install --no-fund --no-audit golive@alpha` | MIT |
| [nokia-applied-research/AnyJev](https://github.com/nokia-applied-research/AnyJev) | Decide a multiple-choice question via AnyJev against a running vLLM HTTP server and return calibrated choice probabilities (L0 or raw) | `pip install "anyjev[hf]"` | Apache-2.0 |
| [yetone/magpie](https://github.com/yetone/magpie) | Run magpie CLI commands (list presets, models, agents, or arbitrary subcommands) | `curl -fsSL https://usemagpie.ai/install.sh | sh` | MIT |

## Format

One folder per repo, `<owner>__<repo>` in lowercase:

- `wrapper.py`: the MCP wrapper, with its self-test
- `manifest.json`: source repo and commit, synthesis date and model,
  source license and any license flag, smoke-test result, install command

## Adding one

Run `legwork contribute owner/repo` in your fork and open a PR; see
[CONTRIBUTING.md](../CONTRIBUTING.md). A bad entry is removed with a plain
`git revert`.
