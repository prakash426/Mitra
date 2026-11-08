"""Voice output backends. The HUD speaks through the browser; the terminal
speaks through a native backend when one is available and enabled."""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Protocol

MAX_SAY_CHUNK = 1200

VOICE_PERSONAS: dict[str, dict[str, object]] = {
    "jarvis": {
        "label": "Jarvis, measured British butler",
        "voices": ("Daniel", "Oliver", "Gordon", "Arthur"),
        "rate": 178,
        "pitch": 0.92,
        "line": "At your service. Systems nominal.",
    },
    "friday": {
        "label": "Friday, warm Irish analyst",
        "voices": ("Moira", "Serena", "Samantha", "Ava"),
        "rate": 196,
        "pitch": 1.04,
        "line": "Hey, I am here. What are we solving first?",
    },
}

_HUMAN_SWAPS = (
    (r"\bI am Mitra, working on project (\S+)\.", r"Mitra here, on \1."),
    (r"\bType 'help' to see examples\.", r"Say help any time for ideas."),
    (r"\btell me more\b", r"want to go deeper"),
    (r"\bHere is the direct result\b", r"Here is what I found"),
    (r"\bI could not reach\b", r"I lost"),
)


def humanize(text: str) -> str:
    """Shape a reply for speech: contractions, breath pauses, no markup."""
    cleaned = re.sub(r"https?://\S+", "link", text)
    cleaned = re.sub(r"[`*_#>|]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    for pattern, swap in _HUMAN_SWAPS:
        cleaned = re.sub(pattern, swap, cleaned)
    cleaned = re.sub(r"\bI will\b", "I'll", cleaned)
    cleaned = re.sub(r"\bI have\b", "I've", cleaned)
    cleaned = re.sub(r"\bdo not\b", "don't", cleaned)
    cleaned = re.sub(r"\bcannot\b", "can't", cleaned)
    cleaned = re.sub(r"([.!?])\s+([A-Z])", r"\1 ... \2", cleaned)
    return cleaned[:1500]


def describe_persona(name: str) -> dict[str, object]:
    """Persona preset for the HUD voice picker. Unknown names map to auto."""
    key = (name or "auto").strip().lower()
    if key in VOICE_PERSONAS:
        preset = dict(VOICE_PERSONAS[key])
        preset["id"] = key
        return preset
    return {"id": "auto", "label": "Auto, best system voice", "voices": (), "rate": 190, "pitch": 1.0, "line": "Systems nominal."}


def pick_voice(candidates: tuple[str, ...], installed: list[str]) -> str:
    """First candidate present in installed voices, else the first installed."""
    lowered = {name.lower(): name for name in installed}
    for want in candidates:
        if want.lower() in lowered:
            return lowered[want.lower()]
    return installed[0] if installed else ""


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

    def __init__(self, voice: str = "Samantha", rate: int = 190) -> None:
        self.voice = voice
        self.rate = rate

    def available(self) -> bool:
        return shutil.which("say") is not None

    def speak(self, text: str) -> bool:
        cleaned = humanize(_clean(text))
        if not cleaned or not self.available():
            return False
        try:
            for chunk in _chunks(cleaned):
                subprocess.run(
                    ["say", "-r", str(self.rate), "-v", self.voice, chunk],
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
        voice = section.voice
        rate = section.rate
        persona = describe_persona(getattr(section, "persona", "auto"))
        if voice.strip().lower() in ("auto", "default", "") and persona["voices"]:
            voice = list(persona["voices"])[0]  # type: ignore[arg-type]
        if persona["id"] != "auto" and rate == 190:
            rate = int(persona["rate"])  # type: ignore[arg-type]
        return MacSayBackend(voice=voice, rate=rate)
    return SilentBackend()
