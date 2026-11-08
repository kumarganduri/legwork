import os
import re
from typing import List, Dict, Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("pyspellchecker_mcp")

def _make_spellchecker(language: str = "en", distance: int = 2):
    # Lazy import inside tool so install-time failures don't break module import
    from spellchecker import SpellChecker
    # Constrain distance to library-supported range [0, 2]
    d = max(0, min(2, int(distance)))
    return SpellChecker(language=language, distance=d)

def _tokenize_text(text: str) -> List[str]:
    # Extract word-like tokens using unicode letters, excluding digits/underscore.
    # This aims to work across languages supported by pyspellchecker.
    # Example matches: "hapenning", "l'été" -> ["l", "été"] (conservative split)
    pattern = r"[^\W\d_]+(?:'[^\W\d_]+)?"
    return re.findall(pattern, text, flags=re.UNICODE)

@mcp.tool()
def check_words(
    words: List[str],
    language: str = "en",
    distance: int = 2,
    include_candidates: bool = True,
) -> List[Dict[str, Any]]:
    """
    Spellcheck a list of words. Returns per-word status, best correction, and candidates.
    """
    sp = _make_spellchecker(language=language, distance=distance)

    # Determine which inputs are misspelled
    unknown_set = sp.unknown(words)

    results: List[Dict[str, Any]] = []
    for w in words:
        is_misspelled = w in unknown_set
        entry: Dict[str, Any] = {
            "word": w,
            "is_misspelled": bool(is_misspelled),
        }
        # Best correction: for correct words, return the original
        try:
            corr = sp.correction(w)
        except Exception:
            corr = None
        if corr is None:
            corr = w if not is_misspelled else None
        entry["correction"] = corr

        if include_candidates:
            # Some versions may return None if no candidates; normalize to []
            try:
                cset = sp.candidates(w)
            except Exception:
                cset = None
            candidates = sorted(list(cset)) if cset else []
            entry["candidates"] = candidates

        results.append(entry)
    return results

@mcp.tool()
def misspellings_in_text(
    text: str,
    language: str = "en",
    distance: int = 2,
) -> List[str]:
    """
    Find unique misspelled words in a block of text.
    """
    tokens = _tokenize_text(text)
    if not tokens:
        return []
    sp = _make_spellchecker(language=language, distance=distance)
    miss = sp.unknown(tokens)
    # Return stable order
    return sorted(miss)

def _self_test():
    # Simple offline verification using the README's quickstart flow
    from spellchecker import SpellChecker

    spell = SpellChecker()
    misspelled = spell.unknown(['something', 'is', 'hapenning', 'here'])
    if not misspelled:
        raise RuntimeError("Self-test: expected at least one misspelled word")

    # Also exercise the wrapper tools in a minimal way without relying on specific outputs
    res = check_words(["hapenning", "is"], language="en", distance=2, include_candidates=True)
    if not res:
        raise RuntimeError("Self-test: check_words returned no results")

    txt_res = misspellings_in_text("Something is hapenning here.", language="en", distance=2)
    # Not asserting on exact content; just ensure call succeeded and produced a list (possibly empty)
    if txt_res is None:
        raise RuntimeError("Self-test: misspellings_in_text returned None")

    print("Self-test passed.")

if __name__ == "__main__":
    _self_test()