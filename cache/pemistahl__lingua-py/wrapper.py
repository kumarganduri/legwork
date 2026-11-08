import os
from typing import List, Optional, Dict, Any, Tuple
from functools import lru_cache

from mcp.server.fastmcp import FastMCP

# Lingua imports (works for both 1.x pure-Python and 2.x Rust-backed)
from lingua import Language, LanguageDetectorBuilder

mcp = FastMCP("lingua_language_detector")

# Cache detectors to avoid repeated building
_detector_cache: Dict[Tuple[Any, ...], Any] = {}


def _format_iso(enum_obj) -> Optional[str]:
    # Safely extract enum name like "EN" or "ENG"
    if enum_obj is None:
        return None
    return getattr(enum_obj, "name", None)


def _format_language(lng: Any) -> Dict[str, Optional[str]]:
    # lng is a Language enum member
    return {
        "name": getattr(lng, "name", str(lng)),
        "iso639_1": _format_iso(getattr(lng, "iso_code_639_1", None)),
        "iso639_3": _format_iso(getattr(lng, "iso_code_639_3", None)),
    }


def _resolve_languages(items: Optional[List[str]]) -> Optional[List[Language]]:
    """
    Resolve a list of language identifiers to Language enums.
    Accepts:
    - Full language names (case-insensitive), e.g., "english", "German"
    - ISO 639-1 codes (2 letters), e.g., "en", "de"
    - ISO 639-3 codes (3 letters), e.g., "eng", "deu"
    Returns None if items is None.
    """
    if items is None:
        return None

    all_langs = list(Language.all())
    resolved: List[Language] = []

    # Build reverse maps for ISO codes
    iso1_map = {}
    iso3_map = {}
    for l in all_langs:
        iso1 = getattr(l, "iso_code_639_1", None)
        iso3 = getattr(l, "iso_code_639_3", None)
        if iso1 is not None:
            iso1_map[getattr(iso1, "name", "").lower()] = l
        if iso3 is not None:
            iso3_map[getattr(iso3, "name", "").lower()] = l

    for item in items:
        if not isinstance(item, str):
            raise ValueError(f"Language identifier must be a string, got {type(item)}")
        s = item.strip()
        if not s:
            continue

        # Try by Language name via from_str (case-insensitive)
        lang = None
        try:
            lang = Language.from_str(s)
        except Exception:
            lang = None

        if lang is None:
            # Try by ISO code
            key = s.replace("-", "_").lower()
            if len(key) == 2 and key in iso1_map:
                lang = iso1_map[key]
            elif len(key) == 3 and key in iso3_map:
                lang = iso3_map[key]

        if lang is None:
            raise ValueError(
                f"Could not resolve language '{item}'. Use full names (e.g., 'English') "
                f"or ISO codes (e.g., 'en', 'eng')."
            )
        resolved.append(lang)

    # Deduplicate and keep stable order by first occurrence
    seen = set()
    unique_resolved = []
    for l in resolved:
        if l not in seen:
            unique_resolved.append(l)
            seen.add(l)
    return unique_resolved


def _detector_key(
    include: Optional[List[Language]],
    exclude: Optional[List[Language]],
    low_accuracy: bool,
    preload_models: bool,
    min_relative_distance: Optional[float],
) -> Tuple[Any, ...]:
    if include is not None:
        langs_key = ("INCLUDE", tuple(sorted(l.name for l in include)))
    elif exclude is not None:
        langs_key = ("ALL_WITHOUT", tuple(sorted(l.name for l in exclude)))
    else:
        langs_key = ("ALL",)
    return (
        langs_key,
        ("LOW_ACC", bool(low_accuracy)),
        ("PRELOAD", bool(preload_models)),
        ("MIN_REL_DIST", round(float(min_relative_distance), 6) if min_relative_distance is not None else None),
    )


