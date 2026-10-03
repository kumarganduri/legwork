"""Copying the files a served tool makes to a folder the user can find
(pre-launch review, 2026-10-03). The tool's folder is writable by untrusted
code, so the copy must not follow links or carry an execute bit."""

from __future__ import annotations

import os
import platform
import subprocess

import pytest

from legwork import outputs


def _tool_folder(tmp_path):
    work = tmp_path / "attempt-1"
    work.mkdir()
    return work


def test_files_made_during_the_call_are_copied_without_an_execute_bit(tmp_path):
    work = _tool_folder(tmp_path)
    since = outputs.snapshot(work)
    (work / "trim.mp4").write_bytes(b"video")
    (work / "run.sh").write_text("#!/bin/sh\necho hi\n")
    os.chmod(work / "run.sh", 0o755)
    copies = outputs.collect(work, since, tmp_path / "out", "moviepy")
    names = sorted(c.name for c in copies)
    assert names == ["run.sh", "trim.mp4"]
    for c in copies:
        assert c.parent == tmp_path / "out" / "moviepy"
        assert os.stat(c).st_mode & 0o777 == 0o644
    assert (tmp_path / "out" / "moviepy" / "trim.mp4").read_bytes() == b"video"


@pytest.mark.skipif(platform.system() != "Darwin", reason="macOS quarantine flag")
def test_copies_get_the_quarantine_flag_on_macos(tmp_path):
    work = _tool_folder(tmp_path)
    since = outputs.snapshot(work)
    (work / "chart.png").write_bytes(b"png")
    [copy] = outputs.collect(work, since, tmp_path / "out", "plotly")
    flag = subprocess.run(["/usr/bin/xattr", "-p", "com.apple.quarantine", str(copy)], capture_output=True, text=True)
    assert flag.returncode == 0 and "Legwork" in flag.stdout


def test_links_old_files_and_the_tools_own_folders_are_never_copied(tmp_path):
    work = _tool_folder(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("private")
    old = work / "old.txt"
    old.write_text("there before the call")
    since = outputs.snapshot(work)
    (work / "link.txt").symlink_to(secret)
    os.link(secret, work / "hardlink.txt")  # a hard link to a file of yours
    for folder in (".venv", "src", "models", "node_modules", ".tmp"):
        (work / folder).mkdir()
        (work / folder / "x.bin").write_bytes(b"x")
    (work / "wrapper.py").write_text("# the wrapper itself")
    assert outputs.collect(work, since, tmp_path / "out", "t") == []


def test_names_dont_overwrite_earlier_copies(tmp_path):
    work = _tool_folder(tmp_path)
    for _ in range(2):
        since = outputs.snapshot(work)
        (work / "result.txt").write_text(f"run {since}")
        outputs.collect(work, since, tmp_path / "out", "t")
    assert sorted(p.name for p in (tmp_path / "out" / "t").iterdir()) == ["result (2).txt", "result.txt"]


def test_library_caches_and_hidden_files_are_not_results(tmp_path):
    """HOME is the tool's folder, so onnxruntime's database under
    Library/Application Support was copied next to rembg's image (2026-10-03)."""
    work = _tool_folder(tmp_path)
    since = outputs.snapshot(work)
    support = work / "Library" / "Application Support" / "Microsoft" / ".onnxruntime"
    support.mkdir(parents=True)
    (support / "onnxruntime.db-wal").write_bytes(b"x")
    (work / ".config").mkdir()
    (work / ".config" / "settings.json").write_text("{}")
    (work / ".DS_Store").write_bytes(b"x")
    (work / "outputs").mkdir()
    (work / "outputs" / "photo.out.png").write_bytes(b"png")
    (work / "outputs" / "rows.db-journal").write_bytes(b"x")
    assert [c.name for c in outputs.collect(work, since, tmp_path / "out", "rembg")] == ["photo.out.png"]
