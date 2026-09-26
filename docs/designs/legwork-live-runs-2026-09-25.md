# Legwork live runs — 2026-09-25

First runs of the full pipeline against a real model (`gpt-5` via OpenAI's
chat-completions endpoint). Before this, every test mocked the model's
reply. Three repos from the validation spike.

## Final results

| Repo | Expected | Result |
|---|---|---|
| `2akouwu/reverify` | working wrapper | ✅ working FastMCP wrapper on attempt 2 (148.8s, 2 model calls) |
| `ValerianXXX/JobFlow` | refusal: no CLI/API | ✅ `NO_PROGRAMMATIC_ENTRYPOINT` on attempt 1 (20.5s) |
| `ShawnPana/phone-harness` | refusal: needs hardware | ⚠️ refused, but as `INSUFFICIENT_README` — its README defers install steps to `install.md`, which Legwork doesn't send |

Every model reply followed the required reply format — no parse failures.

## What the runs broke, and the fixes (commits `0d373eb`, `53d6220`)

1. **Venv built on Python 3.9.** macOS's `/usr/bin/python3` is 3.9, where
   pip finds no installable version of the MCP SDK. The venv now uses the
   first Python 3.10+ outside `$HOME`.
2. **60s model timeout too short.** gpt-5 took ~45–60s per reply, so calls
   kept timing out and silently retrying (one took 178s). Codegen calls now
   get 300s.
3. **MCP SDK is 2.x; models write 1.x.** mcp 2.x renamed `FastMCP` to
   `MCPServer`. Legwork now installs `mcp<2` itself, after the model's own
   install command, and the prompt gives the exact 1.x import.
4. **Latest-failure-only retries repeated old mistakes.** Attempt 3 redid
   attempt 1's error because it only saw attempt 2's. Retries now carry the
   latest failure in full plus a one-line summary of each earlier one.
5. **Self-tests too ambitious.** The model tested showcase features and
   asserted on the tool's own verdicts. The prompt now asks for the
   simplest command and a shape-only check.
6. **Scanner false positive on phone-harness.** An ordinary proxy class
   (`getattr(self._wrapped, name)`) plus the documented `exec()` of the
   script it runs tripped the two-weak-signals rule. `getattr()` /
   `__import__()` now count only when the name is built inline, not passed
   in through a variable. The real malicious fixture is still blocked. Known
   trade-off: decoding a name into a variable first dodges this one signal.

## Not Legwork's to fix

reverify's README example `reverify verify - --claim ...` crashes in its
released package (`bytes.fromhex("-")`). The model followed the docs
exactly; on retry it worked around it with a dummy hex input.

## Follow-up: command + serve mode (2026-09-26, commit `c3f9dcb`)

`legwork <repo>` builds and saves a wrapper under `~/.legwork`, then prints
the `claude mcp add ...` line and an `mcpServers` config block.
`legwork serve <repo>` runs it as an MCP server over stdio in the sandbox
(network off, no timeout); Legwork's own launcher finds the FastMCP object,
so the model never has to write startup code.

Live-verified with gpt-5: built on attempt 2, then a real MCP client
connected to `legwork serve 2akouwu/reverify`, listed `re_verify_claim`,
and called it with reverify's README example — `VERIFIED` (eax = 8),
executed inside the served sandbox.

Found along the way: a workdir under `$HOME` broke Python startup in the
sandbox (it stats every parent folder resolving its own path). The profile
now allows metadata, not contents, on exactly those ancestor folders.

## Open follow-ups

- ~~**The printed launch path is this checkout's venv**~~ — fixed by T9:
  installed as `legwork-mcp`, it prints the installed command or a `uvx`
  line.
- ~~**README-only input.**~~ Fixed 2026-09-26, see below.

## Follow-up: linked setup docs (2026-09-26)

The prompt now carries setup docs the README links to (or that sit at the
repo root) when their name or link text looks like setup — at most 3,
inside the same 50KB budget, README first.

Re-run of `ShawnPana/phone-harness` with gpt-5: `install.md` was included
and the build **succeeded on attempt 2** instead of being refused. Not the
`EXTERNAL_HARDWARE_REQUIRED` refusal we originally expected, and worth
being precise about: the wrapper exposes three tools. `skill` and
`config_show` need no phone, and the self-test passed on `skill`.
`run_script`, the one that actually drives a phone, needs a connected
device and was **not** verified — the self-test only ever checks the
simplest command. So "built" means "starts and its simplest tool works",
not "every tool works". The README should say so.

The same run caught the printed launch command being relative
(`.venv/bin/legwork`) when legwork is started by a relative path; it's now
made absolute.
