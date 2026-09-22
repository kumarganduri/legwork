from __future__ import annotations

import pytest

from legwork.codegen import (
    CredentialRequiredError,
    ExternalHardwareRequiredError,
    InsufficientReadmeError,
    NoProgrammaticEntrypointError,
    NotAWrappableCapabilityError,
    WrapperDraft,
    build_messages,
    parse_response,
)
from legwork.llm_client import LLMMalformedOutputError

VALID_RESPONSE = """\
ENTRYPOINT: reverify auto <binary> --json auto-triages a binary file

```install
pip install "reverify[full]"
```

```python
from mcp.server import Server

server = Server("reverify")

@server.tool()
def reverify_auto(binary_path: str) -> dict:
    import subprocess, json
    result = subprocess.run(["reverify", "auto", binary_path, "--json"], capture_output=True, text=True)
    return json.loads(result.stdout)
```
"""


# --- build_messages ------------------------------------------------------


def test_system_prompt_requires_a_self_test_main_block():
    """Without this, `python wrapper.py` would just import and exit,
    validating nothing — the invoke phase's only signal that the wrapper
    actually works comes from this self-test running for real."""
    assert "__main__" in build_messages("# Repo")[0].content


def test_build_messages_includes_readme():
    messages = build_messages("# My Repo\n\nInstall with pip.")
    assert messages[0].role == "system"
    assert messages[1].role == "user"
    assert "# My Repo" in messages[1].content


def test_build_messages_without_prior_failure_has_no_retry_language():
    messages = build_messages("# Repo")
    assert "previous attempt" not in messages[1].content


def test_build_messages_with_prior_failure_includes_only_latest_not_history():
    messages = build_messages("# Repo", prior_failure="WrapperRuntimeError: boom")
    assert "WrapperRuntimeError: boom" in messages[1].content
    assert "previous attempt" in messages[1].content


# --- parse_response: success shape ----------------------------------------


def test_parse_response_extracts_wrapper_draft():
    draft = parse_response(VALID_RESPONSE)
    assert isinstance(draft, WrapperDraft)
    assert "reverify" in draft.entrypoint_description
    assert draft.install_command == 'pip install "reverify[full]"'
    assert "def reverify_auto" in draft.wrapper_code
    assert "```" not in draft.wrapper_code  # fences stripped, not included


def test_parse_response_handles_extra_prose_around_the_shape():
    """Models sometimes add a sentence of preamble even when told not to —
    the parser should still find the fenced blocks and ENTRYPOINT line."""
    response = "Sure, here's the wrapper:\n\n" + VALID_RESPONSE + "\nLet me know if you need changes!"
    draft = parse_response(response)
    assert draft.install_command == 'pip install "reverify[full]"'


# --- parse_response: refusal shape -----------------------------------------


@pytest.mark.parametrize(
    "category,exc_class",
    [
        ("INSUFFICIENT_README", InsufficientReadmeError),
        ("NO_PROGRAMMATIC_ENTRYPOINT", NoProgrammaticEntrypointError),
        ("NOT_A_WRAPPABLE_CAPABILITY", NotAWrappableCapabilityError),
        ("EXTERNAL_HARDWARE_REQUIRED", ExternalHardwareRequiredError),
        ("CREDENTIAL_REQUIRED", CredentialRequiredError),
    ],
)
def test_parse_response_refusal_categories_map_to_exceptions(category, exc_class):
    response = f"REFUSAL: {category} - because reasons"
    with pytest.raises(exc_class, match="because reasons"):
        parse_response(response)


def test_parse_response_unrecognized_refusal_category_is_malformed():
    with pytest.raises(LLMMalformedOutputError, match="unrecognized REFUSAL"):
        parse_response("REFUSAL: MADE_UP_CATEGORY - some reason")


# --- parse_response: malformed shape ----------------------------------------


def test_parse_response_missing_python_block_is_malformed():
    broken = "ENTRYPOINT: does a thing\n\n```install\npip install foo\n```\n"
    with pytest.raises(LLMMalformedOutputError):
        parse_response(broken)


def test_parse_response_missing_install_block_is_malformed():
    broken = "ENTRYPOINT: does a thing\n\n```python\nprint('hi')\n```\n"
    with pytest.raises(LLMMalformedOutputError):
        parse_response(broken)


def test_parse_response_missing_entrypoint_line_is_malformed():
    broken = "```install\npip install foo\n```\n\n```python\nprint('hi')\n```\n"
    with pytest.raises(LLMMalformedOutputError):
        parse_response(broken)


def test_parse_response_freeform_text_is_malformed():
    with pytest.raises(LLMMalformedOutputError):
        parse_response("I'm not sure what to do here, let me think about it...")


def test_parse_response_empty_string_is_malformed():
    with pytest.raises(LLMMalformedOutputError):
        parse_response("")
