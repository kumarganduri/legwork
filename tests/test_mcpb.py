"""The Claude Desktop extension (mcpb/): it runs the released Legwork from
PyPI, so its pin must follow each release (scripts/build_mcpb.sh locks it
by hash once the release is on PyPI), and the user's settings must
become exactly the hub's grants."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MCPB = ROOT / "mcpb"


def _launch():
    spec = importlib.util.spec_from_file_location("mcpb_launch", MCPB / "src" / "launch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_bundle_runs_the_version_being_released():
    # Not tomllib: it needs Python 3.11, and Legwork supports 3.10.
    [version] = re.findall(r'^version = "(.+)"$', (ROOT / "pyproject.toml").read_text(), re.M)
    manifest = json.loads((MCPB / "manifest.json").read_text())
    bundle = (MCPB / "pyproject.toml").read_text()
    assert manifest["version"] == version and f'\nversion = "{version}"\n' in bundle
    assert f'dependencies = ["legwork-mcp=={version}"]' in bundle


def test_settings_become_the_hubs_grants():
    hub_args = _launch().hub_args
    assert hub_args(["false", "/Users/me/Downloads", "/Users/me/Documents"]) == [
        "hub", "--allow-read", "/Users/me/Downloads", "--allow-read", "/Users/me/Documents"]
    assert hub_args(["true"]) == ["hub", "--allow-net"]
    assert hub_args([]) == ["hub"]


def test_a_setting_left_unfilled_grants_nothing():
    hub_args = _launch().hub_args
    assert hub_args(["${user_config.allow_net}", "${user_config.folders}"]) == ["hub"]
    assert hub_args(["TRUE", ""]) == ["hub", "--allow-net"]
    assert hub_args(["false", "/x/${UNKNOWN}/y"]) == ["hub"]


def test_the_default_folder_is_expanded():
    """Claude Desktop passed the manifest's default through unexpanded:
    the hub started with no folders at all (2026-10-18)."""
    home = str(Path.home())
    assert _launch().hub_args(["false", "${HOME}/Downloads", "${DOCUMENTS}"]) == [
        "hub", "--allow-read", f"{home}/Downloads", "--allow-read", f"{home}/Documents"]
