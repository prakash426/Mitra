"""Voice output backends: silent default plus macOS say."""

from __future__ import annotations

import subprocess

from mitra.config import MitraConfig, SpeechConfig
from mitra.speech import (
    MacSayBackend,
    SilentBackend,
    _chunks,
    build_speech,
    describe_persona,
    humanize,
    pick_voice,
)


def test_silent_backend():
    backend = SilentBackend()
    assert backend.available() is True
    assert backend.speak("hello") is False


def test_build_speech_routes():
    assert isinstance(build_speech(MitraConfig()), SilentBackend)
    cfg = MitraConfig(speech=SpeechConfig(backend="say", voice="Alex"))
    backend = build_speech(cfg)
    assert isinstance(backend, MacSayBackend) and backend.voice == "Alex"


def test_chunks_split_long_text():
    long_text = "Hello world. " * 200
    parts = _chunks(long_text)
    assert len(parts) > 1
    assert all(len(part) <= 1200 for part in parts)


def test_say_backend_argv(monkeypatch):
    calls = []
    monkeypatch.setattr("mitra.speech.shutil.which", lambda name: "/usr/bin/say")

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return None

    monkeypatch.setattr("mitra.speech.subprocess.run", fake_run)
    backend = MacSayBackend(voice="Samantha")
    assert backend.available() is True
    assert backend.speak("Hello there.") is True
    assert calls == [["say", "-r", "190", "-v", "Samantha", "Hello there."]]


def test_say_backend_custom_rate(monkeypatch):
    calls = []
    monkeypatch.setattr("mitra.speech.shutil.which", lambda name: "/usr/bin/say")

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return None

    monkeypatch.setattr("mitra.speech.subprocess.run", fake_run)
    assert MacSayBackend(voice="Alex", rate=220).speak("Hi.") is True
    assert calls == [["say", "-r", "220", "-v", "Alex", "Hi."]]


def test_say_backend_unavailable(monkeypatch):
    monkeypatch.setattr("mitra.speech.shutil.which", lambda name: None)
    backend = MacSayBackend()
    assert backend.available() is False
    assert backend.speak("hi") is False


def test_say_backend_failure(monkeypatch):
    monkeypatch.setattr("mitra.speech.shutil.which", lambda name: "/usr/bin/say")

    def boom(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "say")

    monkeypatch.setattr("mitra.speech.subprocess.run", boom)
    assert MacSayBackend().speak("hi") is False


def test_personas_have_distinct_voices():
    jarvis = describe_persona("jarvis")
    friday = describe_persona("friday")
    assert jarvis["voices"][0] == "Daniel"
    assert friday["voices"][0] == "Moira"
    assert jarvis["rate"] != friday["rate"]
    assert describe_persona("bogus")["id"] == "auto"


def test_pick_voice_prefers_persona_candidate():
    assert pick_voice(("Daniel", "Alex"), ["Alex", "Daniel"]) == "Daniel"
    assert pick_voice(("Daniel",), ["Alex"]) == "Alex"


def test_humanize_adds_contractions_and_pauses():
    shaped = humanize("I will help. Here is the plan. I have it.")
    assert "I'll" in shaped and "I've" in shaped
    assert "..." in shaped
    assert "http" not in humanize("see https://example.com/x now")


def test_build_speech_applies_persona_defaults():
    cfg = MitraConfig(speech=SpeechConfig(backend="say", voice="auto", persona="jarvis"))
    backend = build_speech(cfg)
    assert isinstance(backend, MacSayBackend)
    assert backend.voice == "Daniel"
    assert backend.rate == 178
