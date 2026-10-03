"""`legwork doctor`: check this machine is ready, in one paste-able report.

Read-only. It runs the checks Legwork itself uses (the sandbox backend, a
one-line sandboxed run, the cache, the key file) and looks for a Legwork
entry in known client configs. It never prints a key or a config's
contents: other MCP servers' tokens live in those files.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

OK, WARN, FAIL = "ok", "warn", "fail"
_MARK = {OK: "✓", WARN: "!", FAIL: "✗"}


def _client_configs() -> list[tuple[str, Path]]:
    home = Path.home()
    return [
        ("Claude Code", home / ".claude.json"),
        ("Claude Desktop", home / "Library/Application Support/Claude/claude_desktop_config.json"),
        ("Cursor", home / ".cursor/mcp.json"),
        ("Codex CLI", home / ".codex/config.toml"),
        ("opencode", home / ".config/opencode/opencode.json"),
    ]


def run() -> int:
    from legwork import cache_reader, llm_client, local_store, outputs, retry_loop, sandbox_runner

    checks: list[tuple[str, str, str]] = []

    def add(status: str, name: str, detail: str) -> None:
        checks.append((status, name, detail))

    try:
        from importlib.metadata import version

        add(OK, "Legwork", version("legwork-mcp"))
    except Exception:  # noqa: BLE001
        add(WARN, "Legwork", "version unknown (not installed as a package)")
    add(OK, "System", f"{platform.system()} {platform.machine()}, Python {platform.python_version()}")

    try:
        sandbox_runner._check_backend_available()
        with tempfile.TemporaryDirectory() as tmp:
            result = sandbox_runner.invoke(["/bin/sh", "-c", "echo sandboxed"], Path(tmp), timeout=30)
        add(OK if "sandboxed" in result.stdout else FAIL, "Sandbox", "starts and runs a command")
    except Exception as exc:  # noqa: BLE001 — report, never crash
        add(FAIL, "Sandbox", str(exc).splitlines()[0][:300])

    try:
        python = retry_loop._find_system_python()
        add(OK, "Python for builds", python)
    except Exception as exc:  # noqa: BLE001
        add(FAIL, "Python for builds", str(exc)[:200])
    uvx = shutil.which("uvx")
    add(OK if uvx else WARN, "uvx", uvx or "not on PATH: install uv (https://docs.astral.sh/uv/) to use `uvx legwork-mcp`")

    try:
        config = llm_client.LLMConfig.from_env()
        add(OK, "Model key", f"set ({config.model} at {re.sub(r'^https?://', '', config.endpoint).split('/')[0]}); key not shown")
    except llm_client.LLMAuthError as exc:
        text = str(exc)
        if "can be read by other users" in text:
            add(FAIL, "Model key", text)
        else:
            add(WARN, "Model key", f"none: the {len(_index_or_empty(cache_reader))} cached tools work; other repos need {llm_client.env_file_path()} (see the README)")
    add(OK if llm_client.github_token() else WARN, "GitHub token",
        "set" if llm_client.github_token() else "not set: 10 GitHub searches a minute (each find uses 2-3); add GITHUB_TOKEN to ~/.legwork.env for 30")

    index = _index_or_empty(cache_reader)
    base = os.environ.get(cache_reader.CACHE_URL_ENV) or cache_reader.default_cache_url()
    add(OK if index else WARN, "Public cache", f"{len(index)} tools at {base}" if index else f"unreachable or off ({base})")
    add(*_search_limit(llm_client.github_token()))

    for client, path in _client_configs():
        add(*_client_entry(client, path))

    home = local_store.legwork_home()
    hub_file = home / "hub.json"
    try:
        count = len(json.loads(hub_file.read_text()).get("installed", []))
    except (OSError, ValueError):
        count = 0
    add(OK, "Installed through the hub", f"{count} (in {home})")
    add(OK, "Outputs folder", f"{outputs.DEFAULT_DIR} (files tools make are copied here)")

    width = max(len(name) for _, name, _ in checks)
    home = str(Path.home())
    for status, name, detail in checks:
        # Pasted into public bug reports: your home folder shows as ~
        print(f"{_MARK[status]} {name.ljust(width)}  {detail.replace(home, '~')}")
    failed = sum(1 for status, _, _ in checks if status == FAIL)
    print(f"\n{'All set.' if not failed else f'{failed} problem(s) above.'} Paste this report into a bug report if something doesn't work.")
    return 1 if failed else 0


def _index_or_empty(cache_reader) -> list:
    try:
        return cache_reader.fetch_index()
    except Exception:  # noqa: BLE001
        return []


def _search_limit(token: str | None) -> tuple[str, str, str]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "legwork"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:  # /rate_limit doesn't count against the limit
        with urllib.request.urlopen(urllib.request.Request("https://api.github.com/rate_limit", headers=headers), timeout=10) as r:
            search = json.load(r)["resources"]["search"]
        return (OK if search["remaining"] > 3 else WARN, "GitHub search", f"{search['remaining']} of {search['limit']} left this minute")
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as exc:
        return (WARN, "GitHub search", f"couldn't reach GitHub ({exc}); find_tools will show cached tools only")


def _client_entry(client: str, path: Path) -> tuple[str, str, str]:
    """Whether the config mentions Legwork, and a pinned old version. Never
    prints the file: other servers' tokens live there."""
    if not path.exists():
        return (OK, client, f"no config at {path}")
    try:
        text = path.read_text(errors="replace")
    except OSError as exc:
        return (WARN, client, f"couldn't read its config ({exc.strerror})")
    if "legwork" not in text:
        return (WARN, client, f"no Legwork entry in {path}")
    pinned = re.findall(r"legwork-mcp@(\d+\.\d+\.\d+)", text)
    if pinned:
        return (WARN, client, f"Legwork pinned to {', '.join(sorted(set(pinned)))}: use legwork-mcp@latest for fixes")
    return (OK, client, "Legwork entry found")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run())
