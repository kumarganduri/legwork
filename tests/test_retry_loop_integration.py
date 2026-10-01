"""Real end-to-end run of T6's pipeline: real GitHub fetch, real
obfuscation scan, real README parse, real sandboxed venv+install+invoke.
The LLM call is the only mocked seam — no OpenAI-compatible API key exists
in this environment (same gap disclosed throughout T3/T6). The mocked
response is hand-written to the exact SYSTEM_PROMPT contract, standing in
for what a real model is expected to produce; whether a real model
actually produces something this good is unvalidated and flagged in
codegen.py's module docstring.

Uses `2akouwu/reverify` — the validation spike's one clean success case,
and the same repo T5's manual smoke test already proved installs and runs
correctly inside this exact sandbox."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from legwork import sandbox_runner
from legwork.llm_client import LLMConfig
from legwork.repo_fetcher import RepoAccessError, fetch
from legwork.retry_loop import run

REALISTIC_REVERIFY_RESPONSE = '''\
ENTRYPOINT: `reverify auto <binary> --json` auto-triages a binary file (format, sections, strings)

```install
pip install "reverify[full]"
```

```python
import json
import subprocess
import sys


def reverify_auto(binary_path: str) -> dict:
    """Auto-triage a binary file: detect format, architecture, sections, top strings."""
    result = subprocess.run(
        ["reverify", "auto", binary_path, "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


if __name__ == "__main__":
    # Self-test against a real binary guaranteed to exist on the system.
    output = reverify_auto("/bin/ls")
    assert "filename" in output, f"unexpected output shape: {output}"
    assert output["filename"] == "ls"
    print("self-test passed:", output["filename"], output.get("type"))
```
'''


def _network_available() -> bool:
    try:
        fetch("2akouwu/reverify", Path(tempfile.mkdtemp()) / "probe")
        return True
    except RepoAccessError:
        return False


@pytest.mark.skipif(not sandbox_runner.available(), reason="no sandbox backend on this machine")
@pytest.mark.skipif(not _network_available(), reason="GitHub unreachable")
def test_full_pipeline_real_fetch_real_sandbox_mocked_llm(tmp_path):
    config = LLMConfig(endpoint="https://unused.example.com", api_key="unused", model="unused")

    with patch("legwork.retry_loop.complete", return_value=REALISTIC_REVERIFY_RESPONSE):
        result = run("2akouwu/reverify", tmp_path, config)

    assert result.success is True, result.final_error
    assert len(result.attempts) == 1
    assert result.attempts[0].outcome == "success"
    assert "reverify" in result.wrapper_code
    assert result.install_command == 'pip install "reverify[full]"'

    # The venv and wrapper really exist on disk, not just in the result object.
    attempt_dir = tmp_path / "attempt-1"
    assert (attempt_dir / "wrapper.py").exists()
    assert (attempt_dir / ".venv" / "bin" / "python").exists()


@pytest.mark.skipif(not sandbox_runner.available(), reason="no sandbox backend on this machine")
@pytest.mark.skipif(not _network_available(), reason="needs PyPI for the build backend and mcp")
def test_an_unpublished_repo_installs_from_its_source(tmp_path):
    """Most GitHub repos aren't on PyPI. The clone is copied into the attempt
    as ./src, so `pip install ./src` works and the wrapper can import it."""
    from legwork.retry_loop import _install_and_self_test

    clone = tmp_path / "repo"
    (clone / "tinytool").mkdir(parents=True)
    (clone / "tinytool" / "__init__.py").write_text("def shout(s):\n    return s.upper() + '!'\n")
    (clone / "pyproject.toml").write_text(
        '[project]\nname = "tinytool"\nversion = "0.1"\n'
        '[build-system]\nrequires = ["setuptools"]\nbuild-backend = "setuptools.build_meta"\n'
    )
    wrapper = (
        "from mcp.server.fastmcp import FastMCP\nfrom tinytool import shout\n"
        "mcp = FastMCP('tiny')\n@mcp.tool()\ndef yell(s: str) -> str:\n    return shout(s)\n"
        "if __name__ == '__main__':\n    assert yell('hi')\n    print('ok')\n"
    )
    _install_and_self_test(tmp_path / "attempt-1", "pip install -q ./src", wrapper)
    assert (tmp_path / "attempt-1" / "src" / "pyproject.toml").exists()


@pytest.mark.skipif(not sandbox_runner.available(), reason="no sandbox backend on this machine")
@pytest.mark.skipif(not _network_available(), reason="needs PyPI for mcp")
def test_a_multi_line_install_with_a_heredoc_runs_and_stops_at_a_failing_line(tmp_path):
    """RapidOCR's install (2026-10-01) ended with a `python - <<'PY' ... PY`
    model warm-up. Inlined as `a && <cmd> && b`, the closing PY became
    `PY && python -m pip ...` and the heredoc never ended."""
    from legwork.retry_loop import _install_and_self_test
    from legwork.sandbox_runner import DependencyInstallError

    install = "mkdir -p models\npython - <<'PY'\nopen('models/weights.txt', 'w').write('ready')\nPY\n"
    wrapper = (
        "from mcp.server.fastmcp import FastMCP\nmcp = FastMCP('t')\n"
        "if __name__ == '__main__':\n    print(open('models/weights.txt').read())\n"
    )
    _install_and_self_test(tmp_path / "attempt-1", install, wrapper)
    assert (tmp_path / "attempt-1" / "models" / "weights.txt").read_text() == "ready"

    with pytest.raises(DependencyInstallError):
        _install_and_self_test(tmp_path / "attempt-2", "false\necho 'carried on anyway' > marker\n", wrapper)
    assert not (tmp_path / "attempt-2" / "marker").exists()
