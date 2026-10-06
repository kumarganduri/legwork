# Legwork

<!-- mcp-name: io.github.kumarganduri/legwork -->

[![PyPI](https://img.shields.io/pypi/v/legwork-mcp)](https://pypi.org/project/legwork-mcp/)
[![CI](https://github.com/kumarganduri/legwork/actions/workflows/ci.yml/badge.svg)](https://github.com/kumarganduri/legwork/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

**Your AI finds the open-source tool it needs on GitHub. Legwork installs it in a sandbox.**

https://github.com/user-attachments/assets/9e9b80b5-99ff-4d21-b689-74655418f23d

<p align="center"><sub>A real run in Claude Desktop. The 25-second install is sped up; the malware repos' names are masked.</sub></p>

Add Legwork to Claude, Cursor, Codex, opencode or any [MCP](https://modelcontextprotocol.io)
client once. When you ask for something your AI has no tool for, it
searches GitHub, picks a repo, and asks you to approve installing it.
Legwork then checks the code for hidden payloads, builds a working tool for it in a
sandbox, tests it, and hands it over. In your home folder it can only read
the folders you allow.

```sh
claude mcp add legwork -- uvx legwork-mcp hub --allow-read ~/Downloads
```

Then just ask. Four to try first, all from the [public cache](cache/), so
they need no API key:

- *"Use Legwork to transcribe ~/Downloads/memo.m4a."* Any voice memo, mp3 or
  video, offline. Best in English and other widely spoken languages; its
  small built-in model is unreliable for languages like Telugu.
- *"Use Legwork to remove the background from ~/Downloads/photo.jpg."*
- *"Use Legwork to pull the tables out of ~/Downloads/report.pdf."*
- *"Use Legwork to make a QR code for https://github.com/kumarganduri/legwork."*

Saying "use Legwork" matters in Claude Desktop: without it, Claude may reach
for its own cloud code sandbox, which can't see the files on your computer
unless you attach them.

Your AI finds the tool ([faster-whisper](https://github.com/SYSTRAN/faster-whisper),
[rembg](https://github.com/danielgatis/rembg),
[pdfplumber](https://github.com/jsvine/pdfplumber),
[qrcode](https://github.com/lincolnloop/python-qrcode)), you approve the
install, and about a minute later it does the job. Files a tool makes are
copied to `~/Legwork/outputs/`. On macOS, the first time a tool reads
Downloads, macOS asks whether `uvx` may access it; allow it once. If
something doesn't work, `uvx legwork-mcp doctor` checks your setup. Or
build one tool yourself: `uvx legwork-mcp owner/repo` (a model key for repos
not in the cache).

## Why not just ask your AI to install it?

- **Most repos have no MCP server to install.** Your AI would have to write
  one on the spot, untested, every time. Legwork writes it once, proves it
  works with a self-test, and caches it so the next person doesn't pay for it.
- **Whatever your AI installs runs with full access to your machine:** your
  SSH keys, your files, your environment variables. That's how the two
  malware repos below would have got in. Legwork checks the code for
  obfuscated payloads first, and every tool it installs runs sandboxed: in your home folder it sees only
  the folders you grant.
- **It works across your tools:** the same install works in Claude Code,
  Claude Desktop, Cursor, opencode, Codex CLI, Goose and OpenClaw.

## What it did on real repos

On 2026-09-26 we ran Legwork against **the 10 most-starred AI repos created
on GitHub in the previous week**, taken exactly as search ranked them, with
nothing skipped for being hard:

| Outcome | Repos |
|---|---|
| ✅ Built a working MCP server | **3** — an npm CLI, a Python library, a Go binary |
| ↩️ Refused, with the right reason | **5** — two Android/macOS apps, a desktop app, a repo with no usage docs, a tool that needs its own API keys |
| 🛑 Blocked before install (malware) | **2** |

Two of those top-10 "AI tools", about 700 stars each and three days old
at the time, carried the same byte-identical obfuscated dropper under different
file names. Legwork's pre-install scan refused both in about a second.
Nothing from them was installed or run. Stars are not a trust signal.

The tripwire has to stay quiet on ordinary code too. Run over the 150
most-starred Python, JavaScript and TypeScript repos on GitHub
(2026-10-01), it blocks one: lodash, for a vendored 2009 debugging script
that really does run `eval(unescape(...))`.

Full write-up, including what failed along the way and what we fixed:
[docs/designs/legwork-trending-trial-2026-09-26.md](docs/designs/legwork-trending-trial-2026-09-26.md).

**A second, larger run** on the next 22 new trending repos, with two
models: **gpt-5 built 12 and refused 10 with reasons, with no crashes**;
OpenRouter's free Nemotron built 4. That run also caught two false malware
alarms and a gap where only published packages could be installed, both
fixed. [Write-up](docs/designs/legwork-trending-trial-2-2026-09-27.md).

## Let your AI find its own tools (hub)

`legwork hub` is one MCP server that gives your AI five tools: `find_tools`,
`install_tool`, `install_status`, `list_installed_tools` and `use_tool`.

```sh
claude mcp add legwork -- uvx legwork-mcp hub --allow-read ~/Downloads
```

For Claude Desktop or Cursor, add this to the MCP config (for Claude
Desktop, while the app is quit), with your own user name in the path: a
folder that doesn't exist stops the hub from starting.

```json
{ "mcpServers": { "legwork": { "command": "uvx",
  "args": ["legwork-mcp", "hub", "--allow-read", "/Users/you/Downloads"] } } }
```

For [opencode](https://opencode.ai), add this to `~/.config/opencode/opencode.json`.
opencode runs MCP tools without asking by default, so the `permission` line
is what makes it ask you before an install:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": { "legwork": { "type": "local",
    "command": ["uvx", "legwork-mcp", "hub", "--allow-read", "~/Downloads"] } },
  "permission": { "legwork_install_tool": "ask" }
}
```

- **You approve every install:** your client shows the repo and the
  folders it asks for.
- **The hub's flags are the ceiling.** An install can ask for the folders
  you allowed or less, never more, and no network unless you started the
  hub with `--allow-net`. Secret folders (`~/.ssh`, `~/.aws`, ...) are
  refused outright.
- **Installed tools appear by name** (`pdfplumber__extract_tables`), and
  `use_tool` works in clients that don't refresh their tool list. Installs
  are remembered across restarts.
- **Search results tell your AI what matters:** stars, license, how recently
  the repo was updated, whether it's in the Legwork cache, whether it
  already has an MCP server, and a warning on very new repos (the malware
  we caught was three days old). Descriptions are marked as untrusted text.

**37 tools are in the [public cache](cache/) and install in about a minute
with no API key**: speech-to-text, OCR, background removal, PDFs and
Office files, video, YouTube transcripts, charts, DuckDB and CSV tools, maths
and units, and linters and formatters. Each was built, reviewed by hand and
tried with a real call before it was added, and pins the version of the
package it wraps (its dependencies resolve at install). The cache a
release reads is the one tagged with that release, so a change to the cache
reaches you only with an upgrade. Others need a model key, and building takes 1–5 minutes. Put the key
in `~/.legwork.env` ([three lines](#model-providers), `chmod 600`) and the
hub reads it when it needs to build, so the key never goes in your MCP
config, where it would sit in plain text.

`legwork find "extract tables pdf"` runs the same search from your terminal.

- **Tools that use the internet** (YouTube transcripts and downloads, web
  pages) need the hub started with `--allow-net`; without it they install,
  then can't connect.
- **Files a tool makes** (a trimmed video, a chart, a QR code) are copied
  to `~/Legwork/outputs/<tool>/`, and the reply says where; images also
  come back inline. Your granted folders stay read-only, so a tool can't
  write next to your files. The copies are never executable, and on macOS
  they get the same quarantine flag as downloads. `--outputs DIR` puts
  them elsewhere, `--outputs off` turns copying off.
- **GitHub allows 10 searches a minute without a token**, and each
  `find_tools` uses 2 to 4. Cached tools still show up when GitHub says no.
  For 30 a minute, add `GITHUB_TOKEN=<a GitHub token with no scopes>` to
  `~/.legwork.env`.

### Other MCP clients

Legwork is a standard MCP server over stdio, so any client that runs local
MCP servers can use it. What differs is whether the client asks you before
it calls a tool. Legwork labels its tools (searching only reads; installing
acts), and the clients marked "asks" use those labels.

| Client | Tested | Add Legwork | Before an install |
|---|---|---|---|
| Claude Code | ✅ | `claude mcp add legwork -- uvx legwork-mcp hub --allow-read ~/Downloads` | asks |
| Claude Desktop | ✅ | JSON above | asks |
| Cursor | ✅ | JSON above | its default Auto-review mode lets an AI decide, and ran installs without asking in our test; to approve every install, set Settings → Agents → Approvals & Execution to Allowlist and leave `install_tool` off the list |
| opencode | ✅ 1.18 | JSON above | asks only with the `permission` line above |
| Codex CLI | ✅ 0.159 | `codex mcp add legwork -- uvx legwork-mcp hub --allow-read ~/Downloads` | asks |
| Goose | ✅ 1.52 | `goose session --with-extension "uvx legwork-mcp hub --allow-read ~/Downloads"` | asks with `GOOSE_MODE=smart_approve`; the default mode doesn't |
| OpenClaw | ✅ 2026.9 | `openclaw mcp add legwork --command uvx --arg legwork-mcp --arg hub --arg --allow-read --arg ~/Downloads` | its default full-permission mode doesn't ask |
| Gemini CLI, Cline, Zed, Continue, ... | not yet | the same command in the client's MCP config | check the client's settings |

In every client, the folders and network you start the hub with stay the
ceiling, whether or not the client asks.

**Faster starts:** `uvx` downloads Legwork on the first run, which can be
slower than some clients wait (Codex allows 10 seconds; add
`startup_timeout_sec = 60` under `[mcp_servers.legwork]` in
`~/.codex/config.toml`). Or install it once with `uv tool install
legwork-mcp` and use the full path that `which legwork` prints, plus
`hub ...`, as the command (desktop apps don't see your shell's `PATH`);
`uv tool upgrade legwork-mcp` updates it.

## Quick start

You need **macOS or Linux** and [uv](https://docs.astral.sh/uv/). Tools in
the [public cache](cache/) need nothing else. For any other repo you also
need a model key for an OpenAI-compatible chat-completions endpoint (see
[Model providers](#model-providers)). Legwork itself is free; the only cost
is your own model usage, which is 1–3 calls per build.

<details>
<summary><b>Linux:</b> install bubblewrap (and one step on Ubuntu 24.04+)</summary>

The Linux sandbox is [bubblewrap](https://github.com/containers/bubblewrap):

```sh
sudo apt install bubblewrap python3-venv   # Debian/Ubuntu
sudo dnf install bubblewrap                # Fedora
```

Ubuntu 24.04 and later block the user namespaces bubblewrap needs, unless a
program's AppArmor profile allows them. Allow them for `bwrap` only (this
is what Ubuntu does for Chrome and Flatpak), rather than switching the
restriction off system-wide:

```sh
sudo tee /etc/apparmor.d/bwrap <<'EOF'
abi <abi/4.0>,
include <tunables/global>

profile bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,
  include if exists <local/bwrap>
}
EOF
sudo apparmor_parser -r /etc/apparmor.d/bwrap
```

If anything's missing, Legwork stops and prints these instructions.
</details>

**Tested on every commit:** macOS on Apple silicon and Intel; Ubuntu on
x86-64 and ARM64; Fedora; Debian. **Windows:** not supported natively.
WSL2 should behave like Ubuntu (install bubblewrap) but hasn't been tested.

A repo in the [public cache](cache/) needs no model and no API key:

```sh
uvx legwork-mcp jsvine/pdfplumber
```

It installs and tests the cached wrapper in about a minute, then prints the
line to connect it:

```
Installed the cached MCP wrapper for jsvine/pdfplumber; it passed its self-test here.
  What it wraps: Extract text, tables, and low-level page objects from local PDFs using pdfplumber's Python API

Connect it to Claude Code:
  claude mcp add pdfplumber -- ~/.local/bin/uvx legwork-mcp serve jsvine/pdfplumber
```

For any other repo, Legwork has a model write the wrapper (1–5 minutes,
1–3 model calls on your own key; see [Model providers](#model-providers)):

```sh
export LEGWORK_LLM_ENDPOINT=https://api.openai.com/v1
export LEGWORK_LLM_API_KEY=...        # your own key; never a CLI flag
export LEGWORK_LLM_MODEL=gpt-5

uvx legwork-mcp owner/repo
```

It also prints an `mcpServers` block for Claude Desktop, Cursor and other
clients. For Claude Desktop, add it to `claude_desktop_config.json` **while
the app is quit**: the running app writes its settings back on exit and
drops edits it didn't make. For a permanent `legwork` command: `uv tool
install legwork-mcp`.

**Upgrading:** `uvx` keeps using the version it first downloaded. To get
fixes (and the cache that ships with them), use `uvx legwork-mcp@latest`
once, or `uv cache clean legwork-mcp`. `legwork doctor` warns when a client
is pinned to an old version.

**The name:** the package is `legwork-mcp`. `pip install legwork` and
`uvx legwork` install an unrelated astronomy package.


## Model providers

Any OpenAI-compatible chat-completions endpoint works. Put three lines in
`~/.legwork.env` and make it private with `chmod 600 ~/.legwork.env`; Legwork
reads it whenever the variables aren't set in your environment (and refuses
it if other users can read it):

```sh
LEGWORK_LLM_ENDPOINT=https://api.openai.com/v1
LEGWORK_LLM_API_KEY=sk-...
LEGWORK_LLM_MODEL=gpt-5
```

| Provider | `LEGWORK_LLM_ENDPOINT` | `LEGWORK_LLM_MODEL` (tested) | Cost |
|---|---|---|---|
| OpenAI | `https://api.openai.com/v1` | `gpt-5` | Your API credits |
| Anthropic | `https://api.anthropic.com/v1` | `claude-sonnet-5` | Your API credits |
| OpenRouter | `https://openrouter.ai/api/v1` | `nvidia/nemotron-3-super-120b-a12b:free` | Free models, rate-limited |
| Ollama (local) | `http://127.0.0.1:11434/v1` | `gpt-oss:20b` | Free; 14 GB download, ~16 GB RAM |

`LEGWORK_LLM_API_KEY` is your key (for Ollama, any non-empty value).

**Ollama:** start it with a larger context window, or it silently cuts
Legwork's prompt to ~2,000 tokens and the model never sees the
instructions: `OLLAMA_CONTEXT_LENGTH=32768 ollama serve`. Legwork prints
this tip when it detects Ollama.

**OpenRouter free models** come and go and are often busy (HTTP 429). If
one keeps failing, pick another from
[their list](https://openrouter.ai/models?max_price=0).

**Measured 2026-09-27**, same four repos on each (reverify, golive-skill,
AnyJev, phone-harness), counting wrappers built:

| Model | Built | Notes |
|---|---|---|
| gpt-5 | 4 / 4 | all on the first attempt |
| Nemotron 3 Super (OpenRouter, free) | 3 / 4 | |
| gpt-oss:20b (Ollama, local) | 3 / 4 | slowest: 1–3 min per reply on an M5 |
| Claude Sonnet 5 | 1 / 4 | the most cautious: refused three, citing third-party accounts, a paired iPhone, and "a research pipeline" |

A refusal isn't a crash: Legwork reports the model's reason and stops
without installing anything.

## How it works

0. **Cache.** If the repo is in the [public cache](cache/), Legwork uses
   that wrapper instead of steps 3–4. It still clones and scans the repo,
   scans the cached wrapper too, and installs and self-tests it locally;
   if the self-test fails, it writes a fresh wrapper. `--no-cache` skips
   the cache.
1. **Fetch.** Checks the repo is public and reachable, then shallow-clones it.
2. **Scan.** Statically scans every Python and JavaScript/TypeScript file
   for obfuscated payloads: XOR-decoded byte arrays, computed imports,
   javascript-obfuscator output, `eval` of decoded strings, code hidden
   off-screen behind whitespace. A hit stops everything before install.
3. **Read.** The README, plus setup docs it links to (`install.md`,
   `docs/getting-started.md`, …), capped at 50KB with install and usage
   sections kept first.
4. **Write.** Your model writes the install command and a Python MCP
   wrapper with a self-test, or refuses with a specific reason: no
   programmatic entrypoint, needs hardware, needs its own credentials,
   needs a toolchain the sandbox doesn't have, and so on.
5. **Install**, sandboxed, network on.
6. **Self-test**, sandboxed, network off. A failure goes back to the model
   and it tries again, up to 3 attempts and 30 minutes. Attempts share one download cache, so a retry doesn't re-download multi-GB dependencies like PyTorch.
7. **Serve.** `legwork serve owner/repo` runs the wrapper as an MCP server
   over stdio, sandboxed, network off.

## Permissions: locked down unless you say so

A served tool **can't read your home folder and has no network** until you
grant them. Grant exactly what a tool needs when you add it to your client:

```sh
# Build it once (cached: no key needed), then let it read one folder (read-only):
uvx legwork-mcp microsoft/markitdown
claude mcp add markitdown -- uvx legwork-mcp serve microsoft/markitdown --allow-read ~/Downloads

# A tool that fetches web pages:
uvx legwork-mcp serve owner/repo --allow-net
```

`--allow-read` is repeatable and always read-only. Some folders can't be
granted even on request: `/`, your whole home folder, and a list of known
credential locations (`~/.ssh`, `~/.aws`, `~/.config`, `~/.gnupg`, macOS
keychains and browser data, Linux keyrings and Firefox profiles,
`~/.npmrc`, `~/.pgpass`, `~/.legwork.env`, shell history and startup files,
AI clients' configs), and any folder that contains one of those (such as
`~/Library`). It's a list, so it can miss a place your setup keeps secrets.

**A granted folder is readable in full**, including any `.env` file or key
inside it: grant `~/Downloads`, not your code folder. A tool with a folder
*and* `--allow-net` could send what it reads to the internet, so grant both
only to a tool that needs both. With `--allow-net`, the tool can reach
services on this machine (`localhost`) but not local sockets such as your
SSH agent or Docker.

Outside your home folder, tools can read system locations such as `/usr`,
`/opt`, `/etc`, `/tmp`, macOS's per-user temporary folder, `/Users/Shared`
and external drives under `/Volumes`, because Python and the libraries they
need live there. Don't keep secrets in those places. (On Linux, `/tmp` is a
private empty one.)

On macOS, the first time a tool reads a protected folder (Downloads,
Documents, Desktop), macOS itself asks whether `uvx` may access it. That's
the operating system's own privacy check, on top of Legwork's grant; allow
it once.

## What "built" means

The self-test calls the **simplest** documented command and checks the
shape of its result. So "built" means the wrapper starts and its simplest
tool works. It does not mean every tool works. In the trial:

- golive-skill's wrapper exposes its read-only commands, not its deploy
  flow, which needs accounts.
- AnyJev's decision tool needs a running vLLM server, which the self-test
  didn't have.

Tools that must reach the network at run time won't work under `serve`,
because the network is off.

## Risks, stated plainly

Legwork runs code you didn't write: the target repo's install steps and a
wrapper an LLM wrote from a README a stranger wrote. What contains it:

- **Install time is the biggest risk.** Package installs and `install.sh`
  scripts run with network on, because they have to. The sandbox limits
  them to their own working folder: no access to your home directory (SSH
  keys, cloud credentials, dotfiles; system locations such as `/tmp` stay
  readable), no writes outside the folder, and an
  environment with none of your variables (including your model key). A
  malicious package can still misbehave inside that folder and reach the
  network during install.
- **The scan covers the repo's source, not what installers download.**
  magpie, for example, installs with the vendor's `curl … | sh`. For
  downloaded code, the sandbox is the protection.
- **Prompt injection is not mitigated.** A README can steer the model into
  writing a wrapper that does something other than what you asked for. The
  wrapper still runs sandboxed with network off, which bounds the damage;
  it doesn't prevent a wrong or misleading tool.
- **Linux on architectures other than x86-64 and ARM64:** install code
  can reach "abstract" Unix sockets (such as an X11 display), because the
  seccomp filter that blocks Unix sockets during install only covers those
  two. The run phase has no network and isn't affected.
- **macOS can't stop a process that detaches itself.** Each install and
  self-test is cleaned up when it finishes, but on macOS code that
  deliberately detaches (`setsid`) can keep running afterwards. It stays in
  the sandbox (no home folder, no writes outside its own folder) but keeps
  the install step's network access until you log out. Linux's sandbox
  stops these too.
- **Keep your key file in your home folder** (`~/.legwork.env`, the
  default). Sandboxed code can't read your home folder, but it can read
  most places outside it.
- **Installs on Linux can reach services on this machine** (`localhost`
  databases, dev servers): bubblewrap shares the host's network during
  install. macOS blocks them. Unix sockets are blocked on both.
- **Not yet:** `legwork serve` used on its own passes along anything a tool
  prints by mistake, which can confuse a client (the hub filters it out).
- **The scan is an obfuscation tripwire, not a malware scanner.** It
  catches the hiding patterns seen in real payloads so far, and a
  determined author can get past it. The sandbox is the protection.

The sandbox is `sandbox-exec` on macOS and bubblewrap on Linux, with the
same rules on both: the system read-only, your home directory hidden,
writes only in the build's own folder, no local sockets (SSH agent, Docker,
display servers), only the system services builds need, and network only
during install. If the sandbox isn't available, Legwork
refuses to run. It never falls back to running unsandboxed.

[THREAT_MODEL.md](THREAT_MODEL.md) has the full table of what a tool can
reach in each phase, a comparison with Docker and VMs, and every escape
found so far with the test that keeps it fixed.

## Commands

| Command | What it does |
|---|---|
| `legwork hub [--allow-read PATH] [--allow-net]` | One MCP server through which your AI finds, installs and uses tools, within the limits you set |
| `legwork find "keywords"` | Search GitHub for tools that do something, with facts to choose by |
| `legwork owner/repo` | Build a wrapper (also `legwork build`), from the cache when possible. Accepts `owner/repo` or a github.com URL. `--no-cache` always writes a fresh one |
| `legwork serve owner/repo` | Run the built wrapper as an MCP server over stdio. Needs no model key. `--allow-read PATH`, `--allow-net` grant access (see Permissions) |
| `legwork contribute owner/repo [--out DIR]` | Write the build as a public-cache entry, ready for a PR (below) |
| `legwork doctor` | Check this machine is ready: sandbox, cache, key, search limit, client configs. Read-only; prints no keys. Paste it into bug reports |
| `legwork clean [owner/repo]` | Free disk space: remove old and failed builds, keep the ones you serve. `--dry-run`, `--all` |

Builds live in `~/.legwork` (override with `LEGWORK_HOME`). A failed build
saves its full error output to `attempts.log` in its build folder. Each
build keeps its own environment, which is several GB for repos that use
PyTorch: `legwork clean` removes old and failed builds and keeps the ones
you serve (`--dry-run` to preview, `--all` to remove everything).

## Contributing a wrapper

`legwork contribute owner/repo` writes `cache/<owner>__<repo>/` containing
`wrapper.py` and `manifest.json`, and prints a PR description. Before
writing anything, it:

- blocks the entry if the wrapper copies 50+ words in a row from the source repo
- re-runs the self-test in the sandbox
- blocks the write if any output contains something shaped like an API key,
  including your own configured key

A GPL, AGPL, missing or unrecognized source license is flagged in the
manifest and the PR description, but not blocked. If the repo has commits
newer than the build or the existing entry, you get a warning. Open the PR
yourself; a bad entry is removed with a plain `git revert`.

## Status

Early. What's next:

- Better multi-tool verification than a single simplest-command self-test.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md). Security reports: [SECURITY.md](SECURITY.md).

```sh
uv sync
uv run pytest        # sandbox tests need macOS, or Linux with bubblewrap
```

`tests/fixtures/ai_data_extractor_payload.py` is a real malicious file with
its payload destroyed (every encoded byte randomized, code shape kept), so
the scanner is tested against a real technique. Tests only parse it with
`ast`; see `tests/fixtures/README.md`.

## License

MIT
