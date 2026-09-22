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

SYSTEM_PROMPT = """You are Legwork's codegen step. You are given a GitHub \
repo's README and must either produce a working Python MCP (Model Context \
Protocol) tool wrapper for it, or explain precisely why one can't be built.

Respond in EXACTLY one of these two shapes. No other text outside them.

SHAPE 1 -- a usable entrypoint exists:

ENTRYPOINT: <one-line description of what you identified as the callable capability>

```install
<shell command(s) to install the target repo's own dependencies -- prefer \
the most complete install variant the README describes (e.g. an extras \
group like "package[full]") over a bare minimal install>
```

```python
<complete Python source for an MCP tool wrapper that installs cleanly and \
exposes the identified capability as one or more typed MCP tools. MUST \
include an `if __name__ == "__main__":` block that calls the primary tool \
function directly with a reasonable example input drawn from the README \
(not mocked, not skipped) and raises if the result looks wrong. Running \
this file standalone (`python wrapper.py`) is the only signal Legwork has \
that the wrapper actually works, not just that it imports.>
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


def build_messages(readme_content: str, prior_failure: str | None = None) -> list[ChatMessage]:
    """Compose the chat messages for one codegen attempt. On retry,
    `prior_failure` carries only the immediately prior attempt's failure —
    never a growing transcript of all attempts (design doc, "Retry prompt
    content", eng-review pass 2)."""
    user_parts = [f"README:\n\n{readme_content}"]
    if prior_failure:
        user_parts.append(
            f"\nYour previous attempt failed:\n{prior_failure}\n"
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
