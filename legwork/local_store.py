"""Where built wrappers live on this machine, so `legwork serve` can find
them again. Local only — the shared, versioned public cache is T7.

Layout under LEGWORK_HOME (default ~/.legwork):

    wrappers/<owner>__<repo>/
        builds/<UTC timestamp>/      one folder per `legwork build` run
            repo/                    the clone
            attempt-N/               wrapper.py + .venv of each attempt
        current.json                 points at the latest successful attempt

Each build gets its own folder rather than overwriting the last one: a venv
can't be moved (its scripts hold absolute paths), and a failed rebuild
shouldn't break a wrapper that's already working.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

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


def legwork_home() -> Path:
    return Path(os.environ.get(LEGWORK_HOME_ENV) or Path.home() / ".legwork")


def _wrapper_root(ref: RepoRef) -> Path:
    return legwork_home() / "wrappers" / f"{ref.owner}__{ref.repo}"


def new_build_dir(ref: RepoRef) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = _wrapper_root(ref) / "builds" / stamp
    path.mkdir(parents=True)
    return path


def save_current(ref: RepoRef, attempt_dir: Path, install_command: str, entrypoint: str, model: str) -> BuildRecord:
    record = BuildRecord(
        repo=ref.slug,
        attempt_dir=str(attempt_dir),
        wrapper_path=str(attempt_dir / "wrapper.py"),
        python_path=str(attempt_dir / ".venv" / "bin" / "python"),
        install_command=install_command,
        entrypoint=entrypoint,
        model=model,
        built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    (_wrapper_root(ref) / "current.json").write_text(json.dumps(asdict(record), indent=2) + "\n")
    return record


def load_current(ref: RepoRef) -> BuildRecord:
    pointer = _wrapper_root(ref) / "current.json"
    if not pointer.exists():
        raise NoBuildError(f"No wrapper has been built for {ref.slug} yet — run `legwork {ref.slug}` first.")
    record = BuildRecord(**json.loads(pointer.read_text()))
    missing = [p for p in (record.wrapper_path, record.python_path) if not Path(p).exists()]
    if missing:
        raise NoBuildError(
            f"The saved wrapper for {ref.slug} is incomplete (missing {', '.join(missing)}) — "
            f"rebuild it with `legwork {ref.slug}`."
        )
    return record
