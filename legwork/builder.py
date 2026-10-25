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
    netlog,
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
        # Most repos move on daily, and the cached wrapper is tested on this
        # machine either way; the old wording read as a warning on most
        # installs (fresh QA, 2026-10-03).
        progress(
            f"The cached wrapper was written for an earlier commit ({built_from[:12]}; now {current[:12]}); "
            "it's tested here before use."
        )


def _from_cache(ref: RepoRef, progress: Callable[[str], None], commit: str | None = None) -> tuple[BuildOutcome | None, str]:
    """(outcome, state): state is "hit", "miss", "unreachable" or "failed"."""
    try:
        cached = cache_reader.fetch(ref)
    except cache_reader.CacheUnavailableError as exc:
        progress(f"(skipping the Legwork cache: {exc})")
        return None, "unreachable"
    if cached is None:
        return None, "miss"
    progress(
        f"Found {ref.slug} in the Legwork cache (written by {cached.manifest['llm_model']}); "
        "installing and testing it — no model call needed"
    )
    _warn_if_stale(ref, cached, progress)
    result = retry_loop.run_cached(
        ref.slug, local_store.new_build_dir(ref), cached.install_command, cached.wrapper_code, progress=progress,
        commit=commit,
    )
    _record_install(ref, "cache", result)
    if not result.success:
        detail = codegen.failure_summary(result.attempts[-1].detail, 300)
        progress(f"The cached wrapper didn't pass here ({detail}); writing a fresh one.")
        return None, "failed"
    record = local_store.save_current(
        ref,
        result.attempt_dir,
        install_command=cached.install_command,
        entrypoint=cached.entrypoint,
        model=f"{cached.manifest['llm_model']} (Legwork cache)",
        install_hosts=_host_list(result.contacted),
        install_hosts_complete=result.network_complete,
    )
    return BuildOutcome(ok=True, ref=ref, record=record, from_cache=True, attempts=1), "hit"


def _host_list(contacted: list[dict]) -> tuple[str, ...]:
    return tuple(sorted({f"{h['host']}:{h['port']}" for h in contacted if h.get("connections")}))


def _record_install(ref: RepoRef, source: str, result) -> None:
    """Append what this build's installs reached to the tool's record, from
    outside the sandbox (issue #5). A failed record never fails a build."""
    try:
        netlog.record(local_store.records_path(ref), {
            "event": "install", "repo": ref.slug, "source": source, "success": result.success,
            "complete": result.network_complete, "hosts": result.contacted,
        })
    except OSError:
        pass


def record_approval(ref: RepoRef, command: str, how: str) -> None:
    """The install command the user approved for a fresh build, before it
    ran (issue #4), appended to the tool's record from outside the sandbox."""
    try:
        netlog.record(local_store.records_path(ref), {"event": "approve", "repo": ref.slug, "how": how, "command": command})
    except OSError:
        pass


def _no_key_message(ref: RepoRef, cache_state: str, exc: Exception) -> str:
    """Why a model key is needed, accurately. A client with no key once told its
    user Legwork needed a key to install anything; offline, Legwork said a cached
    repo wasn't cached; a misspelt repo asked for a key (QA, 2026-10-02/03)."""
    if cache_state == "miss":
        try:
            repo_fetcher.check_repo(ref.slug)
        except (RepoNotFoundError, RepoAccessError) as missing:
            if isinstance(missing, RepoNotFoundError):
                return f"{ref.slug} wasn't found on GitHub (or it's private). Check the name, or use find_tools."
    why = {
        "miss": f"{ref.slug} isn't in the Legwork public cache, so building it needs that key.",
        "unreachable": f"Legwork couldn't reach its public cache to check for {ref.slug}, and building without it needs that key. Try again when you're online.",
        "failed": f"The cached wrapper for {ref.slug} didn't pass on this machine, and writing a fresh one needs that key.",
    }.get(cache_state, f"Building {ref.slug} needs that key.")
    return (
        f"{exc}\n{why} Without a key, choose a repo that find_tools marks 'in the Legwork cache': those install "
        "with no key, in about a minute."
    )


def build(
    ref: RepoRef,
    use_cache: bool = True,
    progress: Callable[[str], None] = _quiet,
    llm_config: LLMConfig | None = None,
    commit: str | None = None,
    *,
    approve: retry_loop.Approve,
) -> BuildOutcome:
    """Build `ref`, from the cache when possible. Never raises for an
    ordinary failure: the outcome says what happened. `commit` (a full SHA
    or a tag) builds exactly that commit instead of the default branch (#4).
    `approve` is asked before an install command a model wrote runs (a
    cached tool's command is fixed, and shown by a preview beforehand)."""
    try:
        cache_state = "off"
        if use_cache:
            hit, cache_state = _from_cache(ref, progress, commit)
            if hit is not None:
                return hit
        try:
            config = llm_config or LLMConfig.from_env()
        except LLMAuthError as exc:
            return BuildOutcome(ok=False, ref=ref, error=_no_key_message(ref, cache_state, exc))
        if looks_like_ollama(config.endpoint):
            progress(OLLAMA_TIP)
        build_dir = local_store.new_build_dir(ref)
        progress(f"Building an MCP wrapper for {ref.slug} (usually 1-3 minutes)")
        result = retry_loop.run(ref.slug, build_dir, config, progress=progress, commit=commit, approve=approve)
    except BUILD_ERRORS as exc:
        return BuildOutcome(ok=False, ref=ref, error=f"{type(exc).__name__}: {exc}")

    _record_install(ref, "fresh", result)
    if not result.success and result.attempts and result.attempts[-1].outcome == "InstallNotApproved":
        return BuildOutcome(
            ok=False, ref=ref, attempts=len(result.attempts),
            error=f"The model's install command for {ref.slug} wasn't approved, so none of it ran "
                  f"({result.attempts[-1].detail.split(': ', 1)[-1]}).",
        )
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
        install_hosts=_host_list(result.contacted),
        install_hosts_complete=result.network_complete,
    )
    return BuildOutcome(ok=True, ref=ref, record=record, attempts=len(result.attempts))
