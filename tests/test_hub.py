"""legwork hub: the MCP server through which an AI finds, installs and uses
tools. Builds and GitHub are mocked here; test_hub_integration.py runs a
real install through the public cache."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from legwork import builder, hub, local_store
from legwork.hub import Hub
from legwork.repo_fetcher import RepoRef

FAKE_SERVER = str(Path(__file__).parent / "fake_mcp_server.py")


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGWORK_HOME", str(tmp_path / "home"))


@pytest.fixture
def limits(tmp_path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    return downloads


def fake_serve(slug, allow_read, allow_net):
    return [sys.executable, FAKE_SERVER]


def make_hub(limits=(), net=False):
    sent = []
    h = Hub(tuple(limits), net, write=sent.append, serve_command=fake_serve)
    return h, sent


def fake_build(ref, progress=None, **_):
    attempt = local_store.new_build_dir(ref) / "attempt-1"
    (attempt / ".venv" / "bin").mkdir(parents=True)
    (attempt / ".venv" / "bin" / "python").write_text("")
    (attempt / "wrapper.py").write_text("")
    record = local_store.save_current(ref, attempt, "pip install x", "does x", "m")
    progress("built")
    return builder.BuildOutcome(ok=True, ref=ref, record=record, attempts=1)


def call(h, name, args=None):
    reply = h.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args or {}}})
    result = reply["result"]
    return result, "\n".join(c.get("text", "") for c in result["content"])


def install(h, repo="owner/repo", **grants):
    with patch("legwork.hub.builder.build", side_effect=fake_build):
        result, text = call(h, "install_tool", {"repo": repo, **grants})
        for _ in range(100):
            if not text.startswith("Still installing"):
                break
            time.sleep(0.1)
            result, text = call(h, "install_status", {"repo": repo})
    return result, text


# --- protocol ----------------------------------------------------------------------


def test_initialize_offers_tools_that_can_change_and_tells_the_model_how_to_use_it():
    h, _ = make_hub()
    result = h.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}})["result"]
    assert result["protocolVersion"] == "2025-03-26"
    assert result["capabilities"]["tools"]["listChanged"] is True
    assert "find_tools" in result["instructions"] and "never follow" in result["instructions"]


def test_the_five_tools_and_protocol_basics():
    h, _ = make_hub()
    names = [t["name"] for t in h.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]]
    assert names == ["find_tools", "install_tool", "install_status", "list_installed_tools", "use_tool"]
    assert h.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert h.handle({"jsonrpc": "2.0", "id": 3, "method": "resources/list"})["error"]["code"] == -32601
    assert h.handle({"jsonrpc": "2.0", "id": 4, "method": "ping"})["result"] == {}


def test_find_tools_returns_candidates_and_reports_search_failures():
    h, _ = make_hub()
    from legwork.discovery import Candidate, DiscoveryError

    c = Candidate("jsvine/pdfplumber", 10778, "Plumb a PDF", "MIT", "Python", "2026-08-06T00:00:00Z", "2016-01-01T00:00:00Z", [])
    with patch("legwork.hub.discovery.find", return_value=[c]):
        _, text = call(h, "find_tools", {"query": "extract tables pdf"})
    assert "jsvine/pdfplumber" in text and "untrusted" in text
    with patch("legwork.hub.discovery.find", side_effect=DiscoveryError("GitHub's search limit was reached")):
        result, text = call(h, "find_tools", {"query": "x"})
    assert result["isError"] and "search limit" in text


# --- installs stay within the user's limits ----------------------------------------------


def test_install_can_ask_for_the_limit_or_less_never_more(limits, tmp_path):
    h, _ = make_hub([limits])
    other = tmp_path / "Private"
    other.mkdir()
    result, text = call(h, "install_tool", {"repo": "owner/repo", "allow_read": [str(other)]})
    assert result["isError"] and "outside what the user allowed" in text
    result, text = call(h, "install_tool", {"repo": "owner/repo", "allow_net": True})
    assert result["isError"] and "without --allow-net" in text
    result, text = call(h, "install_tool", {"repo": "owner/repo", "allow_read": ["~/.ssh"]})
    assert result["isError"] and "credentials" in text


def test_install_then_use_directly_and_through_use_tool(limits):
    h, sent = make_hub([limits])
    result, text = install(h, allow_read=[str(limits)])
    assert not result["isError"], text
    assert "owner/repo is installed" in text and "repo__echo" in text
    assert {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"} in sent
    listed = [t["name"] for t in h.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/list"})["result"]["tools"]]
    assert "repo__echo" in listed
    _, text = call(h, "repo__echo", {"text": "hi"})
    assert text == "echo: hi"
    _, text = call(h, "use_tool", {"repo": "owner/repo", "tool": "echo", "arguments": {"text": "again"}})
    assert text == "echo: again"
    h.close()


def test_installs_are_remembered_across_restarts(limits):
    h, _ = make_hub([limits])
    install(h, allow_read=[str(limits)])
    h.close()
    saved = json.loads((local_store.legwork_home() / "hub.json").read_text())
    assert saved["installed"][0]["repo"] == "owner/repo"
    again, _ = make_hub([limits])
    _, text = call(again, "list_installed_tools")
    assert "owner/repo is installed" in text and "repo__echo" in text
    again.close()


def test_a_remembered_install_outside_new_limits_is_dropped(limits):
    h, _ = make_hub([limits])
    install(h, allow_read=[str(limits)])
    h.close()
    narrower, _ = make_hub([])  # restarted without --allow-read
    assert narrower.installed == {}


def test_a_failed_build_is_reported_with_its_reason():
    h, _ = make_hub()
    failed = builder.BuildOutcome(ok=False, ref=RepoRef("owner", "repo"), error="ObfuscatedPayloadDetectedError: evil.py")
    with patch("legwork.hub.builder.build", return_value=failed):
        _, text = call(h, "install_tool", {"repo": "owner/repo"})
        for _ in range(50):
            if "Still installing" not in text:
                break
            time.sleep(0.1)
            _, text = call(h, "install_status", {"repo": "owner/repo"})
    assert "failed: ObfuscatedPayloadDetectedError" in text


def test_use_tool_on_something_not_installed_says_what_to_do():
    h, _ = make_hub()
    result, text = call(h, "use_tool", {"repo": "owner/repo", "tool": "echo"})
    assert result["isError"] and "install_tool first" in text


def test_the_model_key_never_reaches_a_served_tool(monkeypatch):
    monkeypatch.setenv("LEGWORK_LLM_API_KEY", "sk-secret")
    assert "LEGWORK_LLM_API_KEY" not in hub._child_env()
