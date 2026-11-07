"""Memory storage, retrieval, isolation, and correction override."""

from __future__ import annotations

import pytest

from mitra.memory.store import MemoryStore


@pytest.fixture()
def store(tmp_path):
    instance = MemoryStore(tmp_path / "mem.db")
    yield instance
    instance.close()


def test_add_and_get_roundtrip(store):
    record = store.add("CancerLens", "fact", "Uses a local first workflow.")
    fetched = store.get(record.id)
    assert fetched is not None
    assert fetched.project == "CancerLens"
    assert fetched.kind == "fact"
    assert fetched.text == "Uses a local first workflow."
    assert fetched.source == "user"
    assert fetched.created_at
    assert fetched.superseded_by is None


def test_recall_finds_keyword_with_snippet(store):
    store.add("CancerLens", "fact", "CancerLens uses a local first workflow.")
    hits = store.recall("CancerLens", "What did we decide about CancerLens?")
    assert len(hits) == 1
    assert "local first" in hits[0].record.text
    assert hits[0].snippet


def test_project_isolation(store):
    store.add("CancerLens", "fact", "CancerLens uses a local first workflow.")
    hits = store.recall("OtherProject", "CancerLens workflow")
    assert hits == []
    assert store.list_project("OtherProject") == []


def test_list_project_returns_active_only(store):
    first = store.add("P", "fact", "CancerLens uses waterfall.")
    store.add_correction("P", "CancerLens uses local first.", topic="CancerLens")
    records = store.list_project("P")
    assert [r.text for r in records] == ["CancerLens uses local first."]
    assert store.get(first.id).superseded_by is not None


def test_correction_overrides_in_recall(store):
    store.add("P", "fact", "CancerLens uses waterfall.")
    store.add_correction("P", "CancerLens uses local first.", topic="CancerLens")
    hits = store.recall("P", "CancerLens workflow")
    assert len(hits) == 1
    assert "local first" in hits[0].record.text


def test_update_and_delete(store):
    record = store.add("P", "note", "Old text.")
    updated = store.update_text(record.id, "New text.")
    assert updated is not None and updated.text == "New text."
    assert store.delete(record.id) is True
    assert store.get(record.id) is None
    assert store.delete(record.id) is False


def test_invalid_inputs_rejected(store):
    with pytest.raises(ValueError):
        store.add("P", "bogus", "text")
    with pytest.raises(ValueError):
        store.add("P", "fact", "   ")
    with pytest.raises(ValueError):
        store.add("  ", "fact", "text")
    with pytest.raises(ValueError):
        store.update_text(1, "  ")
