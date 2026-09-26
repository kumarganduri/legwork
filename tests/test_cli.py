"""The `legwork` command, with the pipeline and the sandbox exec mocked —
tests/test_serve_integration.py covers the real build-then-serve path."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from legwork import cli, local_store
from legwork.obfuscation_scanner import ObfuscatedPayloadDetectedError
from legwork.repo_fetcher import RepoRef
from legwork.retry_loop import AttemptLog, RunResult

REF = RepoRef("owner", "repo")


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGWORK_HOME", str(tmp_path))
    monkeypatch.setenv("LEGWORK_LLM_ENDPOINT", "https://api.example.com/v1")
    monkeypatch.setenv("LEGWORK_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LEGWORK_LLM_MODEL", "gpt-test")


def _successful_run(repo_url, workdir, config, progress):
    attempt = workdir / "attempt-1"
    (attempt / ".venv" / "bin").mkdir(parents=True)
    (attempt / ".venv" / "bin" / "python").write_text("")
    (attempt / "wrapper.py").write_text("")
    progress("cloning")
    return RunResult(
        success=True,
        wrapper_code="",
        install_command="pip install repo",
        attempts=[AttemptLog(1, "initial attempt", "success", "does a thing")],
        attempt_dir=attempt,
    )


# --- routing -----------------------------------------------------------


def test_bare_repo_argument_means_build():
    with patch("legwork.cli.cmd_build", return_value=0) as build:
        assert cli.main(["owner/repo"]) == 0
    build.assert_called_once_with("owner/repo")


def test_serve_subcommand_routes_to_serve():
    with patch("legwork.cli.cmd_serve", return_value=0) as serve:
        cli.main(["serve", "owner/repo"])
    serve.assert_called_once_with("owner/repo")


# --- build ------------------------------------------------------------------


def test_build_success_records_the_wrapper_and_prints_connect_instructions(capsys):
    with patch("legwork.cli.retry_loop.run", side_effect=_successful_run):
        assert cli.cmd_build("owner/repo") == 0
    out = capsys.readouterr().out
    assert "claude mcp add repo --" in out
    assert "serve owner/repo" in out
    config = json.loads(out[out.index("{") :])
    assert config["mcpServers"]["repo"]["args"][-2:] == ["serve", "owner/repo"]
    assert local_store.load_current(REF).entrypoint == "does a thing"


def test_build_failure_reports_each_attempt_and_exits_1(capsys):
    failed = RunResult(
        success=False,
        wrapper_code=None,
        install_command=None,
        attempts=[AttemptLog(1, "initial attempt", "NoProgrammaticEntrypointError", "no CLI")],
        final_error="no CLI",
    )
    with patch("legwork.cli.retry_loop.run", return_value=failed):
        assert cli.cmd_build("owner/repo") == 1
    assert "NoProgrammaticEntrypointError" in capsys.readouterr().err


def test_build_failure_shows_the_error_and_saves_the_full_output(tmp_path, capsys):
    detail = "DependencyInstallError: '/bin/sh -c " + "x" * 400 + "' failed (exit 127): sh: go: command not found"
    failed = RunResult(
        success=False,
        wrapper_code=None,
        install_command=None,
        attempts=[AttemptLog(1, "initial attempt", "DependencyInstallError", detail)],
        final_error=detail,
    )
    with patch("legwork.cli.retry_loop.run", return_value=failed):
        assert cli.cmd_build("owner/repo") == 1
    err = capsys.readouterr().err
    assert "sh: go: command not found" in err
    log = next((tmp_path / "wrappers" / "owner__repo" / "builds").glob("*/attempts.log"))
    assert detail in log.read_text()
    assert str(log) in err


def test_build_blocked_by_scanner_exits_1_with_reason(capsys):
    with patch("legwork.cli.retry_loop.run", side_effect=ObfuscatedPayloadDetectedError("evil.py")):
        assert cli.cmd_build("owner/repo") == 1
    assert "ObfuscatedPayloadDetectedError" in capsys.readouterr().err


def test_build_rejects_a_bad_url_before_creating_anything(tmp_path, capsys):
    assert cli.cmd_build("not a url") == 1
    assert not (tmp_path / "wrappers").exists()


# --- serve --------------------------------------------------------------------


def test_serve_without_a_build_exits_1_and_keeps_stdout_clean(capsys):
    assert cli.cmd_serve("owner/repo") == 1
    captured = capsys.readouterr()
    assert captured.out == ""  # stdout is the MCP transport
    assert "legwork owner/repo" in captured.err


def test_serve_launches_the_saved_wrapper_in_the_sandbox():
    with patch("legwork.cli.retry_loop.run", side_effect=_successful_run):
        cli.cmd_build("owner/repo")
    record = local_store.load_current(REF)
    with patch("legwork.cli.sandbox_runner.exec_serve") as exec_serve:
        cli.cmd_serve("owner/repo")
    command, workdir, env = exec_serve.call_args.args
    assert command[0] == record.python_path
    assert command[-1] == record.wrapper_path
    assert workdir == Path(record.attempt_dir)
    assert env["PATH"].startswith(str(Path(record.python_path).parent))


# --- how MCP clients should launch legwork ------------------------------------


class _FakeDist:
    def __init__(self, direct_url):
        self.direct_url = direct_url

    def read_text(self, name):
        return None if self.direct_url is None else json.dumps(self.direct_url)


@pytest.mark.parametrize(
    ("direct_url", "expected"),
    [
        (None, ["/bin/uvx", "legwork-mcp"]),  # from PyPI
        ({"url": "file:///src/legwork", "dir_info": {}}, ["/bin/uvx", "--from", "/src/legwork", "legwork"]),
        (
            {"url": "file:///d/legwork_mcp-0.1.0-py3-none-any.whl", "archive_info": {}},
            ["/bin/uvx", "--from", "/d/legwork_mcp-0.1.0-py3-none-any.whl", "legwork"],
        ),
        (
            {"url": "https://github.com/o/legwork", "vcs_info": {"vcs": "git", "commit_id": "abc", "requested_revision": "v0.1"}},
            ["/bin/uvx", "--from", "git+https://github.com/o/legwork@v0.1", "legwork"],
        ),
    ],
)
def test_under_uvx_the_launch_command_is_uvx_not_the_throwaway_env(monkeypatch, direct_url, expected):
    monkeypatch.setattr("sys.prefix", "/Users/me/.cache/uv/archive-v0/AbC123")
    monkeypatch.setattr("legwork.cli.shutil.which", lambda name: f"/bin/{name}")
    with patch("legwork.cli.importlib.metadata.distribution", return_value=_FakeDist(direct_url)):
        assert cli._self_command() == expected


def test_outside_uvx_the_launch_command_is_the_installed_script(monkeypatch):
    monkeypatch.setattr("sys.prefix", "/Users/me/.local/share/uv/tools/legwork-mcp")
    monkeypatch.setattr("sys.argv", ["/Users/me/.local/bin/legwork", "owner/repo"])
    assert cli._self_command() == ["/Users/me/.local/bin/legwork"]


def test_a_relative_launch_path_is_made_absolute(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.prefix", str(tmp_path / ".venv"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", [".venv/bin/legwork", "owner/repo"])
    assert cli._self_command() == [str(tmp_path / ".venv" / "bin" / "legwork")]
