"""Where built wrappers live on this machine, so `legwork serve` can find
them again. Local only — cache_writer turns a build into a public-cache
entry (`legwork contribute`).

Layout under LEGWORK_HOME (default ~/.legwork):

    wrappers/<owner>__<repo>/
        builds/<UTC timestamp>/      one folder per `legwork build` run
            repo/                    the clone
            attempt-N/               wrapper.py + .venv of each attempt
        current.json                 points at the latest successful attempt, and
                                     records what it was built from (issue #4)

Each build gets its own folder rather than overwriting the last one: a venv
can't be moved (its scripts hold absolute paths), and a failed rebuild
shouldn't break a wrapper that's already working.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

from legwork import repo_fetcher
from legwork.repo_fetcher import RepoRef

LEGWORK_HOME_ENV = "LEGWORK_HOME"


class NoBuildError(Exception):
    """No successful build is recorded for this repo, or its files are gone."""


@dataclass(frozen=True)
class BuildRecord:
    repo: str
    attempt_dir: str
    wrapper_path: str
    python_path: str
    install_command: str
    entrypoint: str
    model: str
    built_at: str
    # What the build came from (issue #4). Empty in records from before 0.7.12.
    commit_sha: str = ""  # the commit that was cloned and scanned
    wrapper_sha256: str = ""
    packages: tuple[str, ...] = ()  # what the install resolved: "pip:name==1.2", "npm:name@1.2"
    install_hosts: tuple[str, ...] = ()  # "host:port" reached through Legwork's proxy (issue #5)
    install_hosts_complete: bool = False  # strict mode: the install could reach nothing else


def legwork_home() -> Path:
    return Path(os.environ.get(LEGWORK_HOME_ENV) or Path.home() / ".legwork")


def _wrapper_root(ref: RepoRef) -> Path:
    return legwork_home() / "wrappers" / f"{ref.owner}__{ref.repo}"


def new_build_dir(ref: RepoRef) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = _wrapper_root(ref) / "builds" / stamp
    path.mkdir(parents=True)
    return path


def records_path(ref: RepoRef) -> Path:
    """A tool's append-only record (netlog.record): outside every folder its
    sandboxed code can write, so the code can't edit what it's recorded doing."""
    return legwork_home() / "records" / f"{ref.owner}__{ref.repo}.jsonl"


def save_current(
    ref: RepoRef, attempt_dir: Path, install_command: str, entrypoint: str, model: str,
    install_hosts: tuple[str, ...] = (), install_hosts_complete: bool = False,
) -> BuildRecord:
    try:
        commit = repo_fetcher.head_sha(attempt_dir.parent / "repo")
    except Exception:  # noqa: BLE001 — a record without a commit beats no record
        commit = ""
    record = BuildRecord(
        repo=ref.slug,
        attempt_dir=str(attempt_dir),
        wrapper_path=str(attempt_dir / "wrapper.py"),
        python_path=str(attempt_dir / ".venv" / "bin" / "python"),
        install_command=install_command,
        entrypoint=entrypoint,
        model=model,
        built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        commit_sha=commit,
        wrapper_sha256=_sha256(attempt_dir / "wrapper.py"),
        packages=tuple(installed_packages(attempt_dir)),
        install_hosts=tuple(install_hosts),
        install_hosts_complete=install_hosts_complete,
    )
    (_wrapper_root(ref) / "current.json").write_text(json.dumps(asdict(record), indent=2) + "\n")
    return record


def load_current(ref: RepoRef) -> BuildRecord:
    pointer = _wrapper_root(ref) / "current.json"
    if not pointer.exists():
        raise NoBuildError(f"No wrapper has been built for {ref.slug} yet — run `legwork {ref.slug}` first.")
    data = json.loads(pointer.read_text())
    known = {f.name for f in fields(BuildRecord)}
    record = BuildRecord(**{k: (tuple(v) if k in ("packages", "install_hosts") else v) for k, v in data.items() if k in known})
    missing = [p for p in (record.wrapper_path, record.python_path) if not Path(p).exists()]
    if missing:
        raise NoBuildError(
            f"The saved wrapper for {ref.slug} is incomplete (missing {', '.join(missing)}) — "
            f"rebuild it with `legwork {ref.slug}`."
        )
    return record


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


MAX_PACKAGES = 5000
_MAX_METADATA_BYTES = 256 * 1024


def _read_no_follow(path: Path) -> str | None:
    """A small regular file in the tool's folder, read from outside the
    sandbox without following links (install code could plant one pointing
    at a file of yours)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        return os.read(fd, _MAX_METADATA_BYTES).decode("utf-8", errors="replace")
    finally:
        os.close(fd)


