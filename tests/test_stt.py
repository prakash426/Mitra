"""Server speech to text endpoint plus engines."""

from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from mitra.api import build_app
from mitra.config import SpeechToTextConfig
from mitra.stt import FasterWhisperEngine, StubEngine, TranscribeError, build_engine

import pytest


def test_stub_engine_and_builder(tmp_path):
    target = tmp_path / "a.wav"
    target.write_bytes(b"\x00" * 10)
    assert StubEngine().transcribe(target) == "stub transcript (10 bytes)"
    assert isinstance(build_engine("stub", "base"), StubEngine)
    assert isinstance(build_engine("faster_whisper", "base"), FasterWhisperEngine)
    assert isinstance(build_engine("anything-else", "base"), StubEngine)


def test_faster_whisper_graceful_without_lib(tmp_path):
    try:
        import faster_whisper  # noqa: F401

        pytest.skip("faster-whisper installed; live models stay untested offline")
    except ImportError:
        pass
    with pytest.raises(TranscribeError, match="not installed"):
        FasterWhisperEngine("base").transcribe(tmp_path / "x.wav")


def test_stt_stub_roundtrip(orch):
    client = TestClient(build_app(orch))
    resp = client.post("/stt?project=P&suffix=wav", content=b"\x00" * 100)
    assert resp.status_code == 200
    body = resp.json()
    assert body["engine"] == "stub"
    assert "100 bytes" in body["text"]


def test_stt_rejects_bad_input(orch):
    client = TestClient(build_app(orch))
    assert client.post("/stt?suffix=exe", content=b"x").status_code == 400
    assert client.post("/stt?suffix=wav", content=b"").status_code == 400
    orch.config = replace(
        orch.config, speech_to_text=SpeechToTextConfig(max_mb=1)
    )
    big = client.post("/stt?suffix=wav", content=b"\x00" * (2 * 1024 * 1024))
    assert big.status_code == 413
