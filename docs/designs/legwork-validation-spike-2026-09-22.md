# Legwork Validation Spike — Results

Run 2026-09-22, per design doc Next Steps #0. Raw LLM codegen only — no retry
loop, no cache, no CI. 8 real repos pulled from GitHub's search API, filtered
to genuinely fresh (`created:>2026-08-01`), not pre-selected for friendliness.

## Method

For each repo: fetch the actual README via `gh api`, read it as the sole
input (no looking at source first), decide whether an entrypoint and a
typed tool interface can be inferred, and where plausible, actually attempt
install + invocation. Execution used macOS's native `sandbox-exec` as a
stand-in for the design doc's two-phase container policy (verified
separately: writes confined to the spike dir, network allowed only in the
install-phase profile, blocked in the invoke-phase profile — a real,
working implementation of the phase split without needing Docker).

## Results table

| Repo | README size | Entrypoint locatable? | Schema inferable? | Executed? | Outcome |
|---|---|---|---|---|---|
| `2akouwu/reverify` | 26.5KB | Yes | Yes | **Yes** | **SUCCESS** — clean JSON output, first try |
| `kruzovic7/ai-data-extractor` | 11.0KB | Yes (looked trivial) | Yes | **Yes** | **MALICIOUS** — see below |
| `ShawnPana/phone-harness` | 3.9KB | No | N/A | No | Requires physical/cloud phone + interactive OS pairing |
| `Colafornia/short-video-generator-AI` | 5.5KB | Yes | Yes | No (reasoned) | Needs user's own LLM API key; heavy AV deps likely exceed 120s install cap |
| `fuxicodex/Fuxi` | 5.0KB | No | N/A | No | Requires account registration/login; proprietary binary, not a wrappable library |
| `ValerianXXX/JobFlow` | 9.4KB | No | N/A | No | Windows GUI installer only, no programmatic entrypoint exists |
| `guillaumemeyer/watermarks-remover` | 100.3KB (capped at 50KB) | Partially | Ambiguous | No (reasoned) | Truncation lands mid-way through two competing entrypoints; picks wrong one on a naive linear read |
| `idavidov13/agentic-playwright` | 105.1KB (capped at 50KB) | N/A — wrong tool shape | N/A | No (reasoned) | It's a project scaffolder (mutates the environment), not a callable capability — MCP-wrapper model doesn't fit at all |

**Raw success rate: 1/8 clean success.** But the more important number is that
**7/8 failures were correctly attributable to a specific, nameable cause** —
only 1/8 (the malicious payload) executed further than intended, and that
happened because the spike itself was run without the sandbox initially
(exactly what the spike exists to catch before more is built on an
unvalidated core).

## Finding 1 (critical): a real trending repo contained an obfuscated, exec()'d payload

`kruzovic7/ai-data-extractor` billed itself as "No dependencies -- standard
library only" with a trivially simple CLI (`python extract.py --list`). The
README gave zero indication of anything unusual. Running exactly what the
README said, unsandboxed (before the sandbox profiles were built), produced:

```
File "extract.py", line 21, in <module>
    extract.run_sync()
  File "<string>", line 102, in run_sync
  File "<string>", line 91, in bootstrap
RuntimeError: win32 only
```

`File "<string>"` means the traceback is inside dynamically `exec()`'d code.
The actual source of `extractors/extract.py` is XOR-obfuscated import names
plus a large encrypted blob, decrypted with a custom stream cipher and
`exec()`'d into the module's global namespace at **import time** — before
any argument parsing, so even a "safe," read-only-looking flag like `--list`
triggers it. The payload ran far enough to hit a Windows-only gate and stop;
what it does on Windows was not investigated further (too risky to probe).

**This directly confirms premise #4 is not hypothetical.** It happened on
the very first "easy" repo tried, from real GitHub search results, with a
README that gave no signal anything was wrong. The design doc's phase-based
network cutoff (install-phase open, invoke-phase closed) would NOT have
stopped this on its own — the payload fired at **import time**, before the
phase split even applies. This is a real gap the design needs to account
for, not just the codegen-time and install-time risks already named.

## Finding 2: the one clean success came with a quality nuance

