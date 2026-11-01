# Legwork

When you ask Claude for something it has no tool for, such as transcribing a voice memo, pulling the tables out of a PDF or removing an image background, Legwork lets it find an open-source tool on GitHub and install it as an MCP tool on your computer. You approve each install. The tool then runs in your operating system's sandbox (sandbox-exec on macOS, bubblewrap on Linux) with read-only access to the folder you choose, none of your environment variables or keys, and no network.

This plugin starts Legwork's MCP server, `legwork-mcp` from PyPI, pinned to one release. It runs in Claude Code and in Cowork tasks that run on your computer. Full documentation: <https://github.com/kumarganduri/legwork>.

## Requirements

- macOS or Linux. On Linux, bubblewrap (`bwrap`) must be installed.
- [uv](https://docs.astral.sh/uv/), which provides the `uvx` command the server starts with.

## Settings

- **Folder tools may read**: the most any installed tool may read, read-only. It defaults to `~/Downloads`. Legwork refuses your whole home folder and any folder that holds keys or passwords.

## Tools

- `find_tools`: searches GitHub and the Legwork cache for a tool that does a job. It installs nothing.
- `install_tool`: clones a repo, scans its source for hidden payloads, builds a wrapper and tests it, all in the sandbox. For a repo that isn't in the Legwork cache, a model writes the install command, and the build stops to show you that exact command; none of it runs until you approve it.
- `install_status`: reports on an install that is still building.
- `list_installed_tools`: lists installed tools, what each may read, and the commit each was built from.
- `use_tool`: runs a tool of an installed repo, in its sandbox. Installed tools also appear directly as `<repo>__<tool>`.

## What it runs, sends and fetches

- **Runs**: code from the GitHub repos you approve. Each repo's install step runs in the sandbox with network on, so it can download packages, and with no access to your files. The installed tool runs in the sandbox with only the folder above and no network.
- **Contacts**: the GitHub API (`api.github.com`) to search; `github.com` to clone the repos you approve; `raw.githubusercontent.com` for the Legwork cache of reviewed wrappers; and whatever package indexes a repo's install step uses, such as PyPI or npm. Legwork records the hosts each install reached.
- **For a repo not in the cache**: Legwork sends that repo's README and any build errors to the model provider you configure yourself (an OpenAI-compatible endpoint and key in `~/.legwork.env`; see the main README). Without a key, only the 37 cached tools can be installed.
- **Stores**: builds and records in `~/.legwork`, and files the tools make in `~/Legwork/outputs`, on your computer.

## Privacy

Legwork collects no data about you and has no telemetry. Nothing is sent to Legwork's author or to any server run for Legwork. The only outside services it contacts are the ones listed above: GitHub, the package indexes a repo's install step uses, and, only for repos not in the cache, the model provider you configure, which receives that repo's README and build errors under its own terms. Everything Legwork stores stays on your computer, in `~/.legwork` and `~/Legwork/outputs`, until you delete it (`legwork clean` frees space). Questions: <https://github.com/kumarganduri/legwork/issues>.

## License

MIT. See <https://github.com/kumarganduri/legwork/blob/main/LICENSE>.
