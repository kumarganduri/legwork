import os
import json
import math
import wave
import struct
import numpy as np
from typing import List, Dict, Optional, Any

from mcp.server.fastmcp import FastMCP

# Avoid importing faster_whisper at module import time to allow self-test to control env
_mcp_name = "faster_whisper_mcp"
mcp = FastMCP(_mcp_name)

DEFAULT_MODEL_DIR = os.path.abspath("./models/faster-whisper-tiny.en")


def _read_wav_as_float32_mono_16k(path: str) -> np.ndarray:
    """
    Read a WAV file and return a float32 mono array at 16 kHz in range [-1, 1].
    If not mono/16k, performs simple mono mixdown and linear resampling.
    Supports 8-bit (unsigned) and 16-bit PCM. Raises for other formats.
    """
    with wave.open(path, "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        n_frames = wf.getnframes()
        comp_type = wf.getcomptype()

        if comp_type != "NONE":
            raise ValueError(f"Compressed WAV not supported (comptype={comp_type}). Provide PCM WAV.")

        raw_bytes = wf.readframes(n_frames)

    if sampwidth == 1:
        # 8-bit unsigned PCM -> convert to float32 [-1, 1]
        dtype = np.uint8
        data = np.frombuffer(raw_bytes, dtype=dtype).astype(np.float32)
        data = (data - 128.0) / 128.0
    elif sampwidth == 2:
        # 16-bit signed PCM
        dtype = np.int16
        data = np.frombuffer(raw_bytes, dtype=dtype).astype(np.float32)
        data = data / 32768.0
    else:
        raise ValueError(f"Unsupported sample width: {sampwidth*8} bits. Only 8-bit and 16-bit PCM are supported.")

    if n_channels > 1:
        # Interleaved channels: reshape and average to mono
        try:
            data = data.reshape(-1, n_channels).mean(axis=1)
        except ValueError:
            # In case of misalignment, fallback to simple stride-based mean
            frames = len(data) // n_channels
            data = data[: frames * n_channels].reshape(frames, n_channels).mean(axis=1)

    if framerate != 16000:
        # Linear resampling to 16 kHz
        src = data
        src_len = len(src)
        if src_len == 0:
            return np.zeros(0, dtype=np.float32)
        tgt_len = int(round(src_len * 16000.0 / float(framerate)))
        # Create source index positions
        x = np.linspace(0.0, src_len - 1, num=tgt_len, dtype=np.float64)
        xp = np.arange(src_len, dtype=np.float64)
        data = np.interp(x, xp, src).astype(np.float32)

    # Clip to [-1, 1] to avoid any numeric issues
    np.clip(data, -1.0, 1.0, out=data)
    return data.astype(np.float32)


def _load_model(
    model_path_or_name: Optional[str],
    device: str = "cpu",
    compute_type: str = "int8",
):
    """
    Load Faster-Whisper model from a local directory or a model name.
    Defaults to the pre-downloaded tiny.en in ./models if not provided.
    """
    from faster_whisper import WhisperModel  # import here to avoid side effects on module import

    model_source = model_path_or_name or DEFAULT_MODEL_DIR
    return WhisperModel(model_source, device=device, compute_type=compute_type)


@mcp.tool()
def transcribe_audio(
    audio_path: str,
    model: Optional[str] = None,
    device: str = "cpu",
    compute_type: str = "int8",
    beam_size: int = 5,
    vad_filter: bool = False,
    word_timestamps: bool = False,
    language: Optional[str] = None,
    condition_on_previous_text: bool = True,
    task: str = "transcribe",
) -> Dict[str, Any]:
    """
    Transcribe a local WAV audio file to text using Faster-Whisper.

    Notes:
    - Runs fully offline with the pre-downloaded model in ./models/faster-whisper-tiny.en by default.
    - To avoid PyAV/FFmpeg, this reads WAV directly and passes raw audio samples to Faster-Whisper.
    - Supported input: PCM WAV (8-bit unsigned or 16-bit signed). Other formats are not supported in this wrapper.
    - Resamples and mixes to mono 16 kHz if needed.

    Args:
      audio_path: Path to a local WAV file (read-only).
      model: Local path to a CTranslate2-converted Whisper model directory, or a model name.
             Defaults to ./models/faster-whisper-tiny.en.
      device: "cpu" or "cuda".
      compute_type: e.g., "int8", "float32", "int8_float16", etc.
      beam_size: Beam size for decoding.
      vad_filter: Whether to enable VAD (requires additional model; may not work offline).
      word_timestamps: Whether to return word-level timestamps.
      language: Optional language hint/code (e.g., "en").
      condition_on_previous_text: Whether to condition on previous text.
      task: "transcribe" or "translate".

    Returns:
      Dict with:
        - language
        - language_probability
        - segments: list of {start, end, text, (optional) words}
    """
    # Read WAV and convert to float32 mono 16k PCM to avoid PyAV usage.
    audio = _read_wav_as_float32_mono_16k(audio_path)

    backend = _load_model(model, device=device, compute_type=compute_type)

    # Perform offline transcription from raw samples; sampling rate is 16000 by construction.
    segments_iter, info = backend.transcribe(
        audio,
        beam_size=beam_size,
        vad_filter=vad_filter,
        word_timestamps=word_timestamps,
        language=language,
        condition_on_previous_text=condition_on_previous_text,
        task=task,
    )

    out_segments: List[Dict[str, Any]] = []
    for seg in segments_iter:
        seg_dict: Dict[str, Any] = {
            "start": float(seg.start),
            "end": float(seg.end),
            "text": seg.text,
        }
        if word_timestamps and hasattr(seg, "words") and seg.words is not None:
            seg_dict["words"] = [
                {"start": float(w.start), "end": float(w.end), "word": w.word}
                for w in seg.words
            ]
        out_segments.append(seg_dict)

    return {
        "language": getattr(info, "language", None),
        "language_probability": float(getattr(info, "language_probability", 0.0) or 0.0),
        "segments": out_segments,
    }


@mcp.tool()
def model_info(model: Optional[str] = None, device: str = "cpu", compute_type: str = "int8") -> Dict[str, Any]:
    """
    Load the model and return basic information to verify offline availability.
    This does not run a transcription.

    Args:
      model: Local model directory or model name. Defaults to ./models/faster-whisper-tiny.en.
      device: "cpu" or "cuda".
      compute_type: e.g., "int8".

    Returns:
      Dict with model path used and faster-whisper version.
    """
    from faster_whisper import __version__ as fw_version  # lightweight
    model_path = model or DEFAULT_MODEL_DIR
    # Instantiate to ensure weights are present and loadable offline.
    _ = _load_model(model_path, device=device, compute_type=compute_type)
    return {"model": os.path.abspath(model_path), "faster_whisper_version": fw_version}


if __name__ == "__main__":
    # Self-test: verify import and local model can be loaded offline.
    import sys

    # 1) Check faster_whisper import works and has a non-empty version.
    try:
        import faster_whisper as fw  # noqa: F401
        from faster_whisper import __version__ as fw_version
    except Exception as e:
        raise RuntimeError(f"Failed to import faster_whisper: {e}")

    if not fw_version:
        raise RuntimeError("faster_whisper version is empty")

    # 2) Ensure default model directory exists (downloaded during install).
    if not os.path.isdir(DEFAULT_MODEL_DIR):
        raise RuntimeError(f"Default model directory not found: {DEFAULT_MODEL_DIR}")

    # 3) Try loading the model (no transcription) to ensure it can be used offline.
    info = model_info()
    if not info.get("faster_whisper_version"):
        raise RuntimeError("Model info did not return a version")

    # 4) Print a small confirmation JSON for debugging.
    print(json.dumps({"ok": True, "version": fw_version, "model": info["model"]}))