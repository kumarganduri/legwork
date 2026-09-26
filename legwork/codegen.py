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

Validated live against gpt-5 (2026-09-25, see
docs/designs/legwork-live-runs-2026-09-25.md): every reply across three repos
followed this contract. The MCP-pin, self-test, and retry-context wording in
SYSTEM_PROMPT/build_messages came directly out of those runs.
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
- MISSING_TOOLCHAIN (every documented install path needs a toolchain the sandbox doesn't have -- see SANDBOX in the user message)
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


class MissingToolchainError(Exception):
    """Every documented install path needs a toolchain the sandbox lacks
    (trending trial, 2026-09-26: magpie needed Go, and without knowing Go
    was missing the model spent all 3 attempts on installs that couldn't
    work)."""


_REFUSAL_EXCEPTIONS: dict[str, type[Exception]] = {
    "INSUFFICIENT_README": InsufficientReadmeError,
    "NO_PROGRAMMATIC_ENTRYPOINT": NoProgrammaticEntrypointError,
    "NOT_A_WRAPPABLE_CAPABILITY": NotAWrappableCapabilityError,
    "EXTERNAL_HARDWARE_REQUIRED": ExternalHardwareRequiredError,
    "CREDENTIAL_REQUIRED": CredentialRequiredError,
    "MISSING_TOOLCHAIN": MissingToolchainError,
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
_ERROR_LINE_RE = re.compile(r"(?i)\berror\b|not found|no such file|denied|not permitted|exception")


def _clip(line: str, width: int) -> str:
    """Keep both ends of an over-long line: a failure line often starts with
    a long command and ends with the actual error."""
    if len(line) <= width:
        return line
    half = (width - 3) // 2
    return f"{line[:half]} … {line[len(line) - (width - 3 - half):]}"


def failure_summary(failure: str, width: int = EARLIER_FAILURE_SUMMARY_CHARS) -> str:
    """One line that keeps the cause. A cut from the start alone showed only
    the install command, never the error (trending trial, 2026-09-26), so
    this keeps the first line's ends plus the last line that reads like an
    error, if there is one."""
    lines = [line.strip() for line in failure.strip().splitlines() if line.strip()]
    if not lines:
        return ""
    error_line = next((line for line in reversed(lines[1:]) if _ERROR_LINE_RE.search(line)), None)
    if error_line is None:
        return _clip(lines[0], width)
    return f"{_clip(lines[0], width // 3)} … {_clip(error_line, width - width // 3 - 3)}"


def describe_sandbox(toolchains: dict[str, bool]) -> str:
    have = ", ".join(name for name, ok in toolchains.items() if ok) or "none"
    missing = ", ".join(name for name, ok in toolchains.items() if not ok) or "none"
    return (
        "SANDBOX: the install command runs in a fresh Python venv (python and pip on PATH), network on. "
        "HOME is a scratch folder and nothing outside it is writable: no sudo, no system-wide or "
        "/usr/local installs, no installers that need either.\n"
        f"Also on PATH: {have}.\n"
        f"NOT available: {missing}. If every documented install path needs one of these, "
        "refuse with MISSING_TOOLCHAIN."
    )


def build_messages(
    readme_content: str, prior_failures: list[str] | None = None, sandbox: str | None = None
) -> list[ChatMessage]:
    """Compose the chat messages for one codegen attempt. On retry, the most
    recent failure goes in full and each earlier one as a one-line summary.
    Latest-only (the eng review's original cost choice) failed in the live
    run: attempt 3 repeated attempt 1's exact mistake because it never saw
    it. One-liners keep the extra cost to a few lines per retry."""
    user_parts = [f"README:\n\n{readme_content}"]
    if sandbox:
        user_parts.append(f"\n{sandbox}")
    if prior_failures:
        *earlier, latest = prior_failures
        if earlier:
            summary = "\n".join(f"- attempt {i}: {failure_summary(f)}" for i, f in enumerate(earlier, 1))
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
