"""Prompt construction and response parsing for the LLM codegen step:
turning a README into either a working wrapper draft, or a specific,
named reason it can't be done.

Design doc: docs/designs/legwork-jit-ai-runtime.md, T6. This module — and
the entrypoint-locate error classes it defines — fill a gap in the
Implementation Tasks list: the pipeline in Next Steps #1 requires
"locate entrypoint" and "LLM-drafted MCP wrapper" steps that had no
dedicated task number of their own. Built as part of T6 (the retry loop
needs something concrete to retry), not silently assumed elsewhere.

Response contract asked of the LLM (see SYSTEM_PROMPT): either a fenced
```install``` block + ```python``` block + one-line ENTRYPOINT: description,
or a single REFUSAL: <CATEGORY> - <reason> line when no usable entrypoint
exists. Parsing is format-strict rather than freeform, so a malformed
response is a clear, testable LLMMalformedOutputError instead of silently
guessing at unstructured text.

**Not validated against a real LLM in this session** — no OpenAI-compatible
API key is available in this environment (same gap disclosed in T3's DONE
note). The prompt/parser contract here is fully unit-testable against
synthetic responses matching the documented format, but whether a REAL
model reliably produces that format for a REAL README is exactly the
question the validation spike answered by having Claude do this reasoning
directly during that spike — not by exercising this exact code path.
Flagged as real follow-up work: run this against a live endpoint once a
key exists, and expect to iterate on SYSTEM_PROMPT's wording based on what
a real model actually returns, the same way T4's heading matcher needed a
real-file fix after looking correct against synthetic tests alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from legwork.llm_client import ChatMessage, LLMMalformedOutputError

# Legwork installs this itself after the model's install command, so the
# wrapper always runs against the 1.x API the prompt describes. mcp 2.x
# renamed FastMCP to MCPServer and changed other APIs; current models write
# 1.x code (found in the live run, 2026-09-25). Revisit once models know 2.x.
MCP_SDK_PIN = "mcp<2"

SYSTEM_PROMPT = f"""You are Legwork's codegen step. You are given a GitHub \
repo's README and must either produce a working Python MCP (Model Context \
Protocol) tool wrapper for it, or explain precisely why one can't be built.

Respond in EXACTLY one of these two shapes. No other text outside them.

SHAPE 1 -- a usable entrypoint exists:

ENTRYPOINT: <one-line description of what you identified as the callable capability>

```install
<shell command(s) to install the target repo's own dependencies -- prefer \
the most complete install variant the README describes (e.g. an extras \
group like "package[full]") over a bare minimal install. Do NOT install, \
upgrade, or pin the `mcp` package: Legwork installs `{MCP_SDK_PIN}` itself.>
```

```python
<complete Python source for an MCP tool wrapper that exposes the identified \
capability as one or more typed MCP tools. Use the MCP Python SDK 1.x API \
exactly like this: `from mcp.server.fastmcp import FastMCP`, \
`mcp = FastMCP("<name>")`, and decorate each tool function with \
`@mcp.tool()`. The decorated function stays directly callable. MUST include \
an `if __name__ == "__main__":` self-test. The self-test calls the SIMPLEST, \
most basic documented command or function with a trivial input (not an \
advanced or showcase feature), and checks only that it ran and returned \
the expected shape -- the right type and expected keys or fields -- and \
raises if not. Do NOT assert on domain-specific verdicts or the tool's own \
pass/fail semantics. Running this file standalone (`python wrapper.py`) is \
the only signal Legwork has that the wrapper actually works.>
```

SHAPE 2 -- no usable entrypoint exists. Emit exactly one line, nothing else:

REFUSAL: <CATEGORY> - <one-sentence reason>

