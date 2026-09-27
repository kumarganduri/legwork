from __future__ import annotations

import pytest

from legwork.codegen import (
    CredentialRequiredError,
    ExternalHardwareRequiredError,
    InsufficientReadmeError,
    MissingToolchainError,
    NoProgrammaticEntrypointError,
    NotAWrappableCapabilityError,
    WrapperDraft,
    build_messages,
    describe_sandbox,
    failure_summary,
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
    assert "attempt" not in messages[1].content


def test_build_messages_single_prior_failure_in_full():
    messages = build_messages("# Repo", ["WrapperRuntimeError: boom\ntraceback line"])
    content = messages[1].content
    assert "most recent attempt failed" in content
    assert "traceback line" in content  # latest failure kept in full
    assert "Earlier attempts" not in content


def test_build_messages_earlier_failures_as_one_liners():
    """The live run's attempt 3 repeated attempt 1's mistake because it only
    saw attempt 2's failure. Earlier failures now appear as one-liners."""
    first = "WrapperRuntimeError: bad hex input\n" + "noise line\n" * 50
    messages = build_messages("# Repo", [first, "AttributeError: no .tool"])
    content = messages[1].content
    assert "attempt 1: WrapperRuntimeError: bad hex input" in content
    assert "noise line" not in content  # earlier failure summarized, not dumped
    assert "AttributeError: no .tool" in content


def test_system_prompt_pins_mcp_1x_api():
    prompt = build_messages("# Repo")[0].content
    assert "from mcp.server.fastmcp import FastMCP" in prompt
    assert "mcp<2" in prompt


def test_system_prompt_asks_for_an_offline_non_guessing_self_test():
    """Provider tests (2026-09-27): Claude and Nemotron self-tests guessed the
    output type or asserted the tool's verdict and failed working wrappers;
    Claude's and Ollama's reached for the network, which is off."""
    prompt = build_messages("# Repo")[0].content
    assert "SIMPLEST" in prompt
    assert "NETWORK OFF" in prompt and "60 seconds" in prompt
    assert "assert a specific type" in prompt
    assert "never assert on the tool's own verdicts" in prompt


def test_system_prompt_forbids_global_npm_installs():
    """Claude and Ollama both used `npm install -g`, which the sandbox blocks."""
    prompt = build_messages("# Repo")[0].content
    assert "never `-g`" in prompt and "./node_modules/.bin/" in prompt


def test_malformed_reply_names_what_is_missing():
    with pytest.raises(LLMMalformedOutputError, match="missing the ```install fenced block"):
        parse_response("ENTRYPOINT: x\n\n```python\nprint(1)\n```\n")


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
        ("MISSING_TOOLCHAIN", MissingToolchainError),
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


# --- failure summaries (trending trial: the error was cut off) ------------------

LONG_INSTALL = "'/bin/sh -c /opt/homebrew/bin/python3.14 -m venv /very/long/path/" + "x" * 300 + " && go install github.com/o/r@latest'"


def test_summary_of_a_multiline_install_failure_keeps_the_error_line():
    failure = f"DependencyInstallError: {LONG_INSTALL} failed (exit 1): downloading\nresolving\nERROR: No matching distribution found for foo\ndone"
    summary = failure_summary(failure)
    assert summary.startswith("DependencyInstallError:")
    assert summary.endswith("ERROR: No matching distribution found for foo")
    assert len(summary) <= 200 + 3


def test_summary_of_one_long_line_keeps_both_ends():
    summary = failure_summary(f"DependencyInstallError: {LONG_INSTALL} failed (exit 127): sh: go: command not found")
    assert summary.startswith("DependencyInstallError:")
    assert summary.endswith("sh: go: command not found")
    assert " … " in summary
    assert len(summary) <= 200


def test_short_failures_are_unchanged():
    assert failure_summary("NotAWrappableCapabilityError: a GUI app") == "NotAWrappableCapabilityError: a GUI app"


# --- the sandbox's toolchains go in the prompt ----------------------------------------


def test_sandbox_description_names_available_and_missing_toolchains():
    text = describe_sandbox({"node": True, "npm": True, "go": False, "cargo": False})
    assert "Also on PATH: node, npm." in text
    assert "NOT available: go, cargo." in text
    assert "MISSING_TOOLCHAIN" in text


def test_build_messages_includes_the_sandbox_description():
    content = build_messages("# Repo", sandbox="SANDBOX: no go")[1].content
    assert content.index("# Repo") < content.index("SANDBOX: no go")


def test_system_prompt_lists_the_missing_toolchain_refusal():
    assert "MISSING_TOOLCHAIN" in build_messages("# Repo")[0].content


def test_sandbox_description_names_the_os_the_source_folder_and_uv():
    text = describe_sandbox({"node": True, "go": False}, system="Linux (aarch64)")
    assert text.startswith("SANDBOX: Linux (aarch64).")
    assert "./src" in text and "pip install ./src" in text
    assert "pip install uv" in text
