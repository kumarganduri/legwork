import json
import os
import subprocess
from typing import List, Optional, Dict, Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("youtube_transcript_api_mcp")

def _fetched_to_dict(fetched) -> Dict[str, Any]:
    # Convert a FetchedTranscript to a serializable dict
    return {
        "video_id": getattr(fetched, "video_id", None),
        "language": getattr(fetched, "language", None),
        "language_code": getattr(fetched, "language_code", None),
        "is_generated": getattr(fetched, "is_generated", None),
        "snippets": [
            {
                "text": getattr(snippet, "text", ""),
                "start": getattr(snippet, "start", 0.0),
                "duration": getattr(snippet, "duration", 0.0),
            }
            for snippet in fetched
        ],
    }

@mcp.tool()
def get_transcript(
    video_id: str,
    languages: Optional[List[str]] = None,
    preserve_formatting: bool = False,
) -> Dict[str, Any]:
    """
    Fetch the transcript for a YouTube video.

    Args:
        video_id: The YouTube video ID (not a full URL).
        languages: Optional list of language codes in descending priority (e.g., ["de", "en"]).
        preserve_formatting: If True, keep basic HTML formatting (e.g., <i>, <b>).

    Returns:
        A dict containing transcript metadata and a list of snippets with text, start, duration.

    Raises:
        RuntimeError: If retrieval fails.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        ytt_api = YouTubeTranscriptApi()
        fetched = ytt_api.fetch(video_id, languages=languages or ["en"], preserve_formatting=preserve_formatting)
        return _fetched_to_dict(fetched)
    except Exception as e:
        raise RuntimeError(f"Failed to fetch transcript for video_id={video_id}: {e}")

@mcp.tool()
def list_available_transcripts(video_id: str) -> List[Dict[str, Any]]:
    """
    List available transcripts for a YouTube video and their metadata.

    Args:
        video_id: The YouTube video ID (not a full URL).

    Returns:
        A list of dicts with fields: video_id, language, language_code, is_generated,
        is_translatable, translation_languages.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        ytt_api = YouTubeTranscriptApi()
        transcript_list = ytt_api.list(video_id)

        results: List[Dict[str, Any]] = []
        for t in transcript_list:
            # translation_languages is already JSON-serializable (list of dicts)
            results.append(
                {
                    "video_id": getattr(t, "video_id", None),
                    "language": getattr(t, "language", None),
                    "language_code": getattr(t, "language_code", None),
                    "is_generated": getattr(t, "is_generated", None),
                    "is_translatable": getattr(t, "is_translatable", None),
                    "translation_languages": getattr(t, "translation_languages", []),
                }
            )
        return results
    except Exception as e:
        raise RuntimeError(f"Failed to list transcripts for video_id={video_id}: {e}")

@mcp.tool()
def translate_transcript(
    video_id: str,
    source_languages: List[str],
    target_language: str,
    preserve_formatting: bool = False,
) -> Dict[str, Any]:
    """
    Translate a transcript to a target language using YouTube's translation feature.

    Args:
        video_id: The YouTube video ID.
        source_languages: List of language codes to search for the original transcript (e.g., ["en"]).
        target_language: Language code to translate to (e.g., "de").
        preserve_formatting: If True, keep basic HTML formatting.

    Returns:
        A dict of the translated transcript metadata and snippets.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        ytt_api = YouTubeTranscriptApi()
        transcript_list = ytt_api.list(video_id)
        transcript = transcript_list.find_transcript(source_languages or ["en"])
        translated = transcript.translate(target_language)
        fetched = translated.fetch(preserve_formatting=preserve_formatting)
        return _fetched_to_dict(fetched)
    except Exception as e:
        raise RuntimeError(
            f"Failed to translate transcript for video_id={video_id} to {target_language}: {e}"
        )

if __name__ == "__main__":
    # Self-test: run the CLI --help to ensure the package is installed and usable offline.
    try:
        proc = subprocess.run(
            ["youtube_transcript_api", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0 or not output.strip():
            # Fallback: import and print a basic attribute
            import youtube_transcript_api  # type: ignore

            ver = getattr(youtube_transcript_api, "__version__", "")
            if not str(ver):
                raise RuntimeError("youtube_transcript_api appears installed but returned no version/help output.")
        print("Self-test OK")
    except Exception as e:
        raise SystemExit(f"Self-test failed: {e}")