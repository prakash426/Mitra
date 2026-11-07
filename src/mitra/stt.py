"""Server side speech to text behind a pluggable engine.

The API accepts raw audio bytes; engines turn them into text. The stub
engine keeps tests and offline runs deterministic. The faster-whisper
engine needs the optional faster-whisper package plus a downloaded
model, and fails with install guidance when either is missing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

ALLOWED_SUFFIXES = (".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm")


class TranscribeError(Exception):
    """Transcription failed. The message is safe to show the user."""


class SpeechToTextEngine(ABC):
    name: str = "base"

    @abstractmethod
    def transcribe(self, audio_path: Path) -> str:
        """Return the transcript for an audio file."""


class StubEngine(SpeechToTextEngine):
    """Deterministic stand in. Never touches audio content."""

    name = "stub"

    def transcribe(self, audio_path: Path) -> str:
        try:
            size = audio_path.stat().st_size
        except OSError:
            size = 0
        return "stub transcript (%d bytes)" % size


class FasterWhisperEngine(SpeechToTextEngine):
    """Local faster-whisper transcription. Import guarded."""

    name = "faster_whisper"

    def __init__(self, model: str = "base") -> None:
        self.model = model

    def transcribe(self, audio_path: Path) -> str:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise TranscribeError(
                "faster-whisper is not installed. Run "
                "pip install faster-whisper, then retry."
            )
        try:
            recognizer = WhisperModel(self.model)
            segments, _info = recognizer.transcribe(str(audio_path))
            text = " ".join(segment.text.strip() for segment in segments).strip()
        except TranscribeError:
            raise
        except Exception as exc:
            raise TranscribeError("transcription failed: %s." % exc)
        if not text:
            raise TranscribeError("no speech detected in that audio.")
        return text


def build_engine(engine: str, model: str) -> SpeechToTextEngine:
    if engine == "faster_whisper":
        return FasterWhisperEngine(model)
    return StubEngine()
