"""A scan receipt: what the pre-install scan found in one commit of a repo
(`legwork scan`), as data and as text.

A receipt says what was checked and what was found. It never says a repo is
safe: a clean one says no covered patterns were found in the files that were
analysed, and lists the files that weren't. "Blocked" uses the rule an
install uses (obfuscation_scanner.is_blocking, file by file), so a receipt
and an install never disagree.

Everything that comes from the scanned repo (file names, and details that
quote its identifiers) is a stranger's text: each renderer escapes it for
where it is shown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from legwork import obfuscation_scanner

SCHEMA = 1

BLOCKED, NOTED, CLEAN, NOTHING_COVERED, INCOMPLETE = "blocked", "noted", "clean", "nothing-covered", "incomplete"

NOT_COVERED = (
    "This scan reads the Python and JavaScript/TypeScript source in the repo and runs none of it. "
    "It does not see what an installer downloads later, and a determined author can get past it. "
    "A result with no findings is not a statement that the repo is safe."
)

_KIND_NAMES = {"python": "Python", "javascript": "JavaScript/TypeScript"}


@dataclass(frozen=True)
class Receipt:
    repo: str
    commit: str | None
    legwork: str
    scanned_at: str
    verdict: str
    reason: str | None = None  # why an incomplete scan stopped
    analysed: dict[str, int] = field(default_factory=dict)
    skipped: tuple[tuple[str, str], ...] = ()  # (file, why it wasn't analysed)
    findings: tuple[dict, ...] = ()  # file, line (None for a whole-file finding), pattern, detail, blocks

    @property
    def summary(self) -> str:
        if self.verdict == BLOCKED:
            return "blocked: Legwork would refuse to install this commit"
        if self.verdict == NOTED:
            return f"not blocked: {_count(len(self.findings), 'weaker pattern')} noted"
        if self.verdict == CLEAN:
            return f"no covered patterns found in {_count(sum(self.analysed.values()), 'analysed file')}"
        if self.verdict == NOTHING_COVERED:
            return "nothing this scan covers"
        return f"scan could not complete: {self.reason}"

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA,
            "repo": self.repo,
            "commit": self.commit,
            "legwork": self.legwork,
            "scanned_at": self.scanned_at,
            "verdict": self.verdict,
            "summary": self.summary,
            "analysed": dict(self.analysed),
            "skipped": [{"file": file, "reason": reason} for file, reason in self.skipped],
            "findings": [dict(finding) for finding in self.findings],
            "not_covered": NOT_COVERED,
        }


def build(root: Path, repo: str, commit: str, legwork: str, scanned_at: str) -> Receipt:
    """Scan the clone at `root` and write up what was found."""
    results = obfuscation_scanner.scan_files(root)
    analysed = {kind: sum(1 for r in results if r.kind == kind and r.skipped is None) for kind in _KIND_NAMES}
    skipped = tuple((_relative(r.file, root), r.skipped) for r in results if r.skipped is not None)
    findings = tuple(
        {"file": _relative(r.file, root), "line": f.line or None, "pattern": f.pattern, "detail": f.detail, "blocks": r.blocks}
        for r in sorted(results, key=lambda r: _relative(r.file, root))
        for f in sorted(r.findings, key=lambda f: (f.line, f.pattern))
    )
    reason = None
    if any(r.blocks for r in results):
        verdict = BLOCKED
    elif findings:
        verdict = NOTED
    elif sum(analysed.values()):
        verdict = CLEAN
    elif skipped:
        verdict, reason = INCOMPLETE, f"none of the {_count(len(skipped), 'source file')} could be analysed"
    else:
        verdict = NOTHING_COVERED
    return Receipt(repo, commit, legwork, scanned_at, verdict, reason, analysed, skipped, findings)


def incomplete(repo: str, commit: str | None, legwork: str, scanned_at: str, reason: str) -> Receipt:
    """The receipt for a scan that never got to read the repo (a failed clone)."""
    return Receipt(repo, commit, legwork, scanned_at, INCOMPLETE, reason)


def exit_code(receipt: Receipt) -> int:
    """0 unless blocked (3) or incomplete (1). Never 2: argparse and an
    unknown command already exit 2, and a typo must not read as "blocked"."""
    return {BLOCKED: 3, INCOMPLETE: 1}.get(receipt.verdict, 0)


def render_text(receipt: Receipt) -> str:
    """For a terminal: control characters in a stranger's file name could
    move the cursor or recolour the screen, so they are replaced."""
    plain = _printable
    lines = [
        "Legwork scan receipt",
        f"  repo     {plain(receipt.repo)}",
        f"  commit   {plain(receipt.commit or 'not read')}",
        f"  scanned  {plain(receipt.scanned_at)} with legwork {plain(receipt.legwork)}",
        "",
        f"  {plain(receipt.summary)}",
    ]
    if receipt.verdict != INCOMPLETE or receipt.analysed:
        lines += ["", f"  {_analysed_line(receipt)}"]
    if receipt.skipped:
        lines.append(f"  Not analysed, so not counted above ({len(receipt.skipped)}):")
        lines += [f"    {plain(file)}: {plain(reason)}" for file, reason in receipt.skipped]
    if receipt.findings:
        lines.append("  Found:")
        for finding in receipt.findings:
            lines.append(f"    {plain(_where(finding))}: {plain(finding['pattern'])}{' (blocks)' if finding['blocks'] else ''}")
            lines.append(f"        {plain(finding['detail'])}")
    lines += ["", f"  {NOT_COVERED}"]
    return "\n".join(lines)


def render_markdown(receipt: Receipt) -> str:
    """For a GitHub issue comment: every string from the repo goes in a code
    span, where mentions, links and HTML are not acted on."""
    lines = [
        f"**Legwork scan receipt** for {_code(receipt.repo)} at {_code(receipt.commit or 'not read')}",
        "",
        # An incomplete scan's reason can quote git's own output: a code span too.
        f"**scan could not complete:** {_code(receipt.reason)}" if receipt.verdict == INCOMPLETE else f"**{receipt.summary}**",
        "",
        f"Scanned {_code(receipt.scanned_at)} with legwork {_code(receipt.legwork)}.",
    ]
    if receipt.verdict != INCOMPLETE or receipt.analysed:
        lines.append(_analysed_line(receipt))
    if receipt.skipped:
        lines += ["", f"Not analysed, so not counted above ({len(receipt.skipped)}):"]
        lines += [f"- {_code(file)}: {reason}" for file, reason in receipt.skipped]
    if receipt.findings:
        lines += ["", "Found:"]
        for finding in receipt.findings:
            blocks = " (blocks)" if finding["blocks"] else ""
            lines.append(f"- {_code(_where(finding))}: {_code(finding['pattern'])}{blocks}, {_code(finding['detail'])}")
    lines += ["", NOT_COVERED]
    return "\n".join(lines)


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _relative(path: Path, root: Path) -> str:
    return (path.relative_to(root) if path.is_relative_to(root) else path).as_posix()


def _where(finding: dict) -> str:
    return f"{finding['file']}:{finding['line']}" if finding["line"] else finding["file"]


def _analysed_line(receipt: Receipt) -> str:
    parts = [_count(n, f"{_KIND_NAMES[kind]} file") for kind, n in receipt.analysed.items() if n]
    return f"Analysed {' and '.join(parts) if parts else 'no files'}."


_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f  ‪-‮⁦-⁩]")


def _printable(text: str) -> str:
    """No control characters, and none that reorder the text around them
    (a right-to-left override can make `a.py` read as something else)."""
    return _CONTROL_RE.sub("?", str(text))


def _code(text: str) -> str:
    """`text` as a Markdown code span, whatever backticks it holds: the
    fence is one backtick longer than the longest run inside."""
    text = _printable(text)
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") or not text.strip() else ""
    return f"{fence}{pad}{text}{pad}{fence}"
