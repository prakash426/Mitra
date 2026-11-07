"""Deterministic mock provider for tests and offline demos."""

from __future__ import annotations

from .base import DRAFT_MARKER, Generation, ModelProvider


class MockProvider(ModelProvider):
    """Returns the orchestrator draft verbatim. Records calls for tests."""

    name = "mock"

    def __init__(self, model: str = "mock-echo") -> None:
        self.model = model
        self.calls: list[tuple[str, str]] = []

    def generate(self, prompt: str, context: str = "") -> Generation:
        self.calls.append((prompt, context))
        if DRAFT_MARKER in prompt:
            text = prompt.split(DRAFT_MARKER, 1)[1].strip()
        else:
            text = prompt
        return Generation(text=text, model=self.model, provider=self.name)
