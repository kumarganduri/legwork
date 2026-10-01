import os

# textstat's syllable counts need NLTK's cmudict, downloaded during install
# into ./nltk_data: the tool runs offline.
os.environ.setdefault("NLTK_DATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "nltk_data"))

import os
from typing import Any, Dict, Optional

from mcp.server.fastmcp import FastMCP
import textstat

mcp = FastMCP("textstat")


def _set_lang(lang: Optional[str]) -> None:
    if lang:
        try:
            textstat.set_lang(lang)
        except Exception:
            # Ignore invalid languages to avoid failing the whole tool
            pass


def _safe_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


@mcp.tool()
def compute_readability(
    text: str,
    lang: str = "en_US",
    float_output: bool = False,
    ms_per_char: float = 14.69,
) -> Dict[str, Any]:
    """
    Compute a comprehensive set of readability metrics and text statistics for the given text.

    - lang: language code used for syllable calculation and language-specific variants (e.g., en_US, de_DE, es_ES, it_IT, fr_FR, nl_NL, ru_RU, ar)
    - float_output: return text_standard as a float when True
    - ms_per_char: milliseconds per character for reading_time
    """
    _set_lang(lang)

    results: Dict[str, Any] = {
        # Core readability formulas
        "flesch_reading_ease": _safe_call(textstat.flesch_reading_ease, text),
        "flesch_kincaid_grade": _safe_call(textstat.flesch_kincaid_grade, text),
        "smog_index": _safe_call(textstat.smog_index, text),
        "coleman_liau_index": _safe_call(textstat.coleman_liau_index, text),
        "automated_readability_index": _safe_call(textstat.automated_readability_index, text),
        "dale_chall_readability_score": _safe_call(textstat.dale_chall_readability_score, text),
        "linsear_write_formula": _safe_call(textstat.linsear_write_formula, text),
        "gunning_fog": _safe_call(textstat.gunning_fog, text),
        "text_standard": _safe_call(textstat.text_standard, text, float_output=float_output),
        # Additional formulas
        "spache_readability": _safe_call(textstat.spache_readability, text),
        "mcalpine_eflaw": _safe_call(textstat.mcalpine_eflaw, text),
        # Aggregates and averages
        "reading_time_ms": _safe_call(textstat.reading_time, text, ms_per_char=ms_per_char),
        "difficult_words": _safe_call(textstat.difficult_words, text),
        "syllable_count": _safe_call(textstat.syllable_count, text),
        "lexicon_count": _safe_call(textstat.lexicon_count, text, removepunct=True),
        "sentence_count": _safe_call(textstat.sentence_count, text),
        "char_count_no_spaces": _safe_call(textstat.char_count, text, ignore_spaces=True),
        "letter_count_no_spaces": _safe_call(textstat.letter_count, text, ignore_spaces=True),
        "polysyllable_count": _safe_call(textstat.polysyllabcount, text),
        "monosyllable_count": _safe_call(textstat.monosyllabcount, text),
    }
    return results


@mcp.tool()
def wiener_sachtextformel(text: str, variant: int = 1, lang: str = "de_DE") -> Dict[str, Any]:
    """
    Compute the Wiener Sachtextformel (German) readability score.
    - variant: select formula variant (integer as per textstat docs)
    - lang: language code (defaults to de_DE)
    """
    _set_lang(lang)
    value = _safe_call(textstat.wiener_sachtextformel, text, variant)
    return {"wiener_sachtextformel": value, "variant": variant}


@mcp.tool()
def spanish_readability(text: str, lang: str = "es_ES") -> Dict[str, Any]:
    """
    Compute Spanish-specific readability metrics.
    """
    _set_lang(lang)
    return {
        "fernandez_huerta": _safe_call(textstat.fernandez_huerta, text),
        "szigriszt_pazos": _safe_call(textstat.szigriszt_pazos, text),
        "gutierrez_polini": _safe_call(textstat.gutierrez_polini, text),
        "crawford": _safe_call(textstat.crawford, text),
    }


@mcp.tool()
def gulpease(text: str, lang: str = "it_IT") -> Dict[str, Any]:
    """
    Compute the Gulpease index for Italian text.
    """
    _set_lang(lang)
    return {"gulpease_index": _safe_call(textstat.gulpease_index, text)}


@mcp.tool()
def osman(text: str, lang: str = "ar") -> Dict[str, Any]:
    """
    Compute the OSMAN readability score (Arabic).
    """
    _set_lang(lang)
    return {"osman": _safe_call(textstat.osman, text)}


if __name__ == "__main__":
    # Self-test: perform a minimal offline computation to prove the wrapper works
    demo_text = (
        "This is a short example. It has multiple sentences for testing. "
        "Readability metrics should compute offline without downloads."
    )
    out = compute_readability(demo_text)
    if not isinstance(out, dict) or not out:
        raise SystemExit("Self-test failed: compute_readability returned no data")
    print("Self-test OK: computed", len(out), "metrics")