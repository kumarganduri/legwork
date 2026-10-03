import os
import json
from typing import Optional, Literal, Dict, Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("moviepy_tools")


def _ensure_ffmpeg_local_cache() -> str:
    """
    Ensure imageio-ffmpeg uses a local, pre-downloaded ffmpeg binary under ./.imageio.
    Returns the absolute path to the ffmpeg executable.
    """
    cache_dir = os.path.abspath("./.imageio")
    os.makedirs(cache_dir, exist_ok=True)
    # Must be set before importing modules that resolve ffmpeg
    os.environ["IMAGEIO_USERDIR"] = cache_dir

    # Use imageio-ffmpeg to resolve the binary path without attempting network access
    import imageio_ffmpeg  # type: ignore

    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    if not os.path.exists(ffmpeg_path):
        raise RuntimeError(
            "FFmpeg binary not found in local cache. "
            "Please re-run installation step to pre-download it."
        )
    return ffmpeg_path


def _abs(path: str) -> str:
    return os.path.abspath(path)


@mcp.tool()
def trim_video(
    input_path: str,
    start: Optional[float] = None,
    end: Optional[float] = None,
    output_path: Optional[str] = None,
    codec: str = "libx264",
    fps: Optional[int] = None,
    audio: bool = True,
    audio_codec: Optional[str] = "aac",
) -> Dict[str, Any]:
    """
    Trim a local video between start and end (in seconds) and write it to an output file.

    - input_path: path to the source video (must exist).
    - start: start time in seconds (optional).
    - end: end time in seconds (optional).
    - output_path: output file name (defaults to <input_stem>_trimmed.mp4 in the tool's own folder;
      the folder the input is in is usually read-only).
    - codec: video codec to use (default: libx264).
    - fps: output frames per second (optional; uses source FPS if None).
    - audio: whether to include audio (default: True).
    - audio_codec: audio codec (default: aac). Ignored if audio=False.

    Returns a JSON dict with output_path (absolute), duration, fps, size (w,h).
    """
    ffmpeg_path = _ensure_ffmpeg_local_cache()
    src = _abs(input_path)
    if not os.path.exists(src):
        raise FileNotFoundError(f"Input file not found: {src}")

    # Defer import until after ffmpeg cache is set
    from moviepy import VideoFileClip  # type: ignore

    base_out = (
        output_path
        if output_path
        else os.path.splitext(os.path.basename(src))[0] + "_trimmed.mp4"
    )
    out_path = _abs(base_out)

    # Process
    with VideoFileClip(src) as clip:
        work = clip
        if start is not None or end is not None:
            work = work.subclipped(start, end)

        # Collect basic info before writing
        info_duration = float(getattr(work, "duration", 0.0) or 0.0)
        info_fps = float(getattr(work, "fps", fps or 0.0) or 0.0)
        info_size = tuple(getattr(work, "size", (0, 0)) or (0, 0))

        write_kwargs: Dict[str, Any] = {
            "codec": codec,
            "audio": audio,
        }
        if fps is not None:
            write_kwargs["fps"] = fps
        if audio and audio_codec:
            write_kwargs["audio_codec"] = audio_codec

        work.write_videofile(out_path, **write_kwargs)

    if not os.path.exists(out_path) or os.path.getsize(out_path) <= 0:
        raise RuntimeError("Output video was not created or is empty.")

    return {
        "output_path": out_path,
        "duration_seconds": info_duration,
        "fps": info_fps,
        "size_wh": info_size,
        "ffmpeg_path": ffmpeg_path,
    }


@mcp.tool()
def video_info(input_path: str) -> Dict[str, Any]:
    """
    Read basic metadata (duration, fps, size) from a local video.
    """
    _ensure_ffmpeg_local_cache()
    src = _abs(input_path)
    if not os.path.exists(src):
        raise FileNotFoundError(f"Input file not found: {src}")

    from moviepy import VideoFileClip  # type: ignore

    with VideoFileClip(src) as clip:
        return {
            "path": src,
            "duration_seconds": float(getattr(clip, "duration", 0.0) or 0.0),
            "fps": float(getattr(clip, "fps", 0.0) or 0.0),
            "size_wh": tuple(getattr(clip, "size", (0, 0)) or (0, 0)),
        }


@mcp.tool()
def moviepy_version() -> str:
    """
    Return the installed MoviePy version.
    """
    import moviepy  # type: ignore

    return getattr(moviepy, "__version__", "unknown")


if __name__ == "__main__":
    # Self-test: prove imports work offline and ffmpeg is locally available,
    # and that we can perform a tiny local encode without network.
    ffmpeg_path = _ensure_ffmpeg_local_cache()

    # 1) Check MoviePy version returns something non-empty
    import moviepy  # type: ignore

    ver = getattr(moviepy, "__version__", "")
    if not ver:
        raise RuntimeError("MoviePy version is empty.")

    # 2) Encode a tiny solid-color clip to MP4 to ensure pipeline works offline
    from moviepy import ColorClip  # type: ignore

    out = os.path.abspath("selftest_moviepy.mp4")
    with ColorClip(size=(64, 48), color=(255, 0, 0)).with_duration(0.5) as clip:
        clip.write_videofile(out, codec="libx264", fps=24, audio=False)

    if not os.path.exists(out) or os.path.getsize(out) <= 0:
        raise RuntimeError("Self-test output video missing or empty.")

    print(json.dumps({"ok": True, "moviepy_version": ver, "ffmpeg": ffmpeg_path, "output": out}))