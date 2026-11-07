"""Ollama local inference adapter."""

from __future__ import annotations

import httpx

from .base import Generation, ModelProvider, ProviderError


class OllamaProvider(ModelProvider):
    """Talks to a local Ollama server. No API key needed."""

    name = "ollama"

    def __init__(self, model: str, base_url: str, timeout_seconds: int = 30) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def generate(self, prompt: str, context: str = "") -> Generation:
        full = prompt if not context else prompt + "\n\nEVIDENCE:\n" + context
        try:
            resp = httpx.post(
                self.base_url + "/api/generate",
                json={"model": self.model, "prompt": full, "stream": False},
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
        except httpx.ConnectError as exc:
            raise ProviderError(
                "Cannot reach Ollama at %s. Is Ollama running?" % self.base_url
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "Ollama timed out after %d seconds." % self.timeout_seconds
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise ProviderError("Ollama returned HTTP %d." % status) from exc
        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError("Ollama returned an unreadable reply.") from exc
        text = data.get("response", "") if isinstance(data, dict) else ""
        if not text or not text.strip():
            raise ProviderError("Ollama returned an empty reply.")
        return Generation(text=text.strip(), model=self.model, provider=self.name)
