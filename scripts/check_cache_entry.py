"""Check one proposed cache/ entry the way a user's build will use it.

Run by .github/workflows/cache-check.yml on every PR that touches cache/,
with this script taken from main (never from the PR). Every user who
builds that repo runs the entry's install command with network on and its
wrapper on their machine, so an entry has to pass everything here, and a
human still reviews the install command it prints.

    uv run python scripts/check_cache_entry.py cache/<owner>__<repo> [--network]
"""

from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
from pathlib import Path

from legwork import (
    cache_reader,
    cache_writer,
    local_store,
    obfuscation_scanner,
    repo_fetcher,
    retry_loop,
)
from legwork.mcp_stdio import MCPClientError, StdioMCPClient

# A known answer is a fixed input and the output a person expects from it,
# so a wrapper that misread its README can't pass by agreeing with its own
# self-test (issue #3). Inputs live in the entry's fixtures/ folder, which
# is all the tool may read.
FIXTURES_DIR = "fixtures"
MAX_FIXTURE_BYTES = 2 * 1024 * 1024
KNOWN_ANSWER_TIMEOUT = 300


def check(entry: Path, network: bool = False) -> tuple[list[str], list[str]]:
    """(problems, summary lines). No problems means the entry passes.
    `network`: also run known answers that need the internet (YouTube
    blocks cloud runners, so CI leaves those to a person)."""
    problems: list[str] = []
    try:
        manifest = json.loads((entry / "manifest.json").read_text())
        wrapper = (entry / "wrapper.py").read_text()
    except (OSError, ValueError) as exc:
        return [f"can't read the entry: {exc}"], []
    extra = sorted(p.name for p in entry.iterdir() if p.name not in ("manifest.json", "wrapper.py", FIXTURES_DIR))
    if extra:
        problems.append(f"unexpected files: {', '.join(extra)} (an entry is wrapper.py, manifest.json and fixtures/)")
    problems += _known_answer_problems(manifest, entry)

    missing = [f for f in cache_reader._REQUIRED_FIELDS if not manifest.get(f)]
    if missing:
        return problems + [f"manifest is missing {', '.join(missing)}"], []
    try:
        ref = repo_fetcher.parse_repo_url(manifest["source_repo_url"])
    except repo_fetcher.InvalidRepoURLError as exc:
        return problems + [f"source_repo_url: {exc}"], []
    if entry.name != cache_reader.entry_name(ref):
        problems.append(f"folder should be named {cache_reader.entry_name(ref)} for {ref.slug}")

    for name, text in (("wrapper.py", wrapper), ("manifest.json", json.dumps(manifest))):
        hits = cache_writer.find_secrets(text, [])
        if hits:
            problems.append(f"{name} contains a {' and a '.join(hits)}")
    try:
        obfuscation_scanner.scan(entry)
    except obfuscation_scanner.ObfuscatedPayloadDetectedError as exc:
        problems.append(f"wrapper looks obfuscated: {exc}")
    if problems:
        return problems, []

    summary = [
        f"**{ref.slug}** @ `{manifest['commit_sha'][:12]}`",
        f"- Install command (runs with network on for every user; **review it**): `{manifest['install_command']}`",
        f"- What it wraps: {manifest['entrypoint']}",
        f"- Written by: {manifest['llm_model']}",
        f"- Source license: {manifest.get('source_license', '?')}"
        + (f" ⚠️ {manifest['license_flag']}" if manifest.get("license_flag") else ""),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        try:
            result = retry_loop.run_cached(ref.slug, workdir, manifest["install_command"], wrapper)
        except (repo_fetcher.RepoNotFoundError, repo_fetcher.RepoAccessError, obfuscation_scanner.ObfuscatedPayloadDetectedError) as exc:
            return [f"{type(exc).__name__}: {exc}"], summary
        if not result.success:
            return [f"the wrapper failed its sandboxed install/self-test: {result.final_error}"], summary
        try:
            cache_writer.check_verbatim_copy(wrapper, workdir / "repo")
        except cache_writer.VerbatimCopyDetectedError as exc:
            return [str(exc)], summary
        summary.append("- Scan, sandboxed install and self-test: passed")
        answer = manifest["known_answer"]
        if _needs_network(manifest) and not network:
            summary.append("- Known answer: skipped, it needs the internet (run with --network)")
        else:
            wrong = run_known_answer(ref, manifest, entry, result.attempt_dir)
            if wrong:
                return wrong, summary
            summary.append(f"- Known answer: passed ({answer['tool']} on {answer.get('derived_from', 'its fixture')})")
    try:
        current = repo_fetcher.remote_head_sha(ref.clone_url)
        if current != manifest["commit_sha"]:
            summary.append(f"- Note: the repo has newer commits (now `{current[:12]}`); fine if the self-test passed")
    except repo_fetcher.RepoAccessError:
        pass
    return [], summary


def _needs_network(manifest: dict) -> bool:
    return bool(manifest["known_answer"].get("needs_network", manifest.get("needs_network")))


def _known_answer_problems(manifest: dict, entry: Path) -> list[str]:
    """What's wrong with the entry's known_answer field and fixtures, without running anything."""
    answer = manifest.get("known_answer")
    if not isinstance(answer, dict):
        return ["manifest has no known_answer: a fixed input and the output a person expects (see CONTRIBUTING.md)"]
    problems = []
    if not isinstance(answer.get("tool"), str) or not answer["tool"]:
        problems.append("known_answer.tool must name one of the wrapper's tools")
    if not isinstance(answer.get("arguments", {}), dict):
        problems.append("known_answer.arguments must be an object")
    expects = answer.get("expect_contains", [])
    if not isinstance(expects, list) or not all(isinstance(s, str) and s for s in expects):
        problems.append("known_answer.expect_contains must be a list of non-empty strings")
    image = answer.get("expect_image")
    if image is not None and not (image is True or (isinstance(image, dict) and set(image) <= {"width", "height"}
                                                     and all(isinstance(v, int) for v in image.values()))):
        problems.append("known_answer.expect_image must be true or {\"width\": int, \"height\": int}")
    if not expects and not image:
        problems.append("known_answer needs expect_contains or expect_image: something to compare")
    if not isinstance(answer.get("derived_from"), str) or not answer["derived_from"].strip():
        problems.append("known_answer.derived_from must say how the expected answer follows from the fixture")
    fixtures = entry / FIXTURES_DIR
    if fixtures.exists():
        if fixtures.is_symlink() or not fixtures.is_dir():
            problems.append("fixtures/ must be a plain folder")
        else:
            files = [p for p in fixtures.rglob("*")]
            if any(p.is_symlink() for p in files):
                problems.append("fixtures/ may not contain links")
            size = sum(p.stat().st_size for p in files if p.is_file() and not p.is_symlink())
            if size > MAX_FIXTURE_BYTES:
                problems.append(f"fixtures/ is {size // 1024} KB; keep it under {MAX_FIXTURE_BYTES // 1024 // 1024} MB")
    if "{fixtures}" in json.dumps(answer.get("arguments", {})) and not fixtures.is_dir():
        problems.append("known_answer.arguments refer to {fixtures} but there's no fixtures/ folder")
    return problems


def _with_fixtures(value, fixtures: Path):
    if isinstance(value, str):
        return value.replace("{fixtures}", str(fixtures))
    if isinstance(value, list):
        return [_with_fixtures(v, fixtures) for v in value]
    if isinstance(value, dict):
        return {k: _with_fixtures(v, fixtures) for k, v in value.items()}
    return value


def run_known_answer(ref, manifest: dict, entry: Path, attempt_dir: Path) -> list[str]:
    """Serve the installed wrapper the way a user's client does (`legwork
    serve`, sandboxed, reading only fixtures/), call the tool and compare."""
    answer = manifest["known_answer"]
    fixtures = (entry / FIXTURES_DIR).resolve()
    with tempfile.TemporaryDirectory() as home:
        env = dict(os.environ, LEGWORK_HOME=home)
        saved = os.environ.get(local_store.LEGWORK_HOME_ENV)
        os.environ[local_store.LEGWORK_HOME_ENV] = home
        try:
            (Path(home) / "wrappers" / f"{ref.owner}__{ref.repo}").mkdir(parents=True)
            local_store.save_current(ref, attempt_dir, manifest["install_command"], manifest["entrypoint"], manifest["llm_model"])
        finally:
            if saved is None:
                os.environ.pop(local_store.LEGWORK_HOME_ENV, None)
            else:
                os.environ[local_store.LEGWORK_HOME_ENV] = saved
        command = [sys.executable, "-m", "legwork.cli", "serve", ref.slug]
        if fixtures.is_dir():
            command += ["--allow-read", str(fixtures)]
        if _needs_network(manifest):
            command.append("--allow-net")
        client = StdioMCPClient(command, env)
        try:
            client.initialize(client_name="legwork-cache-check", timeout=120)
            result = client.call_tool(answer["tool"], _with_fixtures(answer.get("arguments", {}), fixtures),
                                      timeout=KNOWN_ANSWER_TIMEOUT)
        except MCPClientError as exc:
            return [f"known answer: calling {answer['tool']} failed: {exc}"]
        finally:
            client.close()
    return compare_known_answer(answer, result)


def compare_known_answer(answer: dict, result: dict) -> list[str]:
    content = result.get("content") or []
    text = "\n".join(c.get("text", "") for c in content if c.get("type") == "text")
    if result.get("isError"):
        return [f"known answer: {answer['tool']} returned an error: {text[:500]}"]
    problems = [f"known answer: expected {s!r} in the reply" for s in answer.get("expect_contains", []) if s not in text]
    image = answer.get("expect_image")
    if image:
        images = [c for c in content if c.get("type") == "image"]
        if not images:
            problems.append("known answer: expected an image in the reply")
        elif isinstance(image, dict):
            size = _png_size(images[0].get("data", ""))
            want = (image.get("width"), image.get("height"))
            if size is None or any(w is not None and w != got for w, got in zip(want, size)):
                problems.append(f"known answer: expected a {want[0]}x{want[1]} PNG, got {size or 'something else'}")
    if problems:
        problems.append(f"the reply began: {text[:300]!r}")
    return problems


def _png_size(data: str) -> tuple[int, int] | None:
    import base64
    import binascii

    try:
        head = base64.b64decode(data[:64] + "=" * (-len(data[:64]) % 4))
    except (binascii.Error, ValueError):
        return None
    if head[:8] != b"\x89PNG\r\n\x1a\n" or len(head) < 24:
        return None
    return struct.unpack(">II", head[16:24])


def main(argv: list[str]) -> int:
    network = "--network" in argv
    argv = [a for a in argv if a != "--network"]
    failed = False
    report = []
    for arg in argv:
        entry = Path(arg)
        problems, summary = check(entry, network=network)
        report += summary
        if problems:
            failed = True
            report += [f"❌ `{entry}`:"] + [f"  - {p}" for p in problems]
        else:
            report.append(f"✅ `{entry}` passed")
        report.append("")
    text = "\n".join(report)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write("## Cache entry check\n\n" + text + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
