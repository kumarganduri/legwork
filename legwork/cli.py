"""The `legwork` command.

    legwork <repo>          build an MCP wrapper for a GitHub repo
    legwork build <repo>    same, spelled out
    legwork serve <repo>    run the built wrapper as an MCP server over stdio
    legwork clean [<repo>] [--all] [--dry-run]
                            free disk space: old and failed builds (a build
                            with PyTorch is several GB)
    legwork contribute <repo> [--out DIR]
                            write the built wrapper + manifest as a public-cache
                            entry (default DIR: ./cache), ready for a PR

`build` needs LEGWORK_LLM_ENDPOINT / LEGWORK_LLM_API_KEY / LEGWORK_LLM_MODEL
set (an OpenAI-compatible endpoint). `serve` needs no model at all — it's
what an MCP client (Claude Code, Cursor, Claude Desktop) launches.

`serve` never writes to stdout itself: stdout is the MCP transport, and a
stray line would corrupt the client's stream. Errors go to stderr.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import re
import shlex
import shutil
import sys
import urllib.parse
from pathlib import Path

from legwork import (
    cache_reader,
    cache_writer,
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
    parse_repo_url,
)

# Runs inside the sandbox with the wrapper's own venv Python. Legwork's
# code, not the model's: it loads wrapper.py WITHOUT running its __main__
# self-test, finds the FastMCP server object, and runs it on stdio. So
# serving works without the model having written any startup code.
_SERVE_LAUNCHER = """\
import importlib.util, sys
from mcp.server.fastmcp import FastMCP
path = sys.argv[1]
spec = importlib.util.spec_from_file_location("legwork_wrapper", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
servers = [v for v in vars(module).values() if isinstance(v, FastMCP)]
if len(servers) != 1:
    sys.exit(f"legwork serve: expected one FastMCP server in {path}, found {len(servers)}")
servers[0].run()
"""

_BUILD_ERRORS = (
    InvalidRepoURLError,
    RepoNotFoundError,
    RepoAccessError,
    ObfuscatedPayloadDetectedError,
    NoReadmeError,
    retry_loop.TotalRunTimeoutExceeded,
    sandbox_runner.SandboxUnavailableError,
    LLMAuthError,
)


def _err(message: str) -> None:
    print(f"legwork: {message}", file=sys.stderr)


# The PyPI name. `legwork` there is an unrelated astrophysics package.
DIST_NAME = "legwork-mcp"


def _in_uvx_env() -> bool:
    # uvx runs tools from a throwaway environment in uv's cache, which
    # `uv cache clean` deletes: never hand that path to an MCP client.
    return "archive-v0" in Path(sys.prefix).parts


def _install_source() -> str | None:
    """Where this install came from (PEP 610 direct_url.json) in a form
    `uvx --from` accepts, or None when it came from PyPI."""
    try:
        raw = importlib.metadata.distribution(DIST_NAME).read_text("direct_url.json")
    except importlib.metadata.PackageNotFoundError:
        return None
    if not raw:
        return None
    info = json.loads(raw)
    url = info["url"]
    if "vcs_info" in info:
        revision = info["vcs_info"].get("requested_revision")
        return f"{info['vcs_info']['vcs']}+{url}" + (f"@{revision}" if revision else "")
    if url.startswith("file://"):
        return urllib.parse.unquote(urllib.parse.urlparse(url).path)
    return url


def _self_command() -> list[str]:
    """How an MCP client should launch this same legwork install."""
    if _in_uvx_env():
        uvx = shutil.which("uvx") or "uvx"
        source = _install_source()
        return [uvx, "--from", source, "legwork"] if source else [uvx, DIST_NAME]
    argv0 = sys.argv[0]
    if argv0.endswith(".py"):  # python -m legwork.cli, or the file directly
        return [sys.executable, "-m", "legwork.cli"]
    # Absolute, since MCP clients launch from their own working directory.
    # abspath, not resolve(): keep ~/.local/bin/legwork rather than
    # following its symlink into uv's tool folder.
    return [os.path.abspath(shutil.which(argv0) or argv0)]


def _server_name(ref: RepoRef) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", ref.repo.lower()).strip("-") or "legwork-tool"


def _print_connect_instructions(ref: RepoRef) -> None:
    launch = [*_self_command(), "serve", ref.slug]
    name = _server_name(ref)
    config = {"mcpServers": {name: {"command": launch[0], "args": launch[1:]}}}
    print("\nConnect it to Claude Code:")
    print(f"  claude mcp add {name} -- {shlex.join(launch)}")
    print("\nOr add this to any MCP client's config (Claude Desktop, Cursor):")
    print(json.dumps(config, indent=2))


def _progress(message: str) -> None:
    print(f"  {message}", file=sys.stderr)


def _warn_if_stale(ref: RepoRef, cached: cache_reader.CachedWrapper) -> None:
    try:
        current = repo_fetcher.remote_head_sha(ref.clone_url)
    except RepoAccessError:
        return
    built_from = cached.manifest["commit_sha"]
    if current != built_from:
        print(
            f"  Cache entry may be stale — repo has new commits (cached from {built_from[:12]}, now at "
            f"{current[:12]}). It usually still works; `legwork --no-cache {ref.slug}` writes a fresh one.",
            file=sys.stderr,
        )


def _build_from_cache(ref: RepoRef) -> tuple[retry_loop.RunResult, cache_reader.CachedWrapper] | None:
    """A verified build from the public cache, or None to build fresh.
    Precondition failures (repo gone, malware found) propagate."""
    try:
        cached = cache_reader.fetch(ref)
    except cache_reader.CacheUnavailableError as exc:
        print(f"  (skipping the Legwork cache: {exc})", file=sys.stderr)
        return None
    if cached is None:
        return None
    print(
        f"Found {ref.slug} in the Legwork cache (written by {cached.manifest['llm_model']}); "
        "installing and testing it — no model call needed",
        file=sys.stderr,
    )
    _warn_if_stale(ref, cached)
    result = retry_loop.run_cached(
        ref.slug, local_store.new_build_dir(ref), cached.install_command, cached.wrapper_code, progress=_progress
    )
    if not result.success:
        detail = codegen.failure_summary(result.attempts[-1].detail, 300)
        print(f"  The cached wrapper didn't pass here ({detail}); writing a fresh one.", file=sys.stderr)
        return None
    return result, cached


def cmd_build(repo: str, use_cache: bool = True) -> int:
    try:
        ref = parse_repo_url(repo)
    except InvalidRepoURLError as exc:
        _err(str(exc))
        return 1

    if use_cache:
        try:
            hit = _build_from_cache(ref)
        except _BUILD_ERRORS as exc:
            _err(f"{type(exc).__name__}: {exc}")
            return 1
        if hit is not None:
            result, cached = hit
            record = local_store.save_current(
                ref,
                result.attempt_dir,
                install_command=cached.install_command,
                entrypoint=cached.entrypoint,
                model=f"{cached.manifest['llm_model']} (Legwork cache)",
            )
            print(f"\nInstalled the cached MCP wrapper for {ref.slug}; it passed its self-test here.")
            print(f"  What it wraps: {record.entrypoint}")
            print(f"  Saved to: {record.attempt_dir} ({local_store.human_size(local_store.disk_usage(Path(record.attempt_dir).parent))})")
            _print_connect_instructions(ref)
            return 0

    try:
        config = LLMConfig.from_env()
    except LLMAuthError as exc:
        _err(f"{exc}\n  If you keep them in a file: source ~/.legwork.env")
        return 1

    build_dir = local_store.new_build_dir(ref)
    print(f"Building an MCP wrapper for {ref.slug} (usually 1-3 minutes)", file=sys.stderr)
    try:
        result = retry_loop.run(ref.slug, build_dir, config, progress=_progress)
    except _BUILD_ERRORS as exc:
        _err(f"{type(exc).__name__}: {exc}")
        return 1

    if not result.success:
        _err(f"no working wrapper for {ref.slug}.")
        log = build_dir / "attempts.log"
        log.write_text("".join(f"=== attempt {a.attempt_number}: {a.outcome}\n{a.detail}\n\n" for a in result.attempts))
        for a in result.attempts:
            print(f"  attempt {a.attempt_number}: {a.outcome} — {codegen.failure_summary(a.detail, 300)}", file=sys.stderr)
        print(f"  Full error output: {log}", file=sys.stderr)
        return 1

    record = local_store.save_current(
        ref,
        result.attempt_dir,
        install_command=result.install_command,
        entrypoint=result.attempts[-1].detail,
        model=config.model,
    )
    print(f"\nBuilt an MCP wrapper for {ref.slug} on attempt {len(result.attempts)} of {retry_loop.MAX_WRAPPER_ATTEMPTS}.")
    print(f"  What it wraps: {record.entrypoint}")
    print(f"  Saved to: {record.attempt_dir} ({local_store.human_size(local_store.disk_usage(Path(record.attempt_dir).parent))})")
    _print_connect_instructions(ref)
    return 0


def cmd_serve(repo: str) -> int:
    try:
        ref = parse_repo_url(repo)
        record = local_store.load_current(ref)
    except (InvalidRepoURLError, local_store.NoBuildError) as exc:
        _err(str(exc))
        return 1
    python = Path(record.python_path)
    env = {"PATH": f"{python.parent}:{sandbox_runner.MINIMAL_PATH}"}
    try:
        sandbox_runner.exec_serve([str(python), "-c", _SERVE_LAUNCHER, record.wrapper_path], Path(record.attempt_dir), env)
    except sandbox_runner.SandboxUnavailableError as exc:
        _err(str(exc))
        return 1
    return 0  # pragma: no cover — exec_serve replaces this process


_CONTRIBUTE_ERRORS = (
    InvalidRepoURLError,
    RepoAccessError,
    local_store.NoBuildError,
    cache_writer.VerbatimCopyDetectedError,
    cache_writer.SmokeTestFailedError,
    cache_writer.SecretScrubTriggeredError,
    sandbox_runner.SandboxUnavailableError,
)


def cmd_contribute(repo: str, out: Path) -> int:
    try:
        ref = parse_repo_url(repo)
        record = local_store.load_current(ref)
        print(f"Checking the {ref.slug} wrapper and re-running its self-test", file=sys.stderr)
        entry = cache_writer.write_entry(ref, record, out)
    except _CONTRIBUTE_ERRORS as exc:
        _err(f"{type(exc).__name__}: {exc}")
        return 1

    print(f"\nWrote a cache entry for {ref.slug} to {entry.path}/")
    print("  wrapper.py, manifest.json")
    if entry.manifest["license_flag"]:
        print(f"  License flag: {entry.manifest['license_flag']}")
    for warning in entry.warnings:
        print(f"  Warning: {warning}")
    print("\nOpen a PR to the Legwork repo with that folder under cache/. PR description:\n")
    print(entry.pr_description, end="")
    return 0


def cmd_clean(repo: str | None, everything: bool, dry_run: bool) -> int:
    ref = None
    if repo is not None:
        try:
            ref = parse_repo_url(repo)
        except InvalidRepoURLError as exc:
            _err(str(exc))
            return 1
    targets = local_store.cleanup_targets(ref, everything=everything)
    if not targets:
        print("Nothing to clean.")
        return 0
    total = 0
    for target in targets:
        size = local_store.disk_usage(target)
        total += size
        shown = str(target).replace(str(Path.home()), "~", 1)
        print(f"  {'would remove' if dry_run else 'removed'} {shown} ({local_store.human_size(size)})")
        if not dry_run:
            shutil.rmtree(target)
    verb = "Would free" if dry_run else "Freed"
    print(f"{verb} {local_store.human_size(total)}.")
    if everything and not dry_run:
        print("Rebuild a wrapper with `legwork owner/repo` before serving it again.")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in ("build", "serve", "contribute", "clean", "-h", "--help"):
        # `legwork <repo>` and `legwork --no-cache <repo>` shorthands
        argv.insert(0, "build")

    parser = argparse.ArgumentParser(prog="legwork", description="Point it at a GitHub repo, get a working MCP tool back.")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="build an MCP wrapper for a GitHub repo (also: legwork <repo>)")
    build.add_argument("repo", help="github.com URL or owner/repo")
    build.add_argument("--no-cache", action="store_true", help="always write a fresh wrapper, even if the repo is in the Legwork cache")
    serve = sub.add_parser("serve", help="run a built wrapper as an MCP server over stdio")
    serve.add_argument("repo", help="github.com URL or owner/repo")
    contribute = sub.add_parser("contribute", help="write a built wrapper + manifest as a public-cache entry")
    contribute.add_argument("repo", help="github.com URL or owner/repo")
    contribute.add_argument(
        "--out", type=Path, default=cache_writer.DEFAULT_CACHE_DIR, help="cache folder to write into (default: ./cache)"
    )
    clean = sub.add_parser("clean", help="free disk space: remove old and failed builds")
    clean.add_argument("repo", nargs="?", help="only this repo (default: all)")
    clean.add_argument("--all", action="store_true", help="also remove current builds (rebuild before serving)")
    clean.add_argument("--dry-run", action="store_true", help="show what would be removed")
    args = parser.parse_args(argv)

    if args.command == "build":
        return cmd_build(args.repo, use_cache=not args.no_cache)
    if args.command == "clean":
        return cmd_clean(args.repo, args.all, args.dry_run)
    if args.command == "contribute":
        return cmd_contribute(args.repo, args.out)
    return cmd_serve(args.repo)


if __name__ == "__main__":
    sys.exit(main())