def installed_packages(attempt_dir: Path) -> list[str]:
    """What an install resolved to, read from package metadata files without
    running any of the tool's code (a venv's python runs .pth files on start).
    It's what the install says it installed: the install could have written
    these files itself, so it's a record, not proof."""
    found: set[str] = set()
    for site in sorted((attempt_dir / ".venv" / "lib").glob("python*/site-packages")):
        for info in sorted(site.glob("*.dist-info")):
            text = _read_no_follow(info / "METADATA") or ""
            name = re.search(r"^Name:\s*(\S+)", text, re.MULTILINE)
            version = re.search(r"^Version:\s*(\S+)", text, re.MULTILINE)
            if name and version:
                found.add(f"pip:{name.group(1)}=={version.group(1)}")
    modules = attempt_dir / "node_modules"
    for package_json in sorted([*modules.glob("*/package.json"), *modules.glob("@*/*/package.json")]):
        try:
            meta = json.loads(_read_no_follow(package_json) or "{}")
        except ValueError:
            continue
        if isinstance(meta, dict) and isinstance(meta.get("name"), str) and isinstance(meta.get("version"), str):
            found.add(f"npm:{meta['name']}@{meta['version']}")
    return sorted(found)[:MAX_PACKAGES]


def disk_usage(path: Path) -> int:
    """Bytes under `path`, not following symlinks (a venv links to its base
    Python, which isn't Legwork's to count)."""
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def cleanup_targets(ref: RepoRef | None = None, everything: bool = False) -> list[Path]:
    """What `legwork clean` removes: every build except each repo's current
    one (serve runs it; contribute reads its clone), or with `everything`,
    whole repo folders. Only ever paths inside LEGWORK_HOME/wrappers."""
    root = legwork_home() / "wrappers"
    if not root.is_dir():
        return []
    repos = [_wrapper_root(ref)] if ref is not None else sorted(p for p in root.iterdir() if p.is_dir())
    targets: list[Path] = []
    for repo_dir in repos:
        if not repo_dir.is_dir():
            continue
        if everything:
            targets.append(repo_dir)
            continue
        current = None
        pointer = repo_dir / "current.json"
        if pointer.exists():
            try:
                current = Path(json.loads(pointer.read_text())["attempt_dir"])
            except (ValueError, KeyError, OSError):
                current = None
        if current is None or not current.exists():
            targets.append(repo_dir)  # nothing here works; it all goes
            continue
        keep = current.parent
        builds = sorted(p for p in (repo_dir / "builds").glob("*") if p.is_dir())
        targets += [b for b in builds if b.resolve() != keep.resolve()]
        # Inside the kept build: environments of attempts that failed, and
        # any download cache a crashed build left behind.
        targets += [a / ".venv" for a in sorted(keep.glob("attempt-*")) if a != current and (a / ".venv").is_dir()]
        if (keep / ".download-cache").is_dir():
            targets.append(keep / ".download-cache")
    resolved_root = root.resolve()
    return [t for t in targets if t.resolve().is_relative_to(resolved_root) and t.resolve() != resolved_root]

