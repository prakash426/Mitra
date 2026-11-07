"""Vector recall: paraphrase matches, hybrid merge, graceful fallback."""

from __future__ import annotations

import httpx
import pytest

from mitra.memory.store import MemoryStore
from mitra.memory.vectors import (
    EmbeddingError,
    OllamaEmbeddingBackend,
    VectorIndex,
    embed_record,
    recall_hybrid,
)


class StubBackend:
    model_name = "stub"

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def embed(self, texts):
        self.calls.append(texts)
        if self.fail:
            raise EmbeddingError("down")
        vectors = []
        for text in texts:
            lowered = text.lower()
            if "tabs" in lowered or "spacing" in lowered:
                vectors.append([1.0, 0.0])
            else:
                vectors.append([0.0, 1.0])
        return vectors


@pytest.fixture()
def pair(tmp_path):
    memory = MemoryStore(tmp_path / "v.db")
    index = VectorIndex(tmp_path / "v.db")
    yield memory, index
    memory.close()
    index.close()


def test_upsert_and_matches_ranking(pair):
    memory, index = pair
    backend = StubBackend()
    first = memory.add("P", "fact", "The team uses tabs.")
    second = memory.add("P", "fact", "Lunch is at noon.")
    embed_record(index, backend, first)
    embed_record(index, backend, second)
    ranked = index.matches("P", "stub", [1.0, 0.0], limit=5)
    assert ranked[0][0] == first.id
    assert ranked[0][1] > ranked[1][1]


def test_matches_respects_project_and_model(pair):
    memory, index = pair
    backend = StubBackend()
    record = memory.add("P", "fact", "The team uses tabs.")
    embed_record(index, backend, record)
    assert record.id > 0
    assert index.matches("Other", "stub", [1.0, 0.0]) == []
    assert index.matches("P", "other-model", [1.0, 0.0]) == []


def test_hybrid_finds_paraphrase(pair):
    memory, index = pair
    backend = StubBackend()
    record = memory.add("P", "fact", "The team uses tabs for indentation.")
    embed_record(index, backend, record)
    hits = recall_hybrid(memory, index, backend, "P", "spacing character choice", limit=5)
    assert len(hits) == 1
    assert hits[0].record.id == record.id


def test_hybrid_falls_back_on_failure(pair):
    memory, index = pair
    memory.add("P", "fact", "The team uses tabs.")
    hits = recall_hybrid(
        memory, index, StubBackend(fail=True), "P", "tabs workflow", limit=5
    )
    assert len(hits) == 1


def test_embed_empty_rejected(pair):
    _memory, index = pair
    with pytest.raises(EmbeddingError):
        index.upsert(1, "stub", [])


def test_ollama_backend_success(monkeypatch):
    class Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"embeddings": [[0.1, 0.2]]}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: Resp())
    vectors = OllamaEmbeddingBackend("m", "http://x").embed(["hi"])
    assert vectors == [[0.1, 0.2]]


def test_ollama_backend_errors(monkeypatch):
    def boom(*args, **kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(EmbeddingError, match="Cannot reach"):
        OllamaEmbeddingBackend("m", "http://x").embed(["hi"])


def test_orchestrator_embeds_on_add(tmp_path):
    from mitra.orchestrator import build_test_orchestrator

    orch = build_test_orchestrator(tmp_path, embedding=StubBackend())
    try:
        orch.handle_message("P", "Remember that P uses tabs.")
        response = orch.handle_message("P", "What did we decide about spacing?")
        assert "tabs" in response.text
    finally:
        orch.close()
