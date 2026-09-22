from __future__ import annotations

import pytest

from legwork.readme_parser import (
    README_MAX_BYTES,
    InsufficientReadmeError,
    _is_priority,
    _Section,
    find_readme,
    parse_readme,
)

# --- find_readme -------------------------------------------------------


@pytest.mark.parametrize("filename", ["README.md", "readme.md", "README.rst", "README.txt", "README"])
def test_find_readme_matches_common_filenames(tmp_path, filename):
    (tmp_path / filename).write_text("hello")
    assert find_readme(tmp_path) == tmp_path / filename


def test_find_readme_returns_none_when_missing(tmp_path):
    (tmp_path / "main.py").write_text("print(1)")
    assert find_readme(tmp_path) is None


def test_find_readme_ignores_directories_named_readme(tmp_path):
    (tmp_path / "README.md").mkdir()  # a directory, not a file — edge case
    assert find_readme(tmp_path) is None


def test_find_readme_does_not_search_subdirectories(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "README.md").write_text("nested, not root")
    assert find_readme(tmp_path) is None


# --- parse_readme: missing / under cap ----------------------------------


def test_parse_readme_raises_when_missing(tmp_path):
    with pytest.raises(InsufficientReadmeError):
        parse_readme(tmp_path)


def test_parse_readme_returns_full_content_when_under_cap(tmp_path):
    (tmp_path / "README.md").write_text("# Hello\n\nShort README.\n")
    result = parse_readme(tmp_path)
    assert result.truncated is False
    assert result.content == "# Hello\n\nShort README.\n"
    assert result.original_bytes == len(b"# Hello\n\nShort README.\n")


# --- truncation: the actual bug this spike found ------------------------


def test_oversized_readme_is_truncated_and_flagged(tmp_path):
    body = "x" * (README_MAX_BYTES * 2)
    (tmp_path / "README.md").write_text(f"# Big\n\n{body}\n")
    result = parse_readme(tmp_path)
    assert result.truncated is True
    assert len(result.content.encode("utf-8")) <= README_MAX_BYTES
    assert result.original_bytes > README_MAX_BYTES


def test_quick_start_section_survives_truncation_even_when_far_from_top(tmp_path):
    """The exact failure mode found in the validation spike: a flat cut
    kept boilerplate and lost the section that actually mattered. Build a
    README where junk comes first and the useful section is near the end,
    past where a flat 50KB cut would land."""
    junk = "Lorem ipsum filler content. " * 3000  # well over 50KB on its own
    readme = (
        "# My Tool\n\n"
        f"## Badges And Fluff\n\n{junk}\n\n"
        "## Quick Start\n\n```bash\npip install mytool\nmytool run\n```\n"
    )
    assert len(readme.encode("utf-8")) > README_MAX_BYTES

    (tmp_path / "README.md").write_text(readme)
    result = parse_readme(tmp_path)

    assert result.truncated is True
    assert "pip install mytool" in result.content
    assert "mytool run" in result.content


def test_truncation_preserves_original_document_order(tmp_path):
    """Priority sections get first claim on the byte budget, but the
    output should still read top-to-bottom in the original order — not
    with priority sections physically moved to the front."""
    readme = "# Intro\n\nIntro text.\n\n## Usage\n\nUsage text.\n\n## More\n\nMore text.\n"
    (tmp_path / "README.md").write_text(readme)
    result = parse_readme(tmp_path)  # well under cap, so untouched — sanity check
    assert result.content.index("Intro text") < result.content.index("Usage text")
    assert result.content.index("Usage text") < result.content.index("More text")


# --- _is_priority heading matching: realistic headings, not bare keywords -


@pytest.mark.parametrize(
    "heading",
    [
        "Install (agent skill)",  # the exact real heading that exposed the exact-match bug
        "Installing from source",
        "Installation Guide",
        "Quick Start",
        "Getting Started",
        "Usage",
    ],
)
def test_realistic_priority_headings_are_matched(heading):
    assert _is_priority(_Section(heading_text=heading, text=""))


@pytest.mark.parametrize(
    "heading",
    [
        "Uninstall",  # must NOT false-match "install" mid-word
        "Uninstalling the CLI",
        "Contributing",
        "License",
        "Badges",
    ],
)
def test_unrelated_headings_are_not_matched(heading):
    assert not _is_priority(_Section(heading_text=heading, text=""))


def test_readme_with_no_headings_still_gets_capped(tmp_path):
    """No markdown structure to be section-aware about — still must not
    exceed the byte cap."""
    (tmp_path / "README.md").write_text("x" * (README_MAX_BYTES * 2))
    result = parse_readme(tmp_path)
    assert result.truncated is True
    assert len(result.content.encode("utf-8")) <= README_MAX_BYTES
