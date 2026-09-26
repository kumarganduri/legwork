"""Static pre-execution scan for obfuscated/malicious code patterns.

Design doc: docs/designs/legwork-jit-ai-runtime.md, T10. Runs against the
full cloned repo tree (see repo_fetcher.py) before README parsing, codegen,
install, or invoke ever touch it.

Written specifically against a real malicious file found in the validation
spike (kruzovic7/ai-data-extractor, 2026-09-22): XOR-decoded string
literals resolved into an exec() call via
`getattr(__import__(xor_decoded), xor_decoded)`, with zero literal
`exec(` or `eval(` token anywhere in the source — built specifically to
evade a naive grep for those two names. See
tests/fixtures/ai_data_extractor_payload.py for the real file's code
(captured from the public repo, payload bytes randomized so it's inert;
used purely as a static-analysis test corpus).

Scope, stated plainly: this is a Python-AST scanner. It covers `.py` files
in the clone; it does not (in v1) analyze other languages (compiled
binaries, JS/TS, Go, etc.) a target repo might contain. Not foolproof — a
sufficiently determined attacker can still evade static heuristics — but
this scope is what actually would have caught the real case above, not
just a narrower exec()/eval() grep.

Severity tiering (why a single dynamic __import__ doesn't auto-block):
legitimate code does use dynamic imports and getattr for real reasons
(plugin loading, lazy imports). A lone weak signal is too noisy to hard-fail
on. The real payload trips several signals AT ONCE in the same file — that
co-occurrence is the actual tell, not any one pattern alone.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", "build", "dist"}

# Patterns strong enough to block on their own — each is rare in ordinary
# code and was actually present in the real malicious file.
_STRONG_PATTERNS = {"byte-array-xor-deobfuscation", "stacked-dynamic-resolution"}

# Below this many distinct constant ints, a list literal is probably just
# data (a lookup table, test fixture, etc.), not a reconstructed key/payload.
_BYTE_ARRAY_MIN_LEN = 16

# Identifiers matching this shape (leading underscore, 5+ random-looking
# lowercase-alnum chars, no vowel-heavy or word-like structure) are the
# generated-junk-name style the real payload used throughout
# (`_a4jgwq6195`, `_phd7ypcw4d`, `_pha24b`, ...).
_GIBBERISH_NAME_RE = re.compile(r"^_[a-z0-9]{5,}$")
_MIN_GIBBERISH_NAMES = 5
_MIN_GIBBERISH_FRACTION = 0.3


class ObfuscatedPayloadDetectedError(Exception):
    """Static scan found obfuscation patterns strongly associated with a
    hidden, dynamically-executed payload. Repo is refused before install
    or invoke ever run."""


@dataclass(frozen=True)
class Finding:
    file: Path
    line: int
    pattern: str
    detail: str


def _is_literal_str(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _is_computed_name(node: ast.expr) -> bool:
    """True when a module/attribute name is built by an inline expression
    (e.g. `decode(blob).decode()`), not a string literal and not a name
    simply passed in through a variable or attribute.

    Why variables don't count (live-run finding, 2026-09-25): the scanner
    blocked phone-harness on an ordinary proxy class
    (`def __getattr__(self, name): return getattr(self._wrapped, name)`)
    plus the exec() of the script the tool exists to run — two legitimate
    patterns. The real malicious fixture builds its names inline, so it
    still trips this (and its XOR/byte-array signal blocks it on its own).
    Known trade-off: an attacker can store the decoded name in a variable
    first to dodge this particular signal."""
    return not _is_literal_str(node) and not isinstance(node, (ast.Name, ast.Attribute))


def _is_int_list_literal(node: ast.expr, min_len: int = _BYTE_ARRAY_MIN_LEN) -> bool:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return False
    if len(node.elts) < min_len:
        return False
    return all(isinstance(e, ast.Constant) and isinstance(e.value, int) for e in node.elts)


class _Visitor(ast.NodeVisitor):
    def __init__(self, file: Path):
        self.file = file
        self.findings: list[Finding] = []
        self._has_xor_op = False
        self._has_byte_array_literal = False
        self._all_names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        self._all_names.add(node.id)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._all_names.add(node.name)
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, ast.BitXor):
            self._has_xor_op = True
        self.generic_visit(node)

    def visit_List(self, node: ast.List) -> None:
        if _is_int_list_literal(node):
            self._has_byte_array_literal = True
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        is_dynamic_import = (
            isinstance(func, ast.Name)
            and func.id == "__import__"
            and node.args
            and _is_computed_name(node.args[0])
        )
        is_dynamic_getattr = (
            isinstance(func, ast.Name)
            and func.id == "getattr"
            and len(node.args) >= 2
            and _is_computed_name(node.args[1])
        )

        if isinstance(func, ast.Name) and func.id in ("exec", "eval"):
            self.findings.append(
                Finding(self.file, node.lineno, "literal-exec-eval", f"direct call to {func.id}()")
            )

        if is_dynamic_getattr and isinstance(node.args[0], ast.Call):
            inner = node.args[0]
            inner_is_dynamic_import = (
                isinstance(inner.func, ast.Name)
                and inner.func.id == "__import__"
                and inner.args
                and _is_computed_name(inner.args[0])
            )
            if inner_is_dynamic_import:
                self.findings.append(
                    Finding(
                        self.file,
                        node.lineno,
                        "stacked-dynamic-resolution",
                        "getattr() with a computed attribute name, called on the result of "
                        "__import__() with a computed module name — resolves an arbitrary "
                        "function from an arbitrary module without either name appearing "
                        "as a literal anywhere in the source",
                    )
                )
        elif is_dynamic_import:
            self.findings.append(
                Finding(self.file, node.lineno, "dynamic-import", "__import__() called with a computed module name")
            )
        elif is_dynamic_getattr:
            self.findings.append(
                Finding(self.file, node.lineno, "dynamic-getattr", "getattr() called with a computed attribute name")
            )

        self.generic_visit(node)

    def finalize(self) -> list[Finding]:
        if self._has_xor_op and self._has_byte_array_literal:
            self.findings.append(
                Finding(
                    self.file,
                    0,
                    "byte-array-xor-deobfuscation",
                    "a large integer/byte-array literal combined with XOR (^) — the "
                    "shape of a string/bytes deobfuscation routine",
                )
            )

        gibberish = [n for n in self._all_names if _GIBBERISH_NAME_RE.match(n)]
        if len(gibberish) >= _MIN_GIBBERISH_NAMES and self._all_names:
            fraction = len(gibberish) / len(self._all_names)
            if fraction >= _MIN_GIBBERISH_FRACTION:
                self.findings.append(
                    Finding(
                        self.file,
                        0,
                        "low-identifier-readability",
                        f"{len(gibberish)}/{len(self._all_names)} names "
                        f"({fraction:.0%}) look machine-generated, not authored",
                    )
                )
        return self.findings


def scan_file(path: Path) -> list[Finding]:
    """Parse and scan one Python file. Returns an empty list for files that
    fail to parse (a file that isn't valid Python isn't in scope for a
    Python-AST scanner — noted as a scope limit, not silently ignored)."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, ValueError, UnicodeDecodeError):
        return []
    visitor = _Visitor(path)
    visitor.visit(tree)
    return visitor.finalize()