def _build_detector(
    include: Optional[List[Language]],
    exclude: Optional[List[Language]],
    low_accuracy: bool,
    preload_models: bool,
    min_relative_distance: Optional[float],
):
    key = _detector_key(include, exclude, low_accuracy, preload_models, min_relative_distance)
    if key in _detector_cache:
        return _detector_cache[key]

    if include is not None and exclude is not None:
        raise ValueError("Provide either 'languages' (include) or 'exclude_languages', not both.")

    if include:
        builder = LanguageDetectorBuilder.from_languages(*include)
    elif exclude:
        builder = LanguageDetectorBuilder.from_all_languages_without(*exclude)
    else:
        builder = LanguageDetectorBuilder.from_all_languages()

    if low_accuracy:
        builder = builder.with_low_accuracy_mode()
    if preload_models:
        builder = builder.with_preloaded_language_models()
    if min_relative_distance is not None:
        builder = builder.with_minimum_relative_distance(float(min_relative_distance))

    detector = builder.build()
    _detector_cache[key] = detector
    return detector


@mcp.tool()
def detect_language(
    text: str,
    languages: Optional[List[str]] = None,
    exclude_languages: Optional[List[str]] = None,
    low_accuracy: bool = False,
    preload_models: bool = False,
    min_relative_distance: Optional[float] = None,
    return_confidences: bool = False,
) -> Dict[str, Any]:
    """
    Detect the primary language of the given text.

    - languages: optional list of language names or ISO codes to include.
    - exclude_languages: optional list of names or ISO codes to exclude (used if 'languages' is not provided).
    - low_accuracy: enable low-accuracy, faster mode.
    - preload_models: preload language models to reduce first-call latency.
    - min_relative_distance: optional minimum relative distance threshold (e.g., 0.9) to return None if too close.
    - return_confidences: include per-language confidence values in the result.
    """
    include_langs = _resolve_languages(languages) if languages is not None else None
    exclude_langs = _resolve_languages(exclude_languages) if exclude_languages is not None else None

    detector = _build_detector(include_langs, exclude_langs, low_accuracy, preload_models, min_relative_distance)

    detected = detector.detect_language_of(text)
    result: Dict[str, Any] = {
        "detected_language": _format_language(detected) if detected is not None else None,
    }

    if return_confidences:
        confs = detector.compute_language_confidence_values(text)
        result["confidence_values"] = [
            {"language": _format_language(cv.language), "value": float(cv.value)} for cv in confs
        ]

    return result


@mcp.tool()
def detect_multiple_languages(
    text: str,
    languages: Optional[List[str]] = None,
    exclude_languages: Optional[List[str]] = None,
    low_accuracy: bool = False,
    preload_models: bool = False,
) -> Dict[str, Any]:
    """
    Detect contiguous language segments in a mixed-language text.
    """
    include_langs = _resolve_languages(languages) if languages is not None else None
    exclude_langs = _resolve_languages(exclude_languages) if exclude_languages is not None else None

    detector = _build_detector(include_langs, exclude_langs, low_accuracy, preload_models, None)

    segments = detector.detect_multiple_languages_of(text)
    out = []
    for seg in segments:
        start = int(getattr(seg, "start_index", 0))
        end = int(getattr(seg, "end_index", start))
        lang = getattr(seg, "language", None)
        out.append(
            {
                "language": _format_language(lang) if lang is not None else None,
                "start_index": start,
                "end_index": end,
                "text": text[start:end],
            }
        )
    return {"segments": out}


@mcp.tool()
def list_supported_languages() -> List[Dict[str, Optional[str]]]:
    """
    List all languages supported by the detector with their ISO codes.
    """
    langs = sorted(Language.all())
    return [_format_language(l) for l in langs]


if __name__ == "__main__":
    # Offline self-test: build a tiny detector and compute confidence values
    test_languages = [Language.ENGLISH, Language.GERMAN]
    detector = LanguageDetectorBuilder.from_languages(*test_languages).build()
    confs = detector.compute_language_confidence_values("hello world")
    if not confs:
        raise RuntimeError("Self-test failed: empty confidence values")
    print("Self-test OK: confidences computed for", len(confs), "languages")