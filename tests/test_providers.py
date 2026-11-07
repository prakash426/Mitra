"""Provider contracts, factory routing, and error mapping."""

from __future__ import annotations

import httpx
import pytest

from mitra.config import MitraConfig, ProviderConfig
from mitra.providers import (
    MockProvider,
    OllamaProvider,
    OpenAICompatProvider,
    ProviderError,
    build_provider,
)
from mitra.providers.base import DRAFT_MARKER


def _config(name: str, **kwargs) -> MitraConfig:
    return MitraConfig(provider=ProviderConfig(name=name, **kwargs))


def test_mock_returns_draft_verbatim():
    provider = MockProvider()
    result = provider.generate("Intro\n" + DRAFT_MARKER + "\nHello there.", "ctx")
    assert result.text == "Hello there."
    assert result.provider == "mock"
    assert len(provider.calls) == 1


def test_mock_without_marker_returns_prompt():
    provider = MockProvider(model="m")
    result = provider.generate("plain")
    assert result.text == "plain"
    assert result.model == "m"


def test_factory_routes_by_name():
    assert isinstance(build_provider(_config("mock")), MockProvider)
    assert isinstance(build_provider(_config("ollama")), OllamaProvider)
    compat = build_provider(_config("openai_compat", api_key="k"))
    assert isinstance(compat, OpenAICompatProvider)
    with pytest.raises(ProviderError):
        build_provider(_config("bogus"))


class _FakeResp:
    def __init__(self, payload=None, status=200):
        self._payload = payload or {}
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "http error",
                request=httpx.Request("POST", "http://x"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self._payload


def test_ollama_success(monkeypatch):
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _FakeResp({"response": "  hi  "})
    )
    result = OllamaProvider("llama", "http://localhost:11434").generate("p")
    assert result.text == "hi"
    assert result.provider == "ollama"


def test_ollama_connection_failure(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(ProviderError, match="Cannot reach Ollama"):
        OllamaProvider("llama", "http://localhost:11434").generate("p")


def test_ollama_empty_reply(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResp({"response": " "}))
    with pytest.raises(ProviderError, match="empty reply"):
        OllamaProvider("llama", "http://localhost:11434").generate("p")


def test_compat_requires_key():
    with pytest.raises(ProviderError, match="MITRA_MODEL_KEY"):
        OpenAICompatProvider("m", "http://x", "").generate("p")


def test_compat_success(monkeypatch):
    payload = {"choices": [{"message": {"content": "  done  "}}]}
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResp(payload))
    result = OpenAICompatProvider("m", "http://x", "key").generate("p", "ctx")
    assert result.text == "done"


def test_compat_rejects_bad_key(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResp({}, status=401))
    with pytest.raises(ProviderError, match="API key"):
        OpenAICompatProvider("m", "http://x", "bad").generate("p")