CATEGORY must be exactly one of:
- INSUFFICIENT_README (the README doesn't contain enough install/run information)
- NO_PROGRAMMATIC_ENTRYPOINT (the product is GUI/installer-only, no CLI or API)
- NOT_A_WRAPPABLE_CAPABILITY (this is a full application or project scaffolder, not a callable tool)
- EXTERNAL_HARDWARE_REQUIRED (this requires paired physical or cloud hardware to do anything)
- CREDENTIAL_REQUIRED (this tool needs its own third-party credentials/API key to run, distinct from Legwork's own LLM key)
"""

_REFUSAL_RE = re.compile(r"^REFUSAL:\s*(\w+)\s*-\s*(.+)$", re.MULTILINE)
_INSTALL_BLOCK_RE = re.compile(r"```install\s*\n(.*?)```", re.DOTALL)
_PYTHON_BLOCK_RE = re.compile(r"```python\s*\n(.*?)```", re.DOTALL)
_ENTRYPOINT_LINE_RE = re.compile(r"^ENTRYPOINT:\s*(.+)$", re.MULTILINE)


class InsufficientReadmeError(Exception):
    """The README doesn't contain enough install/run information for the
    codegen step to locate an entrypoint. (Distinct from readme_parser's
    InsufficientReadmeError, which fires when no README file exists at
    all — this one fires when a README exists but the model couldn't use
    it. Both map to the same rescue action per the design doc's Error
    Registry: fail fast, no retry.)"""


class NoProgrammaticEntrypointError(Exception):
    """The product is GUI/installer-only — no CLI or API surface exists."""


class NotAWrappableCapabilityError(Exception):
    """This is a full application or project scaffolder, not a callable
    tool the MCP-wrapper model fits."""


class ExternalHardwareRequiredError(Exception):
    """Requires paired physical or cloud hardware to do anything useful."""


class CredentialRequiredError(Exception):
    """The target repo needs its own third-party credentials to run,
    distinct from Legwork's own LLM key."""


_REFUSAL_EXCEPTIONS: dict[str, type[Exception]] = {
    "INSUFFICIENT_README": InsufficientReadmeError,
    "NO_PROGRAMMATIC_ENTRYPOINT": NoProgrammaticEntrypointError,
    "NOT_A_WRAPPABLE_CAPABILITY": NotAWrappableCapabilityError,
    "EXTERNAL_HARDWARE_REQUIRED": ExternalHardwareRequiredError,
    "CREDENTIAL_REQUIRED": CredentialRequiredError,
}

# Non-retryable per the design doc's Error Registry: each of these means
# retrying with the same README won't change the outcome.
FAIL_FAST_EXCEPTIONS: tuple[type[Exception], ...] = tuple(_REFUSAL_EXCEPTIONS.values())


@dataclass(frozen=True)
class WrapperDraft:
    entrypoint_description: str
    install_command: str
    wrapper_code: str


EARLIER_FAILURE_SUMMARY_CHARS = 200


def _one_line(failure: str) -> str:
    first = failure.strip().splitlines()[0] if failure.strip() else ""
    return first[:EARLIER_FAILURE_SUMMARY_CHARS]


def build_messages(readme_content: str, prior_failures: list[str] | None = None) -> list[ChatMessage]:
    """Compose the chat messages for one codegen attempt. On retry, the most
    recent failure goes in full and each earlier one as a one-line summary.
    Latest-only (the eng review's original cost choice) failed in the live
    run: attempt 3 repeated attempt 1's exact mistake because it never saw
    it. One-liners keep the extra cost to a few lines per retry."""
    user_parts = [f"README:\n\n{readme_content}"]
    if prior_failures:
        *earlier, latest = prior_failures
        if earlier:
            summary = "\n".join(f"- attempt {i}: {_one_line(f)}" for i, f in enumerate(earlier, 1))
            user_parts.append(f"\nEarlier attempts also failed (do not repeat these):\n{summary}")
        user_parts.append(
            f"\nYour most recent attempt failed:\n{latest}\n"
            "Fix the wrapper (or the install command) accordingly."
        )
    return [
        ChatMessage(role="system", content=SYSTEM_PROMPT),
        ChatMessage(role="user", content="\n".join(user_parts)),
    ]


def parse_response(text: str) -> WrapperDraft:
    """Parse a codegen response into a WrapperDraft, or raise the specific
    named exception for a REFUSAL, or LLMMalformedOutputError if the
    response matches neither documented shape."""
    refusal_match = _REFUSAL_RE.search(text)
    if refusal_match:
        category, reason = refusal_match.group(1), refusal_match.group(2).strip()
        exc_class = _REFUSAL_EXCEPTIONS.get(category)
        if exc_class is None:
            raise LLMMalformedOutputError(
                f"Model returned an unrecognized REFUSAL category {category!r}: {text[:300]!r}"
            )
        raise exc_class(reason)

    install_match = _INSTALL_BLOCK_RE.search(text)
    python_match = _PYTHON_BLOCK_RE.search(text)
    entrypoint_match = _ENTRYPOINT_LINE_RE.search(text)

    if not (install_match and python_match and entrypoint_match):
        raise LLMMalformedOutputError(
            "Codegen response matched neither the wrapper-draft shape nor "
            f"the REFUSAL shape: {text[:300]!r}"
        )

    return WrapperDraft(
        entrypoint_description=entrypoint_match.group(1).strip(),
        install_command=install_match.group(1).strip(),
        wrapper_code=python_match.group(1).strip(),
    )
