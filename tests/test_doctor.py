"""legwork doctor: a paste-able readiness report that never shows secrets."""

from __future__ import annotations

from pathlib import Path

from legwork import doctor


def _run(monkeypatch, tmp_path, capsys, configs):
    monkeypatch.setattr(doctor, "_client_configs", lambda: configs)
    monkeypatch.setattr(doctor, "_search_limit", lambda token: (doctor.OK, "GitHub search", "10 of 10 left"))
    code = doctor.run()
    return code, capsys.readouterr().out


def test_no_key_is_a_warning_and_secrets_never_appear(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LEGWORK_HOME", str(tmp_path / "home"))
    for name in ("LEGWORK_LLM_ENDPOINT", "LEGWORK_LLM_API_KEY", "LEGWORK_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    code, out = _run(monkeypatch, tmp_path, capsys, [])
    assert "! Model key" in out and "cached tools work" in out
    assert code == 0

    monkeypatch.setenv("LEGWORK_LLM_ENDPOINT", "https://api.example.com/v1")
    monkeypatch.setenv("LEGWORK_LLM_API_KEY", "sk-doctor-secret-1234567890")
    monkeypatch.setenv("LEGWORK_LLM_MODEL", "gpt-test")
    _, out = _run(monkeypatch, tmp_path, capsys, [])
    assert "sk-doctor-secret" not in out and "gpt-test at api.example.com" in out


def test_client_configs_are_checked_without_printing_them(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LEGWORK_HOME", str(tmp_path / "home"))
    pinned = tmp_path / "desktop.json"
    pinned.write_text('{"mcpServers": {"legwork": {"args": ["legwork-mcp@0.7.1", "hub"]}, "other": {"env": {"TOKEN": "ghp_other_server_secret"}}}}')
    current = tmp_path / "cursor.json"
    current.write_text('{"mcpServers": {"legwork": {"args": ["legwork-mcp@latest", "hub"]}}}')
    missing = tmp_path / "absent.json"
    _, out = _run(monkeypatch, tmp_path, capsys, [("Desktop", pinned), ("Cursor", current), ("Codex", missing)])
    assert "pinned to 0.7.1" in out and "Legwork entry found" in out and "no config at" in out
    assert "ghp_other_server_secret" not in out


def test_a_key_file_others_can_read_is_a_failure(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LEGWORK_HOME", str(tmp_path / "home"))
    for name in ("LEGWORK_LLM_ENDPOINT", "LEGWORK_LLM_API_KEY", "LEGWORK_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    key = tmp_path / "legwork.env"
    key.write_text("LEGWORK_LLM_API_KEY=sk-x\n")
    key.chmod(0o644)
    monkeypatch.setenv("LEGWORK_ENV_FILE", str(key))
    code, out = _run(monkeypatch, tmp_path, capsys, [])
    assert "✗ Model key" in out and "chmod 600" in out and code == 1
