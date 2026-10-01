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
        ("a tool to convert the docx files into markdown", "convert docx files markdown"),
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
    monkeypatch.setattr(discovery, "_annotate", lambda c: c)
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
