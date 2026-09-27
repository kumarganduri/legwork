"""One build of one repo: the public cache first, then a fresh wrapper from
the model. Shared by `legwork <repo>` (which prints) and `legwork hub`
(which reports back to the MCP client), so it never writes to stdout itself:
in the hub, stdout is the MCP transport. Progress goes to a callback.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from legwork import (
    cache_reader,
    codegen,
    local_store,
    repo_fetcher,
    retry_loop,
    sandbox_runner,
)
from legwork.llm_client import LLMAuthError, LLMConfig
from legwork.obfuscation_scanner import ObfuscatedPayloadDetectedError
from legwork.readme_parser import InsufficientReadmeError as NoReadmeError
from legwork.repo_fetcher import (
    InvalidRepoURLError,
    RepoAccessError,
    RepoNotFoundError,
    RepoRef,
)

# Stop the build with a clear reason; nothing to retry.
BUILD_ERRORS = (
    InvalidRepoURLError,
    RepoNotFoundError,
    RepoAccessError,
    ObfuscatedPayloadDetectedError,
    NoReadmeError,
    retry_loop.TotalRunTimeoutExceeded,
    sandbox_runner.SandboxUnavailableError,
    LLMAuthError,
)

OLLAMA_TIP = (
    "Using Ollama: start it with OLLAMA_CONTEXT_LENGTH=32768 (or more). Its default window is a few\n"
    "  thousand tokens and it silently cuts longer prompts, so the model never sees the instructions."
)


@dataclass
class BuildOutcome:
    ok: bool
    ref: RepoRef
    record: local_store.BuildRecord | None = None
    from_cache: bool = False
    attempts: int = 0
    error: str = ""  # one line: why it stopped
    attempt_lines: list[str] = field(default_factory=list)  # per-attempt summaries on failure
    log_path: Path | None = None


def _quiet(_message: str) -> None:
    pass


def looks_like_ollama(endpoint: str) -> bool:
    # Provider test, 2026-09-27: Ollama cut every Legwork prompt to 2,050
    # tokens and the model answered nonsense. Ollama's default port is 11434.
    import urllib.parse

    parsed = urllib.parse.urlparse(endpoint)
    return parsed.port == 11434 or "ollama" in (parsed.hostname or "")


def _warn_if_stale(ref: RepoRef, cached: cache_reader.CachedWrapper, progress: Callable[[str], None]) -> None:
    try:
        current = repo_fetcher.remote_head_sha(ref.clone_url)
    except RepoAccessError:
        return
    built_from = cached.manifest["commit_sha"]
    if current != built_from:
        progress(
            f"Cache entry may be stale — repo has new commits (cached from {built_from[:12]}, now at "
            f"{current[:12]}). It usually still works; `legwork --no-cache {ref.slug}` writes a fresh one."
        )


def _from_cache(ref: RepoRef, progress: Callable[[str], None]) -> BuildOutcome | None:
    try:
        cached = cache_reader.fetch(ref)
    except cache_reader.CacheUnavailableError as exc:
        progress(f"(skipping the Legwork cache: {exc})")
        return None
    if cached is None:
        return None
    progress(
        f"Found {ref.slug} in the Legwork cache (written by {cached.manifest['llm_model']}); "
        "installing and testing it — no model call needed"
    )
    _warn_if_stale(ref, cached, progress)
    result = retry_loop.run_cached(
        ref.slug, local_store.new_build_dir(ref), cached.install_command, cached.wrapper_code, progress=progress
    )
    if not result.success:
        detail = codegen.failure_summary(result.attempts[-1].detail, 300)
        progress(f"The cached wrapper didn't pass here ({detail}); writing a fresh one.")
        return None
    record = local_store.save_current(
        ref,
        result.attempt_dir,
        install_command=cached.install_command,
        entrypoint=cached.entrypoint,
        model=f"{cached.manifest['llm_model']} (Legwork cache)",
    )
    return BuildOutcome(ok=True, ref=ref, record=record, from_cache=True, attempts=1)


def build(
    ref: RepoRef,
    use_cache: bool = True,
    progress: Callable[[str], None] = _quiet,
    llm_config: LLMConfig | None = None,
) -> BuildOutcome:
    """Build `ref`, from the cache when possible. Never raises for an
    ordinary failure: the outcome says what happened."""
    try:
        if use_cache:
            hit = _from_cache(ref, progress)
            if hit is not None:
                return hit
        try:
            config = llm_config or LLMConfig.from_env()
        except LLMAuthError as exc:
            return BuildOutcome(ok=False, ref=ref, error=f"{exc}\n  If you keep them in a file: source ~/.legwork.env")
        if looks_like_ollama(config.endpoint):
            progress(OLLAMA_TIP)
        build_dir = local_store.new_build_dir(ref)
        progress(f"Building an MCP wrapper for {ref.slug} (usually 1-3 minutes)")
        result = retry_loop.run(ref.slug, build_dir, config, progress=progress)
    except BUILD_ERRORS as exc:
        return BuildOutcome(ok=False, ref=ref, error=f"{type(exc).__name__}: {exc}")

    if not result.success:
        log = build_dir / "attempts.log"
        log.write_text("".join(f"=== attempt {a.attempt_number}: {a.outcome}\n{a.detail}\n\n" for a in result.attempts))
        lines = [f"attempt {a.attempt_number}: {a.outcome} — {codegen.failure_summary(a.detail, 300)}" for a in result.attempts]
        return BuildOutcome(
            ok=False, ref=ref, attempts=len(result.attempts), error=f"no working wrapper for {ref.slug}.",
            attempt_lines=lines, log_path=log,
        )
    record = local_store.save_current(
        ref,
        result.attempt_dir,
        install_command=result.install_command,
        entrypoint=result.attempts[-1].detail,
        model=config.model,
    )
    return BuildOutcome(ok=True, ref=ref, record=record, attempts=len(result.attempts))
