# Second trending-repo trial: 22 repos, two models, 2026-09-27

The first trial had 10 repos. This one takes the next 22 most-starred new
AI repos (GitHub search, created in the last 7 days, the 10 already tested
excluded, nothing else filtered) and builds each with **gpt-5** and with
OpenRouter's free **nvidia/nemotron-3-super-120b-a12b:free**, `--no-cache`.

## What the trial found in Legwork (all fixed the same day)

1. **Two false malware alarms.** The scanner blocked a 3.8 MB vendored Vue
   chunk (a base64 blob + `eval(`, two weak signals) and a chiptune
   synthesizer (note tables + an LFSR's `^`). The XOR rule now needs XOR
   inside `bytes()`/`bytearray()`/`chr()`; in bundled or vendored JS only
   strong signals count. Both real malware samples still block; the
   22 repos' 928 Python and 5,866 JS/TS files are otherwise clean
   (`c5afae6`).
2. **Installs couldn't see the repo.** The clone sat beside the sandboxed
   attempt folder, so "clone, then `pip install -r requirements.txt` /
   `npm install`" could never work: only PyPI/npm packages could. The
   source is now copied in as `./src` and the prompt says how to install
   from it. The prompt also names the OS (models assumed Linux on a Mac)
   and that `pip install uv` works (`2c4e4ee`). Re-running the 6 repos
   this touched: gpt-5 went from 1 built to 5; the free model from 0 to 3.

## Final results (with the fixes)

| | gpt-5 | Nemotron (free) |
|---|---|---|
| Built | **12** | **4** |
| Refused with a reason | 10 | 17 |
| Failed | 0 | 1 (OpenRouter rate limit, 429) |
| Blocked by the scanner | 0 | 0 |

gpt-5's refusals were all defensible: two course/guide READMEs, three
desktop apps, a Windows-only PowerShell installer, a Windows-exe/Rust
project, two prompt-only agent skills, a plugin for another host app, and
one README without a documented entry point. The free model refused far
more often, several times wrongly (e.g. "PyTorch needs a C compiler").

**"Built" varies in usefulness.** Served to a real MCP client, rizzo-flow
exposes its decision tool and threejs-architecture-effects a building
scaffolder, while knoweldge-base exposes only `initialize_database` and
jev-chat-jarvis-mac mostly edits its own config files. Running and
passing a self-test is what's verified, not that the tool is the one you
wanted.

Cost: about $6 of OpenAI credit for all gpt-5 runs; the free model cost
nothing.
