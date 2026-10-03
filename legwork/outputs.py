"""Copy the files a served tool makes to a folder you can find.

Served tools can only write inside their own folder (deep under ~/.legwork),
because the folders you grant are read-only. "Where's my file?" after "trim
this video" was the moment advisors expected people to give up (pre-launch
review, 2026-10-03). So after each call the hub, outside the sandbox, copies
the files the call created to ~/Legwork/outputs/<tool>/ and says where.

The tool's folder is writable by untrusted code, so copying is careful: it
never follows links, skips hard-linked files (a link to a file of yours),
copies only regular files made during the call, caps sizes and counts,
drops any execute bit, and marks copies on macOS with the quarantine flag
that downloaded files get.
"""

from __future__ import annotations

import os
import platform
import stat
import subprocess
import time
from pathlib import Path

DEFAULT_DIR = Path.home() / "Legwork" / "outputs"
MAX_FILES = 20
MAX_FILE_BYTES = 1024**3  # 1 GiB
MAX_TOTAL_BYTES = 2 * 1024**3
# The tool's own machinery, not results. HOME is the tool's folder, so
# libraries' caches and settings land there too: hidden folders, and macOS's
# Library (onnxruntime keeps a database under Library/Application Support).
_SKIP_DIRS = {"venv", "node_modules", "src", "models", "__pycache__", "nltk_data", "gitleaks-bin", "bin", "Library"}
_SKIP_FILES = {"wrapper.py", "legwork-install.sh"}
_SKIP_SUFFIXES = ("-wal", "-shm", "-journal")  # SQLite's working files


Snapshot = dict[str, tuple[int, int]]  # relative path -> (mtime_ns, size)


def snapshot(workdir: Path) -> Snapshot:
    """What's in the tool's folder now, so collect() can tell what a call made.
    A time window copied a self-test leftover written just before the call."""
    return {str(path.relative_to(workdir)): (st.st_mtime_ns, st.st_size) for path, st in _candidates(workdir)}


def collect(workdir: Path, before: Snapshot, dest_root: Path, label: str) -> list[Path]:
    """Copy regular files under `workdir` that are new or changed since
    `before` into dest_root/label/. Returns the copies' paths. Never raises
    for a file it can't copy; it skips it."""
    found = [
        (path, st) for path, st in _candidates(workdir)
        if before.get(str(path.relative_to(workdir))) != (st.st_mtime_ns, st.st_size) and st.st_size <= MAX_FILE_BYTES
    ]
    found.sort(key=lambda x: x[1].st_mtime_ns)
    copies: list[Path] = []
    total = 0
    for path, st in found[:MAX_FILES]:
        if total + st.st_size > MAX_TOTAL_BYTES:
            break
        dest_dir = dest_root / label
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = _copy_no_follow(path, _unique(dest_dir / path.name))
        except OSError:
            continue
        total += st.st_size
        copies.append(dest)
    return copies


def _candidates(workdir: Path):
    """Plain, singly-linked files outside the tool's own machinery."""
    for dirpath, dirnames, filenames in os.walk(workdir, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if name.startswith(".") or name.endswith(_SKIP_SUFFIXES) or (name in _SKIP_FILES and Path(dirpath) == workdir):
                continue
            path = Path(dirpath) / name
            try:
                st = os.lstat(path)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) and st.st_nlink == 1:
                yield path, st


def _unique(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    for i in range(2, 1000):
        candidate = dest.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
    return dest.with_name(f"{stem} ({time.time_ns()}){suffix}")


def _copy_no_follow(src: Path, dest: Path) -> Path:
    src_fd = os.open(src, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(src_fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:  # swapped since the walk
            raise OSError("not a plain file")
        dest_fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        try:
            while chunk := os.read(src_fd, 1024 * 1024):
                os.write(dest_fd, chunk)
            os.fchmod(dest_fd, 0o644)  # never executable
        finally:
            os.close(dest_fd)
    finally:
        os.close(src_fd)
    _quarantine(dest)
    return dest


def _quarantine(path: Path) -> None:
    """macOS's mark for downloaded files: Gatekeeper checks it before opening."""
    if platform.system() != "Darwin":
        return
    value = f"0081;{int(time.time()):08x};Legwork;"
    subprocess.run(
        ["/usr/bin/xattr", "-w", "com.apple.quarantine", value, str(path)],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=10, check=False,
    )
