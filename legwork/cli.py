"""The `legwork` command.

    legwork <repo>          build an MCP wrapper for a GitHub repo
    legwork build <repo>    same, spelled out
    legwork serve <repo>    run the built wrapper as an MCP server over stdio
    legwork find "<what you need>"
                            search GitHub for tools that do it, with facts to choose by
    legwork hub [--allow-read PATH]... [--allow-net]
                            one MCP server through which your AI finds, installs
                            and uses tools on demand, within the limits you set
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

from legwork import (  # noqa: F401 — cache_reader/repo_fetcher kept as patch points for tests
    builder,
    cache_reader,
    cache_writer,
    local_store,
    repo_fetcher,
    retry_loop,
    sandbox_runner,
)
from legwork.repo_fetcher import (
    InvalidRepoURLError,
    RepoAccessError,
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


def cmd_build(repo: str, use_cache: bool = True) -> int:
    try:
        ref = parse_repo_url(repo)
    except InvalidRepoURLError as exc:
        _err(str(exc))
        return 1
    outcome = builder.build(ref, use_cache=use_cache, progress=_progress)
    if not outcome.ok:
        _err(outcome.error)
        for line in outcome.attempt_lines:
            print(f"  {line}", file=sys.stderr)
        if outcome.log_path:
            print(f"  Full error output: {outcome.log_path}", file=sys.stderr)
        return 1
    record = outcome.record
    if outcome.from_cache:
        print(f"\nInstalled the cached MCP wrapper for {ref.slug}; it passed its self-test here.")
    else:
        print(f"\nBuilt an MCP wrapper for {ref.slug} on attempt {outcome.attempts} of {retry_loop.MAX_WRAPPER_ATTEMPTS}.")
    print(f"  What it wraps: {record.entrypoint}")
    size = local_store.human_size(local_store.disk_usage(Path(record.attempt_dir).parent))
    print(f"  Saved to: {record.attempt_dir} ({size})")
    _print_connect_instructions(ref)
    return 0


def cmd_serve(repo: str, allow_read: list[str] | None = None, allow_net: bool = False) -> int:
    try:
        ref = parse_repo_url(repo)
        record = local_store.load_current(ref)
        grants = sandbox_runner.check_read_grants(allow_read or [])
    except (InvalidRepoURLError, local_store.NoBuildError, sandbox_runner.SandboxGrantError) as exc:
        _err(str(exc))
        return 1
    python = Path(record.python_path)
    env = {"PATH": f"{python.parent}:{sandbox_runner.MINIMAL_PATH}"}
    try:
        sandbox_runner.exec_serve(
            [str(python), "-c", _SERVE_LAUNCHER, record.wrapper_path],
            Path(record.attempt_dir),
            env,
            allow_read=grants,
            allow_network=allow_net,
        )
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


def cmd_find(query: str) -> int:
    from legwork import discovery

    try:
        print(discovery.describe(discovery.find(query)))
    except discovery.DiscoveryError as exc:
        _err(str(exc))
        return 1
    print("\nBuild one with: legwork owner/repo")
    return 0


def cmd_hub(allow_read: list[str] | None, allow_net: bool, outputs_to: str | None = None) -> int:
    from legwork import hub, outputs

    try:
        limits = sandbox_runner.check_read_grants(allow_read or [])
        sandbox_runner._check_backend_available()
    except (sandbox_runner.SandboxGrantError, sandbox_runner.SandboxUnavailableError) as exc:
        _err(str(exc))
        return 1
    if outputs_to and outputs_to.strip().lower() == "off":
        outputs_dir = None
    else:
        outputs_dir = Path(outputs_to).expanduser().resolve() if outputs_to else outputs.DEFAULT_DIR
    return hub.run(limits, allow_net, outputs_dir)


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


def _installed_version() -> str:
    try:
        return importlib.metadata.version(DIST_NAME)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in ("build", "serve", "contribute", "clean", "find", "hub", "doctor", "-h", "--help", "-V", "--version"):
        # `legwork <repo>` and `legwork --no-cache <repo>` shorthands
        argv.insert(0, "build")

    parser = argparse.ArgumentParser(prog="legwork", description="Point it at a GitHub repo, get a working MCP tool back.")
    parser.add_argument("-V", "--version", action="version", version=f"legwork {_installed_version()}")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="build an MCP wrapper for a GitHub repo (also: legwork <repo>)")
    build.add_argument("repo", help="github.com URL or owner/repo")
    build.add_argument("--no-cache", action="store_true", help="always write a fresh wrapper, even if the repo is in the Legwork cache")
    serve = sub.add_parser("serve", help="run a built wrapper as an MCP server over stdio")
    serve.add_argument("repo", help="github.com URL or owner/repo")
    serve.add_argument(
        "--allow-read",
        action="append",
        metavar="PATH",
        help="let the tool read this file or folder (read-only; repeatable). By default it sees nothing in your home folder",
    )
    serve.add_argument("--allow-net", action="store_true", help="let the tool use the network (off by default)")
    contribute = sub.add_parser("contribute", help="write a built wrapper + manifest as a public-cache entry")
    contribute.add_argument("repo", help="github.com URL or owner/repo")
    contribute.add_argument(
        "--out", type=Path, default=cache_writer.DEFAULT_CACHE_DIR, help="cache folder to write into (default: ./cache)"
    )
    find = sub.add_parser("find", help="search GitHub for tools that do something")
    find.add_argument("query", nargs="+", help="a few keywords, e.g. extract tables pdf")
    hub_parser = sub.add_parser("hub", help="MCP server: your AI finds, installs and uses tools on demand")
    hub_parser.add_argument("--allow-read", action="append", metavar="PATH", help="the most any installed tool may read (repeatable)")
    hub_parser.add_argument("--allow-net", action="store_true", help="let installed tools use the network if they ask")
    hub_parser.add_argument(
        "--outputs", metavar="DIR",
        help="where files that tools make are copied (default: ~/Legwork/outputs; 'off' to keep them in the tool's folder)",
    )
    sub.add_parser("doctor", help="check this machine is ready (sandbox, key, cache, clients); paste it into bug reports")
    clean = sub.add_parser("clean", help="free disk space: remove old and failed builds")
    clean.add_argument("repo", nargs="?", help="only this repo (default: all)")
    clean.add_argument("--all", action="store_true", help="also remove current builds (rebuild before serving)")
    clean.add_argument("--dry-run", action="store_true", help="show what would be removed")
    args = parser.parse_args(argv)

    if args.command == "build":
        return cmd_build(args.repo, use_cache=not args.no_cache)
    if args.command == "doctor":
        from legwork import doctor

        return doctor.run()
    if args.command == "find":
        return cmd_find(" ".join(args.query))
    if args.command == "hub":
        return cmd_hub(args.allow_read, args.allow_net, args.outputs)
    if args.command == "clean":
        return cmd_clean(args.repo, args.all, args.dry_run)
    if args.command == "contribute":
        return cmd_contribute(args.repo, args.out)
    return cmd_serve(args.repo, args.allow_read, args.allow_net)


if __name__ == "__main__":
    sys.exit(main())
