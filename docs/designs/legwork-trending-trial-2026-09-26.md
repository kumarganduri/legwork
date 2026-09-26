# Legwork trending-repo trial — 2026-09-26

The launch check: run `legwork build` against this week's top trending AI
repos, unfiltered, and count what happens. Model: gpt-5.

**Selection:** GitHub search `ai OR llm OR agent OR mcp created:>2026-09-19
fork:false archived:false`, sorted by stars, top 10 taken as-is — no repo
skipped for being hard. Several are Android, Swift or Go apps.

## Results

| # | Repo | ★ | Outcome | Time |
|---|---|---|---|---|
| 1 | zai-org/ZCode | 6.8k | Refused: `NOT_A_WRAPPABLE_CAPABILITY` (interactive desktop/web app needing a Node build) after one failed attempt | 78s |
| 2 | jev-chat/jev-chat-jarvis | 6.6k | Refused: `NO_PROGRAMMATIC_ENTRYPOINT` (Android app) | 12s |
| 3 | unreallabsai/unreal-agent | 1.9k | Refused: `INSUFFICIENT_README` — correct, the README is an architecture glossary with no install or usage steps anywhere | 7s |
| 4 | mikehasa/golive-skill | 956 | ✅ **Built**, attempt 2. npm CLI; `docs/DISTRIBUTION.md` pulled in by the linked-docs change. Tools: version, detect, help | 88s |
| 5 | yetone/magpie | 823 | ❌ **Failed**: 3× `DependencyInstallError`. Go isn't installed on this machine and the other route is `curl … \| sh` into `$HOME`, which the sandbox blocks | 161s |
| 6 | freestylefly/WeChatBridge | 751 | Refused: `NO_PROGRAMMATIC_ENTRYPOINT` (macOS GUI app) | 9s |
| 7 | nokia-applied-research/AnyJev | 745 | ✅ **Built**, attempt 1. Tool: `decide_choice_vllm` | 123s |
| 8 | asokurasu/text-humanizer | 721 | 🛑 **Blocked: obfuscated payload** | 1s |
| 9 | yukitorido/short-video-generator-AI | 713 | 🛑 **Blocked: obfuscated payload** | 1s |
| 10 | jev-chat/jev-chat-windows | 569 | Refused: `CREDENTIAL_REQUIRED` (needs two third-party API keys) | 15s |

**Scorecard:** 9 of 10 ended correctly (2 built, 5 refused with a right
reason, 2 malware blocked). 1 failure (magpie). Total model spend was
roughly 15 calls.

Both built wrappers were then served with `legwork serve` to a real MCP
client: golive's `golive_version` returned the real version JSON inside the
sandbox; AnyJev listed its tool.

## Two of the top 10 were malware

Repos 8 and 9 — different owners, different names, both created
2026-09-23 with ~700 stars each — contain the **byte-identical** 28,649-byte
file (sha256 `49934c28…`), as `src/services/humanizer.py` and `src/fs.py`.
It's the same template as the `ai-data-extractor` payload from the
validation spike, with the names re-randomised: XOR-hidden imports that
decode (statically, never run) to `hashlib`, `zlib`, `hmac`, an embedded
blob decrypted with an HMAC-SHA256 keystream and decompressed — a dropper.
The scanner flagged all four patterns and stopped both before install, in
about a second. Nothing from either repo was installed, imported or run;
the payload was not decrypted.

Takeaway for the README: star counts on trending AI repos are not a trust
signal, and the pre-install scan is doing real work, not theatre.

## What "built" means — be precise in the README

The self-test calls the simplest command and checks only the shape of its
result. So "built" means the wrapper starts and its simplest tool works:

- golive: the deploy flow (the tool's point) isn't exposed — it needs
  accounts. The wrapper covers the read-only commands.
- AnyJev: the self-test only built a `Question` object; the tool itself
  needs a running vLLM server, which the trial didn't have.

## Fixes this trial calls for

1. **Failure summaries hide the error.** Each attempt line is cut at 200
   characters, which for install failures is all command and no error.
   Show the end of the error, where the cause is.
2. **Tell the model which toolchains exist.** magpie needed Go, which isn't
   installed; the model spent 3 attempts on installs that couldn't work.
   Listing what's available (Python, Node/npm, …) lets it refuse at once.
