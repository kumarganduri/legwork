"""`legwork scan`'s receipt: what the pre-install scan found in one commit,
without overstating it, and safe to show wherever it goes."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from legwork import obfuscation_scanner, receipts

FIXTURES = Path(__file__).parent / "fixtures"
NOW = "2026-11-15T12:00:00Z"
SHA = "a" * 40


def _receipt(root: Path) -> receipts.Receipt:
    return receipts.build(root, "owner/repo", SHA, "0.0.0", NOW)


def _payload(dest: Path) -> Path:
    """The defanged real payload, copied as bytes: never imported or run."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURES / "ai_data_extractor_payload.py", dest)
    return dest


def test_a_repo_an_install_would_refuse_is_blocked(tmp_path):
    _payload(tmp_path / "src" / "extract.py")
    (tmp_path / "ok.py").write_text("print('hello')\n")
    receipt = _receipt(tmp_path)
    assert receipt.verdict == receipts.BLOCKED and receipts.exit_code(receipt) == 3
    assert receipt.summary == "blocked: Legwork would refuse to install this commit"
    assert receipt.analysed == {"python": 2, "javascript": 0}
    blocking = [f for f in receipt.findings if f["blocks"]]
    assert {f["file"] for f in blocking} == {"src/extract.py"}
    assert "byte-array-xor-deobfuscation" in {f["pattern"] for f in blocking}
    # A whole-file finding has no line to point at.
    assert next(f for f in blocking if f["pattern"] == "byte-array-xor-deobfuscation")["line"] is None
    # The same rule as an install: scan() refuses this folder too.
    with pytest.raises(obfuscation_scanner.ObfuscatedPayloadDetectedError):
        obfuscation_scanner.scan(tmp_path)


def test_one_weaker_pattern_is_noted_not_blocked(tmp_path):
    (tmp_path / "setup.py").write_text("exec(open('version.py').read())\n")
    receipt = _receipt(tmp_path)
    assert receipt.verdict == receipts.NOTED and receipts.exit_code(receipt) == 0
    assert receipt.summary == "not blocked: 1 weaker pattern noted"
    assert receipt.findings == ({"file": "setup.py", "line": 1, "pattern": "literal-exec-eval",
                                 "detail": receipt.findings[0]["detail"], "blocks": False},)
    obfuscation_scanner.scan(tmp_path)  # an install goes ahead as well


def test_weaker_patterns_in_different_files_do_not_add_up(tmp_path):
    """The rule is per file: two weak signals block only when they share one."""
    (tmp_path / "a.py").write_text("exec(open('version.py').read())\n")
    (tmp_path / "b.js").write_text("eval(code)\n")
    receipt = _receipt(tmp_path)
    assert receipt.verdict == receipts.NOTED and len(receipt.findings) == 2
    assert receipt.summary == "not blocked: 2 weaker patterns noted"


