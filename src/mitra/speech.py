"""Voice output backends. The HUD speaks through the browser; the terminal
speaks through a native backend when one is available and enabled."""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Protocol

MAX_SAY_CHUNK = 1200


class SpeechBackend(Protocol):
    name: str

    def available(self) -> bool:
        """True when this backend can produce audio on this machine."""
        ...  # pragma: no cover

    def speak(self, text: str) -> bool:
        """Speak text aloud. Returns True only when audio was produced."""
        ...  # pragma: no cover


class SilentBackend:
    """Default backend. Never speaks, never fails."""

    name = "silent"

    def available(self) -> bool:
        return True

    def speak(self, text: str) -> bool:
        return False


def _clean(text: str) -> str:
    cleaned = re.sub(r"[`*_#]", "", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _chunks(text: str, limit: int = MAX_SAY_CHUNK) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current = ""
    for part in parts:
        candidate = (current + " " + part).strip() if current else part.strip()
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current)
            while len(part) > limit:
                chunks.append(part[:limit])
                part = part[limit:]
            current = part.strip()
    if current:
        chunks.append(current)
    return [c for c in chunks if c]


class MacSayBackend:
    """macOS `say` command. Fixed argv, no shell, chunked input."""

    name = "say"

    def __init__(self, voice: str = "Samantha") -> None:
        self.voice = voice

    def available(self) -> bool:
        return shutil.which("say") is not None

    def speak(self, text: str) -> bool:
        cleaned = _clean(text)
        if not cleaned or not self.available():
            return False
        try:
            for chunk in _chunks(cleaned):
                subprocess.run(
                    ["say", "-v", self.voice, chunk],
                    timeout=90,
                    check=True,
                    capture_output=True,
                )
        except (subprocess.SubprocessError, OSError):
            return False
        return True


def build_speech(config) -> SpeechBackend:
    """Create the configured speech backend. Unknown names stay silent."""
    section = config.speech
    if section.backend == "say":
        return MacSayBackend(voice=section.voice)
    return SilentBackend()
