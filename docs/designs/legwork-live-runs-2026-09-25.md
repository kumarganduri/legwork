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

## Open follow-ups

- **Wrappers can't be served yet.** `__main__` runs the self-test only;
  nothing calls `mcp.run()`, so an agent can't actually connect to the
  wrapper as an MCP server.
- **README-only input.** phone-harness shows repos that put install steps
  in linked files (`install.md`) get refused. The full repo is already
  cloned, so linked docs could be included.
