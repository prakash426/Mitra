"""Optional vector recall layered beside FTS5.

Vectors catch paraphrase queries that share no keywords with stored facts.
Everything here is best effort: embedding failures fall back to lexical
recall silently, so memory works with or without an embedding model.
"""

from __future__ import annotations

import math
import sqlite3
import struct
from pathlib import Path
from typing import Protocol

import httpx


class EmbeddingError(Exception):
    """An embedding call failed. Callers fall back to lexical recall."""


class EmbeddingBackend(Protocol):
    model_name: str

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed each text. Returns one vector per input, in order."""
        ...  # pragma: no cover


class OllamaEmbeddingBackend:
    """Local embeddings through Ollama. Needs an embedding model pulled."""

    def __init__(self, model: str, base_url: str, timeout_seconds: int = 60) -> None:
        self.model_name = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            resp = httpx.post(
                self.base_url + "/api/embed",
                json={"model": self.model_name, "input": texts},
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
        except httpx.ConnectError as exc:
            raise EmbeddingError("Cannot reach Ollama at %s." % self.base_url) from exc
        except httpx.TimeoutException as exc:
            raise EmbeddingError("Ollama embedding timed out.") from exc
        except httpx.HTTPStatusError as exc:
            raise EmbeddingError(
                "Ollama returned HTTP %d. Is '%s' pulled?"
                % (exc.response.status_code, self.model_name)
            ) from exc
        try:
            data = resp.json()
            vectors = data["embeddings"]
        except (ValueError, KeyError, TypeError) as exc:
            raise EmbeddingError("Ollama returned unreadable embeddings.") from exc
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise EmbeddingError("Ollama returned the wrong embedding count.")
        return [list(map(float, vec)) for vec in vectors]


def _pack(vector: list[float]) -> bytes:
    return struct.pack("<%df" % len(vector), *vector)


def _unpack(blob: bytes, dims: int) -> list[float]:
    if len(blob) != dims * 4:
        raise EmbeddingError("Stored vector has the wrong size.")
    return list(struct.unpack("<%df" % dims, blob))


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    if norm == 0:
        return 0.0
    return dot / norm


class VectorIndex:
    """Stored record vectors in their own table inside the Mitra database."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_embeddings (
                record_id INTEGER PRIMARY KEY,
                model TEXT NOT NULL,
                dims INTEGER NOT NULL,
                vector BLOB NOT NULL
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def upsert(self, record_id: int, model: str, vector: list[float]) -> None:
        if not vector:
            raise EmbeddingError("Cannot store an empty vector.")
        self._conn.execute(
            "INSERT INTO memory_embeddings (record_id, model, dims, vector)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(record_id) DO UPDATE SET model = ?, dims = ?, vector = ?",
            (
                record_id,
                model,
                len(vector),
                _pack(vector),
                model,
                len(vector),
                _pack(vector),
            ),
        )
        self._conn.commit()

    def matches(
        self, project: str, model: str, vector: list[float], limit: int = 5
    ) -> list[tuple[int, float]]:
        """Active record ids for one project, ranked by cosine similarity."""
        rows = self._conn.execute(
            "SELECT e.record_id AS rid, e.dims AS dims, e.vector AS vec"
            " FROM memory_embeddings e JOIN memories m ON m.id = e.record_id"
            " WHERE m.project = ? AND m.superseded_by IS NULL AND e.model = ?",
            (project.strip(), model),
        ).fetchall()
        scored = []
        for row in rows:
            try:
                candidate = _unpack(row["vec"], row["dims"])
            except EmbeddingError:
                continue
            if len(candidate) != len(vector):
                continue
            scored.append((row["rid"], _cosine(vector, candidate)))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[: max(1, limit)]


def embed_record(index: VectorIndex, backend: EmbeddingBackend, record) -> None:
    """Store one record vector. Raises EmbeddingError on failure."""
    vectors = backend.embed([record.text])
    if not vectors or not vectors[0]:
        raise EmbeddingError("Backend returned no vector.")
    index.upsert(record.id, backend.model_name, vectors[0])


def recall_hybrid(
    memory, index: VectorIndex, backend: EmbeddingBackend, project: str, query: str, limit: int = 5
) -> list:
    """Lexical hits first, then vector only extras. Falls back to lexical
    whenever embedding fails, so recall never depends on the model."""
    from .store import MemoryHit

    hits = memory.recall(project, query, limit=limit)
    try:
        vectors = backend.embed([query])
    except EmbeddingError:
        return hits
    if not vectors or not vectors[0]:
        return hits
    seen = {hit.record.id for hit in hits}
    try:
        ranked = index.matches(project, backend.model_name, vectors[0], limit=limit)
    except EmbeddingError:
        return hits
    for record_id, _score in ranked:
        if record_id in seen:
            continue
        record = memory.get(record_id)
        if record is None or record.superseded_by is not None:
            continue
        hits.append(MemoryHit(record=record, snippet=record.text[:160]))
        seen.add(record_id)
        if len(hits) >= max(1, limit):
            break
    return hits
