"""Model provider contract. Core logic depends only on this file."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

DRAFT_MARKER = "DRAFT:"


@dataclass(frozen=True)
class Generation:
    text: str
    model: str
    provider: str


class ProviderError(Exception):
    """A model call failed. The message is safe to show the user."""


class ModelProvider(ABC):
    """One method contract. prompt carries instructions plus draft."""

    name: str = "base"

    @abstractmethod
    def generate(self, prompt: str, context: str = "") -> Generation:
        """Render a conversational reply for prompt, grounded in context."""
