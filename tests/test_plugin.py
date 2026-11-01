"""The Claude plugin (plugin/), for Claude Code and Cowork and Anthropic's
directory: it runs the released Legwork from PyPI, pinned to an exact version
(the directory refuses an unpinned uvx), so the pin must follow each release."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugin"


def test_the_plugin_runs_the_version_being_released():
    [version] = re.findall(r'^version = "(.+)"$', (ROOT / "pyproject.toml").read_text(), re.M)
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    server = json.loads((PLUGIN / ".mcp.json").read_text())["mcpServers"]["legwork"]
    assert manifest["version"] == version
    assert server["command"] == "uvx" and server["args"][0] == f"legwork-mcp=={version}"


def test_the_folder_setting_is_required_and_is_the_only_grant():
    """A cleared setting would pass `--allow-read ""` (refused from the release after 0.7.18);
    the dialog doesn't accept one, and there's no network grant."""
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    folder = manifest["userConfig"]["allowed_folder"]
    assert folder["required"] is True and folder["default"] == "~/Downloads"
    args = json.loads((PLUGIN / ".mcp.json").read_text())["mcpServers"]["legwork"]["args"]
    assert args[1:] == ["hub", "--allow-read", "${user_config.allowed_folder}"]


def test_the_readme_discloses_what_it_contacts():
    """Anthropic's scan checks that a plugin discloses what it runs, sends
    and fetches."""
    readme = (PLUGIN / "README.md").read_text()
    for needed in ("## Privacy", "api.github.com", "raw.githubusercontent.com", "model provider", "no telemetry"):
        assert needed in readme, needed
    assert len(re.sub(r"```.*?```", "", readme, flags=re.S).split()) >= 40
    assert not list(PLUGIN.rglob(".DS_Store"))
