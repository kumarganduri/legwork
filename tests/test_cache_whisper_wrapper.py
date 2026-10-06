"""The cached faster-whisper wrapper's warnings, with stand-ins for
faster_whisper and mcp (neither is a Legwork dependency). A Telugu recording
was taken for Malayalam and mostly came back empty, with nothing in the reply
to say why (Claude Desktop, 2026-10-06)."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

WRAPPER = Path(__file__).resolve().parents[1] / "cache" / "systran__faster-whisper" / "wrapper.py"


@pytest.fixture
def transcribe(monkeypatch, tmp_path):
    """Returns run(segments, info, language=None) -> the wrapper's result."""
    fastmcp = types.ModuleType("mcp.server.fastmcp")

    class FastMCP:
        def __init__(self, name):
            pass

        def tool(self):
            return lambda fn: fn

    fastmcp.FastMCP = FastMCP
    for name in ("mcp", "mcp.server"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp)

    state = {}

    class WhisperModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, audio, **kwargs):
            state["kwargs"] = kwargs
            return iter(state["segments"]), state["info"]

    fw = types.ModuleType("faster_whisper")
    fw.WhisperModel = WhisperModel
    audio_mod = types.ModuleType("faster_whisper.audio")
    audio_mod.decode_audio = lambda path, sampling_rate: [0.0]
    monkeypatch.setitem(sys.modules, "faster_whisper", fw)
    monkeypatch.setitem(sys.modules, "faster_whisper.audio", audio_mod)

    spec = importlib.util.spec_from_file_location("whisper_wrapper", WRAPPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "models").mkdir()
    module.MODEL_DIR = str(tmp_path / "models")
    audio = tmp_path / "memo.m4a"
    audio.write_bytes(b"x")

    def run(segments, info, language=None):
        state["segments"] = [SimpleNamespace(start=s, end=e, text=t, words=None) for s, e, t in segments]
        state["info"] = info
        module._model = None
        return module.transcribe_audio(str(audio), language=language)

    return run


def info(language, probability, duration, probs=None):
    return SimpleNamespace(language=language, language_probability=probability, duration=duration, all_language_probs=probs)


def test_a_clear_english_memo_has_no_warning(transcribe):
    result = transcribe([(0.0, 4.0, "Send the plan by Tuesday.")], info("en", 0.99, 4.1, [("en", 0.99)]))
    assert "warning" not in result and result["text"] == "Send the plan by Tuesday."


def test_unsure_language_and_little_speech_are_said_first(transcribe):
    result = transcribe([(210.0, 216.0, "x")], info("ml", 0.47, 218.4, [("ml", 0.47), ("te", 0.42), ("ta", 0.03)]))
    assert list(result)[0] == "warning"  # first: a long transcript can't push it past the reply limit
    assert "ml 47%, te 42%, ta 3%" in result["warning"] and "language argument" in result["warning"]
    assert "only 6 of 218 seconds" in result["warning"]


def test_a_language_the_user_set_isnt_second_guessed(transcribe):
    result = transcribe([(0.0, 100.0, "x")], info("te", 0.5, 120.0), language="te")
    assert "warning" not in result


def test_no_guesses_and_no_audio_dont_crash(transcribe):
    result = transcribe([], info(None, 0.0, 0.0, None))
    assert "no clear candidate" in result["warning"] and "seconds" not in result["warning"]