def _iter_python_files(root: Path):
    for path in root.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        yield path


def is_blocking(findings: list[Finding]) -> bool:
    kinds = {f.pattern for f in findings}
    if kinds & _STRONG_PATTERNS:
        return True
    # No single strong pattern, but 2+ distinct weak signals in the same
    # file is also a block — the co-occurrence is the actual tell.
    return len(kinds) >= 2


def scan_repo(root: Path) -> list[Finding]:
    """Scan every .py file under `root`. Returns all findings across all
    files (not just blocking ones) — callers decide what to do with them."""
    all_findings: list[Finding] = []
    for path in _iter_python_files(root):
        all_findings.extend(scan_file(path))
    return all_findings


def scan(root: Path) -> None:
    """The T10 entrypoint: scan `root`, raise ObfuscatedPayloadDetectedError
    on the first file whose findings meet the blocking bar. Scans file by
    file so the error names the specific offending file, not just "somewhere
    in this repo"."""
    for path in _iter_python_files(root):
        findings = scan_file(path)
        if is_blocking(findings):
            patterns = ", ".join(sorted({f.pattern for f in findings}))
            raise ObfuscatedPayloadDetectedError(
                f"{path}: obfuscation patterns detected ({patterns}) — refusing "
                "to install or invoke this repo"
            )
