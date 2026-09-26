from __future__ import annotations

from pathlib import Path

import pytest

from legwork.obfuscation_scanner import (
    ObfuscatedPayloadDetectedError,
    is_blocking,
    scan,
    scan_file,
    scan_repo,
)

FIXTURES = Path(__file__).parent / "fixtures"


# --- the real payload's code, defanged (primary fixture, per design doc T10) --


def test_real_payload_file_scans_without_importing_or_executing():
    """scan_file must never import or exec the fixture — only read + parse
    it as text/AST. If this test somehow executed the fixture, it would
    raise RuntimeError('win32 only') from inside the decrypted payload,
    which would fail this test loudly rather than silently."""
    findings = scan_file(FIXTURES / "ai_data_extractor_payload.py")
    assert findings, "expected the real malicious file to trip at least one heuristic"


def test_real_payload_trips_byte_array_and_readability_patterns():
    findings = scan_file(FIXTURES / "ai_data_extractor_payload.py")
    kinds = {f.pattern for f in findings}
    assert "byte-array-xor-deobfuscation" in kinds
    assert "low-identifier-readability" in kinds


def test_real_payload_does_not_trip_stacked_pattern_but_still_blocks():
    """Honest limitation, found by testing against the real file rather than
    a synthetic one: the payload wraps getattr in a lambda alias
    (`_yp7qrd = lambda m,s,k: getattr(m,...)`), so `getattr(m, ...)` and
    `__import__(...)` never appear as one syntactic expression —
    stacked-dynamic-resolution's lexical-adjacency check doesn't fire here.
    The file is still correctly blocked (see test_real_payload_is_blocking)
    via the weaker dynamic-import + dynamic-getattr signals co-occurring —
    the 2-distinct-weak-signals rule is what actually catches this specific
    file, not the strong pattern designed for the more direct form of the
    same technique. Full data-flow tracking (following a getattr call's
    first argument back through variable assignment to see if IT came from
    a dynamic __import__) would catch the direct case too, but that's a
    meaningfully bigger scope than v1's cheap AST pattern-matching — noted
    as a known gap, not silently papered over."""
    findings = scan_file(FIXTURES / "ai_data_extractor_payload.py")
    kinds = {f.pattern for f in findings}
    assert "stacked-dynamic-resolution" not in kinds
    assert "dynamic-import" in kinds
    assert "dynamic-getattr" in kinds
    assert is_blocking(findings)


def test_real_payload_is_blocking():
    findings = scan_file(FIXTURES / "ai_data_extractor_payload.py")
    assert is_blocking(findings)


def test_scan_raises_on_the_real_payload(tmp_path):
    """The T10 entrypoint used by the pipeline: scan(repo_root) raises on
    a hit, naming the specific file."""
    (tmp_path / "extractors").mkdir()
    target = tmp_path / "extractors" / "extract.py"
    target.write_text((FIXTURES / "ai_data_extractor_payload.py").read_text())

    with pytest.raises(ObfuscatedPayloadDetectedError, match="extract.py"):
        scan(tmp_path)


def test_narrow_exec_eval_only_heuristic_would_have_missed_this_file():
    """Documents the exact gap the eng review found: the real file has no
    literal exec(/eval( token, so a scanner scoped to only that pattern
    would find nothing here."""
    findings = scan_file(FIXTURES / "ai_data_extractor_payload.py")
    kinds = {f.pattern for f in findings}
    assert "literal-exec-eval" not in kinds


def test_direct_stacked_getattr_import_is_detected(tmp_path):
    """The stacked-pattern detector DOES catch the direct, lexically-adjacent
    form — getattr(__import__(x), y) written as one expression, no lambda
    indirection. Confirms the detector works for what it's designed for,
    even though the real payload's extra indirection (see above) evades it."""
    f = tmp_path / "direct_stack.py"
    f.write_text(
        "payload = getattr(__import__(decode(SECRET)), decode(OTHER_SECRET))(data, globals())\n"
    )
    findings = scan_file(f)
    assert any(x.pattern == "stacked-dynamic-resolution" for x in findings)
    assert is_blocking(findings)