def test_clean_says_how_many_files_were_analysed(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.ts").write_text("export const x = 1\n")
    receipt = _receipt(tmp_path)
    assert receipt.verdict == receipts.CLEAN
    assert receipt.summary == "no covered patterns found in 2 analysed files"
    assert "not a statement that the repo is safe" in receipts.render_text(receipt)


def test_a_file_the_scan_could_not_read_is_listed_and_not_counted_as_clean(tmp_path, monkeypatch):
    (tmp_path / "good.py").write_text("x = 1\n")
    (tmp_path / "broken.py").write_text("def (:\n")
    monkeypatch.setattr(obfuscation_scanner, "_JS_MAX_BYTES", 10)
    (tmp_path / "huge.js").write_text("const x = 'more than ten bytes'\n")
    receipt = _receipt(tmp_path)
    assert receipt.analysed == {"python": 1, "javascript": 0}
    assert dict(receipt.skipped) == {"broken.py": "this Python can't parse it", "huge.js": "larger than 0 MB"}
    assert receipt.summary == "no covered patterns found in 1 analysed file"
    text = receipts.render_text(receipt)
    assert "Not analysed, so not counted above (2):" in text and "broken.py: this Python can't parse it" in text


def test_nothing_analysed_is_not_a_clean_result(tmp_path):
    (tmp_path / "broken.py").write_text("def (:\n")
    receipt = _receipt(tmp_path)
    assert receipt.verdict == receipts.INCOMPLETE and receipts.exit_code(receipt) == 1
    assert receipt.summary == "scan could not complete: none of the 1 source file could be analysed"


def test_a_repo_with_no_source_the_scan_reads(tmp_path):
    (tmp_path / "README.md").write_text("# hi\n")
    (tmp_path / "main.go").write_text("package main\n")
    receipt = _receipt(tmp_path)
    assert receipt.verdict == receipts.NOTHING_COVERED and receipts.exit_code(receipt) == 0
    assert receipt.summary == "nothing this scan covers"


def test_a_failed_clone_is_incomplete_and_never_exit_2():
    receipt = receipts.incomplete("owner/repo", None, "0.0.0", NOW, "Repo not found or private: owner/repo")
    assert receipts.exit_code(receipt) == 1
    assert receipt.to_dict()["commit"] is None and "not read" in receipts.render_text(receipt)
    # 2 is what argparse and an unknown command return: never a verdict.
    assert 2 not in {receipts.exit_code(receipts.Receipt("o/r", SHA, "0", NOW, v, "why"))
                     for v in (receipts.BLOCKED, receipts.NOTED, receipts.CLEAN, receipts.NOTHING_COVERED, receipts.INCOMPLETE)}


# No "/": a file name can hold anything else.
HOSTILE = "x\x1b[31m‮`@everyone` <img src=x onerror=alert(1)> [a](javascript:alert(1))\nNEXT.py"


def test_a_hostile_file_name_is_made_safe_wherever_the_receipt_is_shown(tmp_path):
    """A repo names its own files: escape codes, text-direction overrides,
    Markdown, mentions and HTML must not act on a terminal or an issue."""
    _payload(tmp_path / HOSTILE)
    receipt = _receipt(tmp_path)
    assert receipt.verdict == receipts.BLOCKED
    # As data it is exact: JSON escapes it and gives it back unchanged.
    data = json.loads(json.dumps(receipt.to_dict()))
    assert data["findings"][0]["file"] == HOSTILE and "\x1b" not in json.dumps(receipt.to_dict())

    text = receipts.render_text(receipt)
    assert "\x1b" not in text and "‮" not in text
    assert "NEXT.py" in text and "\nNEXT.py" not in text  # the newline did not start a line of its own

    markdown = receipts.render_markdown(receipt)
    assert "\x1b" not in markdown and "‮" not in markdown
    for line in markdown.splitlines():
        if "@everyone" in line or "<img" in line or "javascript:" in line:
            # Inside a code span fenced by more backticks than the name holds.
            assert line.startswith("- ``") and line.count("``") >= 2, line


def test_markdown_code_spans_survive_backticks():
    assert receipts._code("plain") == "`plain`"
    assert receipts._code("a`b") == "``a`b``"
    assert receipts._code("`edge`") == "`` `edge` ``"
    assert receipts._code("a```b") == "````a```b````"


def test_the_receipt_as_data_has_everything_the_text_has(tmp_path):
    (tmp_path / "setup.py").write_text("exec(open('version.py').read())\n")
    data = _receipt(tmp_path).to_dict()
    assert data["schema"] == 1 and data["repo"] == "owner/repo" and data["commit"] == SHA
    assert data["verdict"] == "noted" and data["summary"] == "not blocked: 1 weaker pattern noted"
    assert data["analysed"] == {"python": 1, "javascript": 0} and data["skipped"] == []
    assert data["scanned_at"] == NOW and data["legwork"] == "0.0.0" and data["not_covered"] == receipts.NOT_COVERED
