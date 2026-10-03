"""T7 cache writer: every blocking check leaves nothing on disk, and a clean
build writes the full manifest. The sandboxed smoke test is mocked here;
the real one is the same `invoke` call test_sandbox_runner covers."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from legwork import cache_writer, cli, local_store
from legwork.cache_writer import (
    SecretScrubTriggeredError,
    SmokeTestFailedError,
    VerbatimCopyDetectedError,
)
from legwork.repo_fetcher import RepoAccessError, RepoRef, remote_head_sha
from legwork.sandbox_runner import WrapperRuntimeError

REF = RepoRef("owner", "repo")
MIT = "MIT License\n\nPermission is hereby granted, free of charge, to any person obtaining a copy\n"
README_PARAGRAPH = (
    "Frobnicate reads every widget in the input directory, sorts them by their declared weight, "
    "and then writes a single merged report that lists each widget together with its owner, "
    "its checksum, the time it was last touched, and a short human readable summary of what "
    "changed since the previous run, so that reviewers can see at a glance what moved."
)
CLEAN_WRAPPER = '''from mcp.server.fastmcp import FastMCP

mcp = FastMCP("frobnicate")


@mcp.tool()
def frobnicate(path: str) -> str:
    """Run `frobnicate --json` on a directory."""
    return path


if __name__ == "__main__":
    assert isinstance(frobnicate("."), str)
'''


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGWORK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("LEGWORK_LLM_API_KEY", "configured-key-0123456789")


@pytest.fixture(autouse=True)
def smoke_test():
    with patch("legwork.cache_writer.sandbox_runner.invoke") as invoke:
        yield invoke


@pytest.fixture(autouse=True)
def upstream():
    """The repo's current commit on GitHub. Defaults to whatever the saved
    build was cloned at (up to date); tests set `return_value` to move it."""

    def build_sha(url):
        return _git(Path(local_store.load_current(REF).attempt_dir).parent / "repo", "rev-parse", "HEAD")

    with patch("legwork.cache_writer.repo_fetcher.remote_head_sha", side_effect=build_sha) as remote:
        yield remote


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def make_build(wrapper_code: str = CLEAN_WRAPPER, license_text: str | None = MIT) -> local_store.BuildRecord:
    """A saved build the way `legwork <repo>` leaves one: the clone next to
    the attempt folder, current.json pointing at the attempt."""
    build = local_store.new_build_dir(REF)
    clone = build / "repo"
    clone.mkdir()
    (clone / "README.md").write_text(f"# frobnicate\n\n{README_PARAGRAPH}\n\n    frobnicate --json .\n")
    if license_text is not None:
        (clone / "LICENSE").write_text(license_text)
    _git(clone, "init", "-q")
    _git(clone, "add", ".")
    _git(clone, "commit", "-qm", "init")

    attempt = build / "attempt-1"
    (attempt / ".venv" / "bin").mkdir(parents=True)
    (attempt / ".venv" / "bin" / "python").write_text("")
    (attempt / "wrapper.py").write_text(wrapper_code)
    local_store.save_current(REF, attempt, "pip install frobnicate", "Frobnicates a directory", "gpt-test")
    return local_store.load_current(REF)


def _nothing_written(cache_dir: Path) -> bool:
    return not cache_dir.exists() or not any(cache_dir.iterdir())


# --- a clean entry ---------------------------------------------------------


def test_writes_wrapper_and_the_full_manifest(tmp_path):
    record = make_build()
    entry = cache_writer.write_entry(REF, record, tmp_path / "cache")

    assert entry.path == tmp_path / "cache" / "owner__repo"
    assert (entry.path / "wrapper.py").read_text() == CLEAN_WRAPPER
    manifest = json.loads((entry.path / "manifest.json").read_text())
    clone = Path(record.attempt_dir).parent / "repo"
    assert manifest["source_repo_url"] == "https://github.com/owner/repo"
    assert manifest["commit_sha"] == _git(clone, "rev-parse", "HEAD")
    assert manifest["synthesis_date"] == record.built_at
    assert manifest["wrapper_language"] == "python"
    assert manifest["source_license"] == "MIT"
    assert manifest["license_flag"] is None
    assert manifest["llm_model"] == "gpt-test"
    assert manifest["smoke_test"]["result"] == "passed"
    assert manifest["install_command"] == "pip install frobnicate"
    assert "License warning" not in entry.pr_description


def test_smoke_test_reruns_the_saved_wrapper_in_the_sandbox(tmp_path, smoke_test):
    record = make_build()
    cache_writer.write_entry(REF, record, tmp_path / "cache")
    command, workdir = smoke_test.call_args.args
    assert command == [record.python_path, record.wrapper_path]
    assert workdir == Path(record.attempt_dir)


def test_a_new_synthesis_replaces_the_previous_entry(tmp_path):
    cache_dir = tmp_path / "cache"
    old = cache_dir / "owner__repo"
    old.mkdir(parents=True)
    (old / "leftover.txt").write_text("from an older layout")
    cache_writer.write_entry(REF, make_build(), cache_dir)
    assert sorted(p.name for p in old.iterdir()) == ["manifest.json", "wrapper.py"]
    assert [p.name for p in cache_dir.iterdir()] == ["owner__repo"]  # no staging leftovers


# --- secret scrub blocks the write ---------------------------------------------


def test_scrub_blocks_write_on_a_key_shaped_string(tmp_path):
    leaky = CLEAN_WRAPPER + '\nAPI_KEY = "sk-proj-abcdefghijklmnopqrstuvwxyz0123"\n'
    with pytest.raises(SecretScrubTriggeredError) as exc:
        cache_writer.write_entry(REF, make_build(leaky), tmp_path / "cache")
    assert "sk- style API key" in str(exc.value)
    assert "abcdefghij" not in str(exc.value)  # the message never repeats the key
    assert _nothing_written(tmp_path / "cache")


def test_scrub_blocks_write_on_the_configured_key_in_any_format(tmp_path):
    leaky = CLEAN_WRAPPER + '\nTOKEN = "configured-key-0123456789"\n'
    with pytest.raises(SecretScrubTriggeredError) as exc:
        cache_writer.write_entry(REF, make_build(leaky), tmp_path / "cache")
    assert "LEGWORK_LLM_API_KEY" in str(exc.value)
    assert "configured-key" not in str(exc.value)
    assert _nothing_written(tmp_path / "cache")


def test_scrub_checks_the_manifest_too(tmp_path):
    record = make_build()
    leaky = local_store.BuildRecord(**{**record.__dict__, "install_command": "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz pip install x"})
    with pytest.raises(SecretScrubTriggeredError, match="manifest.json"):
        cache_writer.write_entry(REF, leaky, tmp_path / "cache")
    assert _nothing_written(tmp_path / "cache")


def test_scrub_ignores_ordinary_code():
    assert cache_writer.find_secrets(CLEAN_WRAPPER + "task-runner-configuration-value = 1\n", []) == []


# --- verbatim copy blocks the write --------------------------------------------


def test_verbatim_copy_blocks_write(tmp_path):
    # Re-wrapped and re-cased into a docstring: still the same 60+ words.
    copied = README_PARAGRAPH.upper().replace(", ", ",\n    ")
    wrapper = CLEAN_WRAPPER.replace('"""Run `frobnicate --json` on a directory."""', f'"""{copied}"""')
    with pytest.raises(VerbatimCopyDetectedError, match="README.md"):
        cache_writer.write_entry(REF, make_build(wrapper), tmp_path / "cache")
    assert _nothing_written(tmp_path / "cache")


def test_quoting_a_short_readme_command_is_fine(tmp_path):
    wrapper = CLEAN_WRAPPER + "\n# Wraps: frobnicate --json .\n# Frobnicate reads every widget in the input directory\n"
    cache_writer.write_entry(REF, make_build(wrapper), tmp_path / "cache")


# --- smoke test blocks the write ------------------------------------------------


def test_failing_smoke_test_blocks_write(tmp_path, smoke_test):
    smoke_test.side_effect = WrapperRuntimeError("self-test failed (exit 1)")
    with pytest.raises(SmokeTestFailedError, match="not added to cache"):
        cache_writer.write_entry(REF, make_build(), tmp_path / "cache")
    assert _nothing_written(tmp_path / "cache")


# --- license flag (never blocks) -------------------------------------------------

GPL3 = "GNU GENERAL PUBLIC LICENSE\n                       Version 3, 29 June 2007\n"
AGPL = "GNU AFFERO GENERAL PUBLIC LICENSE\n Version 3, 19 November 2007\n"


@pytest.mark.parametrize(
    ("files", "license_id", "flag_starts"),
    [
        ({"LICENSE": MIT}, "MIT", None),
        ({"LICENSE.md": "Apache License\nVersion 2.0, January 2004\n"}, "Apache-2.0", None),
        ({"COPYING": GPL3}, "GPL-3.0", "copyleft"),
        ({"LICENSE": AGPL}, "AGPL-3.0", "copyleft"),
        ({"LICENSE": "GNU LESSER GENERAL PUBLIC LICENSE\nVersion 3\n"}, "LGPL", None),
        ({}, "none", "missing"),
        ({"LICENSE": "All rights reserved. Ask me first."}, "unrecognized (LICENSE)", "unrecognized"),
        ({"LICENSE-MIT": MIT, "LICENSE-GPL": GPL3}, "GPL-3.0 OR MIT", "copyleft"),
        # The GPL-3.0 names the Affero license in section 13 (fresh QA, 2026-10-03)
        ({"LICENSE": GPL3 + "13. Use with the GNU Affero General Public License.\n"}, "GPL-3.0", "copyleft"),
        ({"LICENSE": "Redistribution and use in source and binary forms ... The name of the author may not be used "
          "to endorse or promote products derived from this software."}, "BSD-3-Clause", None),
        ({"LICENSE": "Pillow is licensed under the open source MIT-CMU License:"}, "MIT-CMU", None),
    ],
)
def test_detect_license(tmp_path, files, license_id, flag_starts):
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    info = cache_writer.detect_license(tmp_path)
    assert info.id == license_id
    assert (info.flag or "").startswith(flag_starts or "")
    assert (info.flag is None) == (flag_starts is None)


def test_a_license_kept_in_a_license_folder_is_found(tmp_path):
    """matplotlib: LICENSE/LICENSE, beside the licenses of code it bundles."""
    (tmp_path / "LICENSE").mkdir()
    (tmp_path / "LICENSE" / "LICENSE").write_text("License agreement for matplotlib versions 1.3.0 and later")
    (tmp_path / "LICENSE" / "LICENSE_QT4_EDITOR").write_text(GPL3)
    assert cache_writer.detect_license(tmp_path).id == "Matplotlib (PSF-style)"


def test_copyleft_is_flagged_in_manifest_and_pr_but_still_written(tmp_path):
    entry = cache_writer.write_entry(REF, make_build(license_text=GPL3), tmp_path / "cache")
    assert entry.manifest["license_flag"].startswith("copyleft")
    assert "⚠️ License warning — copyleft" in entry.pr_description
    assert (entry.path / "manifest.json").exists()


# --- staleness (T8: flagged, never blocks) -------------------------------------------

NEWER = "f" * 40


def _existing_entry(cache_dir: Path, sha: str) -> None:
    (cache_dir / "owner__repo").mkdir(parents=True)
    (cache_dir / "owner__repo" / "manifest.json").write_text(json.dumps({"commit_sha": sha}))


def test_up_to_date_build_has_no_staleness_warning(tmp_path, upstream):
    entry = cache_writer.write_entry(REF, make_build(), tmp_path / "cache")
    assert entry.warnings == []
    upstream.assert_called_once_with("https://github.com/owner/repo.git")


def test_stale_existing_entry_is_flagged_and_replaced(tmp_path):
    _existing_entry(tmp_path / "cache", "a" * 40)
    record = make_build()
    entry = cache_writer.write_entry(REF, record, tmp_path / "cache")
    expected = (
        f"Cache entry may be stale — repo has new commits. The existing entry is from {'a' * 12}; "
        f"owner/repo is now at {entry.manifest['commit_sha'][:12]}."
    )
    assert [str(w) for w in entry.warnings] == [expected]
    assert all(isinstance(w, cache_writer.StaleCacheEntryWarning) for w in entry.warnings)
    assert "stale" not in entry.pr_description  # the new entry is current
    assert json.loads((entry.path / "manifest.json").read_text())["commit_sha"] != "a" * 40


def test_existing_entry_at_the_current_commit_is_not_flagged(tmp_path, upstream):
    record = make_build()
    _existing_entry(tmp_path / "cache", _git(Path(record.attempt_dir).parent / "repo", "rev-parse", "HEAD"))
    assert cache_writer.write_entry(REF, record, tmp_path / "cache").warnings == []


def test_a_build_behind_the_repo_is_flagged_in_the_pr_but_still_written(tmp_path, upstream):
    upstream.side_effect = None
    upstream.return_value = NEWER
    entry = cache_writer.write_entry(REF, make_build(), tmp_path / "cache")
    assert len(entry.warnings) == 1
    assert "This build may be stale" in str(entry.warnings[0])
    assert "legwork owner/repo" in str(entry.warnings[0])
    assert f"⚠️ This build may be stale — repo has new commits. It's from {entry.manifest['commit_sha'][:12]}" in entry.pr_description
    assert (entry.path / "manifest.json").exists()


def test_both_stale_gives_both_warnings(tmp_path, upstream):
    upstream.side_effect = None
    upstream.return_value = NEWER
    _existing_entry(tmp_path / "cache", "a" * 40)
    entry = cache_writer.write_entry(REF, make_build(), tmp_path / "cache")
    assert [str(w).split(" — ")[0] for w in entry.warnings] == ["Cache entry may be stale", "This build may be stale"]


def test_offline_staleness_check_warns_and_still_writes(tmp_path, upstream):
    upstream.side_effect = RepoAccessError("Could not resolve host: github.com")
    entry = cache_writer.write_entry(REF, make_build(), tmp_path / "cache")
    assert "Couldn't check owner/repo for new commits" in str(entry.warnings[0])
    assert (entry.path / "manifest.json").exists()


def test_unreadable_existing_manifest_is_not_a_crash(tmp_path):
    (tmp_path / "cache" / "owner__repo").mkdir(parents=True)
    (tmp_path / "cache" / "owner__repo" / "manifest.json").write_text("{not json")
    assert cache_writer.write_entry(REF, make_build(), tmp_path / "cache").warnings == []


def test_remote_head_sha_reads_a_real_remote(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q")
    (origin / "f").write_text("1")
    _git(origin, "add", ".")
    _git(origin, "commit", "-qm", "one")
    assert remote_head_sha(str(origin)) == _git(origin, "rev-parse", "HEAD")


def test_remote_head_sha_on_a_missing_repo_raises(tmp_path):
    with pytest.raises(RepoAccessError, match="Couldn't read the current commit"):
        remote_head_sha(str(tmp_path / "nope"))


# --- CLI ----------------------------------------------------------------------------


def test_contribute_command_writes_the_entry_and_prints_the_pr_description(tmp_path, capsys):
    make_build()
    assert cli.main(["contribute", "owner/repo", "--out", str(tmp_path / "cache")]) == 0
    out = capsys.readouterr().out
    assert "Add Legwork wrapper for owner/repo" in out
    assert (tmp_path / "cache" / "owner__repo" / "manifest.json").exists()


def test_contribute_command_prints_staleness_warnings(tmp_path, capsys, upstream):
    make_build()
    upstream.side_effect = None
    upstream.return_value = NEWER
    assert cli.main(["contribute", "owner/repo", "--out", str(tmp_path / "cache")]) == 0
    assert "Warning: This build may be stale" in capsys.readouterr().out


def test_contribute_command_reports_a_block_and_exits_1(tmp_path, capsys, smoke_test):
    make_build()
    smoke_test.side_effect = WrapperRuntimeError("boom")
    assert cli.main(["contribute", "owner/repo", "--out", str(tmp_path / "cache")]) == 1
    assert "SmokeTestFailedError" in capsys.readouterr().err


def test_contribute_without_a_build_exits_1(tmp_path, capsys):
    assert cli.main(["contribute", "owner/repo", "--out", str(tmp_path / "cache")]) == 1
    assert "legwork owner/repo" in capsys.readouterr().err