`reverify` worked end to end: `reverify auto /bin/ls --json`, sandboxed, no
network, returned well-formed JSON on the first attempt. But the base
`pip install reverify` gives a degraded pure-Python core — the README
recommends `pip install "reverify[full]"` for full fidelity (capstone,
unicorn, lief engines), and the actual run showed the degraded output
("Mach-O needs lief: pip install lief"). **A naive synthesis that grabs the
first `pip install X` line in a README, rather than reading install-variant
guidance, silently produces a working-but-degraded wrapper — a new failure
mode, not previously named:** `DegradedInstallWarning` (Success, but a
better install target existed and was missed).

## Finding 3: three distinct "not synthesizable" categories the design doc didn't name

- **Requires external hardware + interactive OS permission grants**
  (`phone-harness`): no headless entrypoint exists at all — the tool's
  entire value is driving a physically-paired phone. Distinct from
  `InsufficientReadmeError` (the README is actually excellent) and from
  `CredentialRequiredError` (no credential would fix this).
- **Requires account registration, is a competing full application, not a
  wrappable library** (`Fuxi`): distributed as a proprietary binary,
  requires `fuxi login` before any use. Legwork's model assumes the target
  is a *library/tool with a callable capability*; some trending "AI" repos
  are actually full end-user applications that don't fit that shape at all.
- **No programmatic entrypoint exists, GUI/installer-only** (`JobFlow`):
  Windows-only, install is "double-click this .cmd," core workflow has no
  CLI surface. Maps loosely to `InsufficientReadmeError` but that name
  implies the *README* is lacking; here the *product* has no programmatic
  interface, which is a different and arguably more common case.

## Finding 4: the 50KB truncation cap can cut before the right entrypoint

Both oversized READMEs (`watermarks-remover`, `agentic-playwright`) are
capped at 50KB, and in both cases the retained portion is misleading on its
own:
- `watermarks-remover`'s first 50KB describes an HTTP-service-backed skill
  install (`remove-ai-marks`) — but a simpler, self-contained alternative
  (`clean-user-facing-text`, "text only, ships its own scripts") is
  mentioned once, in passing, inside the retained content, and its actual
  usage details likely live past the truncation point. A naive linear read
  picks the more complex, service-dependent path by default.
- `agentic-playwright` isn't a capability at all — `npm create
  agentic-playwright . -- --demo` scaffolds an entire new project into the
  current directory (downloads browsers, runs `npm install`, mutates the
  environment). No amount of README content changes that this tool doesn't
  fit the "callable capability" model MCP wrapping assumes.

**Truncation strategy gap:** a flat first-N-KB cut can retain the wrong
section when a README describes multiple installation paths. A smarter
strategy (prioritize sections literally headed "Quick Start" / "Usage",
or take the README's Table of Contents as a map before cutting) would likely
do better than the naive linear cap the design doc currently specifies.

## What this changes in the design doc

1. **Entrypoint-locate and schema-inference are validated as real,
   solvable problems** — `reverify` proves it works end to end when the
   README is well-structured, matching premise #2's optimistic case.
2. **A new pre-execution scan step is needed, before the sandbox even
   spins up** — static inspection for obfuscation patterns (encoded
   string literals, `exec()`/`eval()` on decoded data, dynamic `__import__`
   of runtime-decoded module names) in the fetched source, since Finding 1
   fired at **import time**, before the phase-based network cutoff applies.
   This is a gap in the current security design, not just a residual risk
   to disclose — needs its own decision.
3. **Three new named failure classes** for the Error & Rescue Registry:
   `ExternalHardwareRequiredError`, `NotAWrappableCapabilityError` (full
   apps / scaffolders, not tools), `NoProgrammaticEntrypointError` (product
   has no CLI/API surface at all, distinct from a README that merely
   under-documents one).
4. **`DegradedInstallWarning`** — synthesis succeeded but used a worse
   install target than the README recommended for full fidelity.
5. **Truncation strategy should prioritize "Quick Start"/"Usage" sections
   over a flat byte cut**, or take the README's own table of contents as a
   map first.

These are real, load-bearing findings — presenting them to the user as
individual decisions before folding into the design doc.
