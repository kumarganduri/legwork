"""Speech to text with faster-whisper, offline, for any audio or video file.

Reviewed and rewritten by hand (2026-10-03): the earlier wrapper read only
PCM WAV with the English-only tiny model, so "transcribe my voice memo"
(.m4a) failed. Decoding now goes through faster-whisper's own PyAV decoder
(pinned to a version that works with it), and the multilingual `base` model
is downloaded during install into ./models, next to this file.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Literal, Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("faster-whisper")

MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "faster-whisper-base")
_model = None
_model_lock = threading.Lock()


def _get_model():
    """Load the model once, from the folder the install step filled (the tool runs offline)."""
    global _model
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel

            if not os.path.isdir(MODEL_DIR):
                raise RuntimeError(f"Model not found at {MODEL_DIR}; reinstall the tool.")
            _model = WhisperModel(MODEL_DIR, device="cpu", compute_type="int8")
        return _model


@mcp.tool()
def transcribe_audio(
    audio_path: str,
    language: Optional[str] = None,
    task: Literal["transcribe", "translate"] = "transcribe",
    word_timestamps: bool = False,
) -> Dict[str, Any]:
    """
    Transcribe speech in an audio or video file to text, offline.

    Accepts common formats: m4a (voice memos), mp3, wav, flac, ogg, opus, aac,
    mp4, mov, mkv, webm. Multilingual: the language is detected unless given.

    Args:
      audio_path: Path to the file (read-only).
      language: Optional language code hint, e.g. "en", "es", "hi".
      task: "transcribe" (keep the spoken language) or "translate" (into English).
      word_timestamps: Also return per-word timings.

    Returns:
      {"text", "language", "language_probability", "duration_seconds", "segments": [{start, end, text}]},
      plus "warning" when the language detection is unsure or most of a long file came back empty.
    """
    if not os.path.isfile(audio_path):
        raise FileNotFoundError(f"No such file: {audio_path}")
    from faster_whisper.audio import decode_audio

    audio = decode_audio(audio_path, sampling_rate=16000)
    segments_iter, info = _get_model().transcribe(
        audio, beam_size=5, language=language, task=task, word_timestamps=word_timestamps
    )
    segments: List[Dict[str, Any]] = []
    for seg in segments_iter:
        item: Dict[str, Any] = {"start": round(float(seg.start), 2), "end": round(float(seg.end), 2), "text": seg.text.strip()}
        if word_timestamps and getattr(seg, "words", None):
            item["words"] = [{"start": round(float(w.start), 2), "end": round(float(w.end), 2), "word": w.word} for w in seg.words]
        segments.append(item)
    result: Dict[str, Any] = {
        "text": " ".join(s["text"] for s in segments).strip(),
        "language": getattr(info, "language", None),
        "language_probability": round(float(getattr(info, "language_probability", 0.0) or 0.0), 3),
        "duration_seconds": round(float(getattr(info, "duration", 0.0) or 0.0), 2),
        "segments": segments,
    }
    # Telugu was detected as Malayalam (47%) and most of the recording came back
    # empty, with nothing in the reply to say why (Claude Desktop, 2026-10-06).
    warnings: List[str] = []
    if language is None and result["language_probability"] < 0.7:
        top = [f"{code} {p:.0%}" for code, p in (getattr(info, "all_language_probs", None) or [])[:3]]
        warnings.append(
            f"Unsure which language this is ({', '.join(top) or result['language']}), so the text may be wrong "
            "or missing. If the user knows the language, call again with language set (e.g. 'te' for Telugu). "
            "The built-in base model is small: it does best in English and other widely spoken languages."
        )
    covered = sum(s["end"] - s["start"] for s in segments)
    if result["duration_seconds"] > 60 and covered < 0.3 * result["duration_seconds"]:
        warnings.append(
            f"Speech was found in only {covered:.0f} of {result['duration_seconds']:.0f} seconds. If the recording "
            "is speech throughout, the model likely couldn't follow the language: say so rather than presenting "
            "this as a full transcript."
        )
    if warnings:
        result["warning"] = " ".join(warnings)
    return result


@mcp.tool()
def model_info() -> Dict[str, Any]:
    """The speech model this tool uses, and the faster-whisper version."""
    from faster_whisper import __version__ as fw_version

    _get_model()
    return {"model": "faster-whisper base (multilingual)", "path": MODEL_DIR, "faster_whisper_version": fw_version}


if __name__ == "__main__":
    # Self-test, offline: load the model from ./models and run it on one second
    # of silence. This proves the weights were downloaded during install.
    import numpy as np

    segments, info = _get_model().transcribe(np.zeros(16000, dtype=np.float32), beam_size=1)
    list(segments)
    if not getattr(info, "duration", None):
        raise SystemExit("Self-test failed: the model did not process audio")
    print("Self-test passed: faster-whisper base loaded offline")
