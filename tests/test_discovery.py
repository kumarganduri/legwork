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


def test_the_index_and_cache_readme_match_the_manifests():
    """The index kept faster-whisper's old English-only description after the
    wrapper changed, and the README table showed unpinned installs (fresh QA,
    2026-10-03). Run scripts/build_cache_index.py after changing an entry."""
    import json
    from pathlib import Path

    import importlib.util

    cache = Path(__file__).resolve().parents[1] / "cache"
    spec = importlib.util.spec_from_file_location("bci", cache.parent / "scripts" / "build_cache_index.py")
    bci = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bci)
    readme = (cache / "README.md").read_text()
    for e in json.loads((cache / "index.json").read_text()):
        manifest = json.loads((cache / e["folder"] / "manifest.json").read_text())
        assert e["what"] == " ".join(manifest["entrypoint"].split()), e["repo"]
        assert f"| {bci.install_summary(manifest['install_command'])} |" in readme, e["repo"]


def test_odd_index_entries_are_cleaned_or_skipped_and_never_break_search(monkeypatch):
    """A null description, stars as a string or a missing date made every
    find_tools call fail (Codex and pre-launch review, 2026-10-03)."""
    import json as _json

    from legwork import cache_reader, discovery

    raw = [
        {"repo": "owner/pdf", "what": None, "stars": "100", "topics": "pdf"},
        {"repo": "owner/tables", "what": "extract pdf tables", "created_at": "", "pushed_at": "not a date"},
        {"repo": 42}, "junk", {"repo": "no-slash"},
    ]
    monkeypatch.setattr(cache_reader, "_read", lambda base, rel: _json.dumps(raw))
    monkeypatch.setenv("LEGWORK_CACHE_URL", "https://example.invalid/cache")
    index = cache_reader.fetch_index()
    assert [e["repo"] for e in index] == ["owner/pdf", "owner/tables"]
    assert index[0]["what"] == "" and index[0]["stars"] == 0 and index[0]["topics"] == []
    matches = discovery.cache_matches(["pdf", "tables"], index)
    text = discovery.describe(discovery.Found("pdf tables", matches))
    assert "owner/tables" in text and "update date unknown" in text


@pytest.mark.parametrize(
    ("need", "first"),
    [
        ("transcribe audio", "SYSTRAN/faster-whisper"),
        ("ocr image text", "RapidAI/RapidOCR"),
        ("find secrets in code", "gitleaks/gitleaks"),
        ("html to markdown", "matthewwithanm/python-markdownify"),
        ("remove image background", "danielgatis/rembg"),
        ("read pdf tables", "jsvine/pdfplumber"),
        ("query csv with sql", "duckdb/duckdb"),
        ("download youtube video", "yt-dlp/yt-dlp"),
        # The README's own "try these first" wording (fresh QA, 2026-10-03)
        ("transcribe voice memo", "SYSTRAN/faster-whisper"),
        ("transcribe m4a audio", "SYSTRAN/faster-whisper"),
        ("voice memo to text", "SYSTRAN/faster-whisper"),
        ("pull the tables out of report.pdf", "jsvine/pdfplumber"),
        ("make a qr code", "lincolnloop/python-qrcode"),
        ("text readability", "textstat/textstat"),
    ],
)
def test_the_right_cached_tool_ranks_first_for_common_needs(need, first):
    """Against the real cache index (fresh QA, 2026-10-03: four of these ranked a
    more popular but wrong tool first)."""
    import json
    from pathlib import Path

    from legwork.discovery import cache_matches

    index = json.loads((Path(__file__).resolve().parents[1] / "cache" / "index.json").read_text())
    assert cache_matches(search_terms(need).split(), index)[0].slug == first


@pytest.mark.parametrize("need", ["weather forecast", "speech to text", "make qr code", "translate text", "csv to json", "speech synthesis"])
def test_unrelated_cached_tools_are_not_lifted(need):
    """One shared word ("for" in "forecast", "code", "text") put gitleaks,
    shellcheck or ruff at the top (fresh QA, 2026-10-03)."""
    import json
    from pathlib import Path

    from legwork.discovery import cache_matches

    index = json.loads((Path(__file__).resolve().parents[1] / "cache" / "index.json").read_text())
    unrelated = {"SYSTRAN/faster-whisper"} if need == "speech synthesis" else set()
    unrelated |= {"gitleaks/gitleaks", "ast-grep/ast-grep", "Zulko/moviepy", "koalaman/shellcheck", "python-pillow/Pillow",
                 "prettier/prettier", "astral-sh/ruff", "adbar/trafilatura"}
    assert not unrelated & {c.slug for c in cache_matches(search_terms(need).split(), index)}


def test_cached_matches_survive_a_github_search_failure(monkeypatch):
    """Rate-limited or offline, find_tools threw away the locally matched
    cached tools and returned nothing (fresh QA, 2026-10-03)."""
    from legwork import discovery

    monkeypatch.setattr(discovery.cache_reader, "fetch_index", lambda: INDEX)

    def limited(*a, **k):
        raise discovery.DiscoveryError("GitHub's search limit was reached")

    monkeypatch.setattr(discovery, "_search", limited)
    found = discovery.find("speech to text transcribe")
    assert [c.slug for c in found.candidates] == ["SYSTRAN/faster-whisper"]
    assert "search limit" in discovery.describe(found) and "Legwork cache only" in discovery.describe(found)
    with pytest.raises(discovery.DiscoveryError):
        discovery.find("something nobody caches zzzz")
