# Legwork across model providers, 2026-09-27

Before this, only OpenAI's gpt-5 had been tested, while the README said
"any OpenAI-compatible endpoint". Same four repos on each provider:
reverify, golive-skill, AnyJev, phone-harness. `--no-cache`, so each
model wrote its own wrappers.

## First pass (Legwork 0.4.1)

| Model | Built | What went wrong |
|---|---|---|
| Claude Sonnet 5 | 1/4 | reverify: the wrapper worked, but its self-test assumed the output was a dict (it's a list) and failed it, three times. golive: `npm install -g` (blocked by the sandbox), then a self-test that hung on the network |
| gpt-oss:20b, Ollama | 0/4 | **Ollama cut every prompt to 2,050 tokens** (prompts were 4.4k–12.4k), so the model never saw the instructions. With `OLLAMA_CONTEXT_LENGTH=32768`: 2/4, same `npm -g` and hang issues as Claude |
| Nemotron 3 Super, OpenRouter free | 1/4 | reverify self-test asserted the tool's verdict. AnyJev: an upstream 503 arrived as HTTP 200 with an error body and **used up a wrapper-repair attempt** |

Every provider's API worked with Legwork's client; the failures were
Legwork's prompt and error handling, and would hit any model.

## Fixes (commit `bc92cce`, released 0.4.2)

- Self-test instructions: network is off and there's a 60s limit, so call
  something offline and check only that it returned something; never
  assert a guessed type or the tool's own verdict.
- Install instructions: npm packages locally (`./node_modules/.bin`), never `-g`.
- HTTP 200 carrying an error body is retried as infrastructure.
- A reply in the wrong shape says exactly what's missing; every raw reply
  is kept as `model-reply.md`.
- Legwork prints the Ollama context-window tip when it detects Ollama.

## Second pass (with the fixes)

| Model | Built | Model calls | Refusals |
|---|---|---|---|
| gpt-5 | 4/4 | 4 (all first attempt; before: 6) | none |
| Nemotron 3 Super (free) | 3/4 | 5 | AnyJev: "no offline method documented" |
| gpt-oss:20b (local) | 3/4 | 5 | golive: "only a Node.js CLI" (wrong: a CLI is callable) |
| Claude Sonnet 5 | 1/4 | 5 | golive (needs third-party accounts), phone-harness (needs a paired iPhone), AnyJev ("a research pipeline") |

Claude's refusals are defensible but cautious; the README says so rather
than tuning the prompt against one model.
