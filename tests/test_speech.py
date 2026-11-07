"""Voice output backends: silent default plus macOS say."""

from __future__ import annotations

import subprocess

from mitra.config import MitraConfig, SpeechConfig
from mitra.speech import MacSayBackend, SilentBackend, _chunks, build_speech


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