def test_phone_harness_shaped_code_does_not_block(tmp_path):
    """Live-run false positive (2026-09-25): phone-harness was blocked for an
    ordinary proxy class plus exec() of the script it exists to run. Names
    passed through variables no longer count as dynamic."""
    f = tmp_path / "run.py"
    f.write_text(
        "class Tee:\n"
        "    def __getattr__(self, name):\n"
        "        return getattr(self._wrapped, name)\n"
        "\n"
        "def main(code, helpers):\n"
        "    g = {k: getattr(helpers, k) for k in dir(helpers)}\n"
        "    exec(code, g)\n"
    )
    findings = scan_file(f)
    assert not any(x.pattern in ("dynamic-getattr", "dynamic-import") for x in findings)
    assert not is_blocking(findings)


def test_known_tradeoff_decoded_name_stored_in_variable_not_flagged(tmp_path):
    """Documents the accepted evasion: decode into a variable first and the
    dynamic-name signals don't fire. Other signals (XOR/byte arrays,
    identifier readability) are what still cover this case."""
    f = tmp_path / "evasive.py"
    f.write_text("n = decode(SECRET)\nf = getattr(__import__(n), n)\n")
    findings = scan_file(f)
    assert not any(x.pattern in ("dynamic-getattr", "dynamic-import") for x in findings)


# --- literal exec()/eval() (the original, narrower signal) -----------------


def test_literal_exec_call_detected(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("exec(open('payload.txt').read())\n")
    findings = scan_file(f)
    assert any(x.pattern == "literal-exec-eval" for x in findings)


# --- false-positive resistance ----------------------------------------------


def test_clean_plugin_loader_does_not_block():
    """A single legitimate dynamic getattr/import call is common (plugin
    systems, config-driven dispatch) and must not alone block a repo."""
    findings = scan_file(FIXTURES / "clean_plugin_loader.py")
    assert not is_blocking(findings)


def test_small_int_list_is_not_flagged_as_byte_array(tmp_path):
    f = tmp_path / "constants.py"
    f.write_text("PORTS = [80, 443, 8080]\n")
    findings = scan_file(f)
    assert not any(x.pattern == "byte-array-xor-deobfuscation" for x in findings)


def test_large_int_list_without_xor_is_not_flagged(tmp_path):
    f = tmp_path / "lookup_table.py"
    f.write_text(f"CRC_TABLE = {list(range(64))}\n")
    findings = scan_file(f)
    assert not any(x.pattern == "byte-array-xor-deobfuscation" for x in findings)


def test_large_int_list_with_xor_is_flagged(tmp_path):
    f = tmp_path / "suspicious.py"
    f.write_text(
        f"KEY = {list(range(32))}\n"
        "def decode(data, key):\n"
        "    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))\n"
    )
    findings = scan_file(f)
    assert any(x.pattern == "byte-array-xor-deobfuscation" for x in findings)


def test_syntax_error_file_is_skipped_not_crashed(tmp_path):
    f = tmp_path / "not_python.py"
    f.write_text("this is not valid python syntax {{{ ][")
    findings = scan_file(f)  # should not raise
    assert findings == []


# --- scan_repo walks the tree, skips vendored dirs -------------------------


def test_scan_repo_finds_findings_in_nested_files(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("exec('x')\n")
    findings = scan_repo(tmp_path)
    assert any(f.pattern == "literal-exec-eval" for f in findings)


def test_scan_repo_skips_vendored_and_venv_dirs(tmp_path):
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "evil.py").write_text("exec('x')\n")
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "evil.py").write_text("exec('x')\n")
    findings = scan_repo(tmp_path)
    assert findings == []


def test_scan_passes_clean_repo(tmp_path):
    (tmp_path / "main.py").write_text("print('hello world')\n")
    scan(tmp_path)  # must not raise
