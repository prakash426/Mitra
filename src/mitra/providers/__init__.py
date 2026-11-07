"""Provider factory. Config chooses, core never branches on provider."""

from __future__ import annotations

from ..config import MitraConfig
from .base import Generation, ModelProvider, ProviderError
from .mock import MockProvider
from .ollama import OllamaProvider
from .openai_compat import OpenAICompatProvider

__all__ = [
    "Generation",
    "ModelProvider",
    "ProviderError",
    "MockProvider",
    "OllamaProvider",
    "OpenAICompatProvider",
    "build_provider",
]


def build_provider(config: MitraConfig) -> ModelProvider:
    """Create the configured provider. Raises ProviderError on unknowns."""
    section = config.provider
    if section.name == "mock":
        return MockProvider(model=section.model)
    if section.name == "ollama":
        return OllamaProvider(
            model=section.model,
            base_url=section.base_url,
            timeout_seconds=section.timeout_seconds,
        )
    if section.name == "openai_compat":
        return OpenAICompatProvider(
            model=section.model,
            base_url=section.base_url,
            api_key=section.api_key,
            timeout_seconds=section.timeout_seconds,
        )
    raise ProviderError("Unknown provider '%s'." % section.name)
