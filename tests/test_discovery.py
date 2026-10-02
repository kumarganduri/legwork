"""Search wording: GitHub needs every word to match, so filler a model adds
must not hide the right repo (OpenClaw test, 2026-10-01: "extract tables
from pdf" missed pdfplumber)."""

import pytest

from legwork.discovery import search_terms


@pytest.mark.parametrize(
    ("query", "terms"),
    [
        ("extract tables from pdf", "extract tables pdf"),
        ("read PDF tables", "pdf tables"),
        ("a tool to convert the docx files into markdown", "convert docx markdown"),
        ("  Whisper,   speech-to-text!  ", "whisper speech-to-text"),
        ("c++ json parser", "c++ json parser"),
        ("node.js pdf", "node.js pdf"),
    ],
)
def test_filler_words_and_punctuation_are_dropped(query, terms):
    assert search_terms(query) == terms


def test_a_query_of_only_filler_is_kept_rather_than_emptied():
    assert search_terms("the tool") == "the tool"


def test_an_empty_query_stays_empty():
    assert search_terms("  ?! ") == ""


def test_web_search_style_queries_are_cut_to_the_words_that_matter():
    # gpt-5.4-mini in OpenClaw, 2026-10-01
    assert search_terms("GitHub open-source tool extract tables from PDF best maintained stars license") == "extract tables pdf"


def test_search_qualifiers_are_dropped_so_a_query_cant_lift_the_star_floor():
    assert search_terms("site:github.com pdfplumber stars:>=0 user:someone tables") == "pdfplumber tables"


def test_at_most_four_words_are_searched():
    assert search_terms("pdf table extraction camelot tabula pdfplumber") == "pdf table extraction camelot"


def _repo(name, stars):
    return {"full_name": name, "stargazers_count": stars, "description": "", "license": None,
            "language": "Python", "pushed_at": "2026-09-01T00:00:00Z", "created_at": "2020-01-01T00:00:00Z", "topics": []}


def test_a_search_with_too_few_results_drops_its_last_word_down_to_two(monkeypatch):
    from legwork import discovery

    searched = []

    def fake_search(query, sort, in_readme=False):
        searched.append((query, sort, in_readme))
        return [_repo("a/one", 10)] if len(query.split()) > 2 else [_repo("b/two", 50), _repo("c/three", 40), _repo("d/four", 30)]

    monkeypatch.setattr(discovery, "_search", fake_search)
    monkeypatch.setattr(discovery, "_annotate", lambda c, cached=None: c)
    monkeypatch.setattr(discovery.cache_reader, "fetch_index", list)
    found = discovery.find("pdf table extraction camelot")
    assert found.terms == "pdf table"
    assert searched == [
        ("pdf table extraction camelot", "stars", False),
        ("pdf table extraction", "stars", False),
        ("pdf table", "stars", False),
        ("pdf table", None, True),  # relevance also reads READMEs
    ]
    assert [c.slug for c in found.candidates] == ["b/two", "c/three", "d/four"]
    assert discovery.describe(found).startswith("Searched GitHub for: pdf table\n\n1. b/two")


INDEX = [
    {"repo": "SYSTRAN/faster-whisper", "what": "Offline speech-to-text transcription", "about": "Faster Whisper transcription with CTranslate2",
     "topics": ["whisper", "speech-recognition"], "license": "MIT", "language": "Python", "stars": 25000, "created_at": "2023-01-01T00:00:00Z", "pushed_at": "2026-09-01T00:00:00Z"},
    {"repo": "py-pdf/pypdf", "what": "Read, split, merge and transform PDFs", "about": "A pure-python PDF library", "topics": ["pdf"],
     "license": "BSD-3-Clause", "language": "Python", "stars": 10000, "created_at": "2012-01-01T00:00:00Z", "pushed_at": "2026-09-01T00:00:00Z"},
]


def test_cached_tools_that_fit_come_first_even_when_github_ranks_them_low(monkeypatch):
    """"speech to text transcribe" didn't list faster-whisper, so Claude picked
    an uncached repo and, with no key, gave up (QA, 2026-10-02)."""
    from legwork import discovery

    monkeypatch.setattr(discovery.cache_reader, "fetch_index", lambda: INDEX)
    monkeypatch.setattr(discovery, "_search", lambda q, sort, in_readme=False: [_repo("openai/whisper", 100000), _repo("x/voicebox", 5000), _repo("y/other", 400)])
    monkeypatch.setattr(discovery, "_read_url", lambda url: None)  # no README fetches
    found = discovery.find("speech to text transcribe")
    assert found.candidates[0].slug == "SYSTRAN/faster-whisper" and found.candidates[0].in_cache
    assert [c.slug for c in found.candidates[1:]] == ["openai/whisper", "x/voicebox", "y/other"]
    assert not any(c.in_cache for c in found.candidates[1:])


def test_stems_dont_overreach():
    from legwork.discovery import cache_matches

    # "transcribe" must not match pypdf's "transform"
    assert [c.slug for c in cache_matches(["speech", "text", "transcribe"], INDEX)] == ["SYSTRAN/faster-whisper"]
    assert [c.slug for c in cache_matches(["pdf"], INDEX)] == ["py-pdf/pypdf"]


def test_the_cache_index_lists_exactly_the_cache_folders():
    """Run scripts/build_cache_index.py after adding or removing an entry."""
    import json
    from pathlib import Path

    cache = Path(__file__).resolve().parents[1] / "cache"
    folders = {p.name for p in cache.iterdir() if (p / "manifest.json").is_file()}
    index = json.loads((cache / "index.json").read_text())
    assert {e["folder"] for e in index} == folders
    for e in index:
        manifest = json.loads((cache / e["folder"] / "manifest.json").read_text())
        assert manifest["source_repo_url"].lower() == f"https://github.com/{e['repo']}".lower()
