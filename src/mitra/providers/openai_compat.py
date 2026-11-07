"""Generic OpenAI compatible adapter. Also serves Meta style endpoints."""

from __future__ import annotations

import httpx

from .base import Generation, ModelProvider, ProviderError


class OpenAICompatProvider(ModelProvider):
    """Any OpenAI style chat completions endpoint via base URL plus key."""

    name = "openai_compat"

    def __init__(
        self, model: str, base_url: str, api_key: str, timeout_seconds: int = 30
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def generate(self, prompt: str, context: str = "") -> Generation:
        if not self.api_key:
            raise ProviderError(
                "Missing MITRA_MODEL_KEY. Set it in the environment to use "
                "the OpenAI compatible provider."
            )
        content = prompt if not context else prompt + "\n\nEVIDENCE:\n" + context
        try:
            resp = httpx.post(
                self.base_url + "/chat/completions",
                headers={"Authorization": "Bearer " + self.api_key},
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": content}],
                },
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
        except httpx.ConnectError as exc:
            raise ProviderError(
                "Cannot reach the model endpoint at %s." % self.base_url
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "The model timed out after %d seconds." % self.timeout_seconds
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (401, 403):
                raise ProviderError(
                    "The model endpoint rejected the API key (HTTP %d)." % status
                ) from exc
            raise ProviderError("The model endpoint returned HTTP %d." % status) from exc
        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("The model returned an unreadable reply.") from exc
        if not text or not text.strip():
            raise ProviderError("The model returned an empty reply.")
        return Generation(text=text.strip(), model=self.model, provider=self.name)
