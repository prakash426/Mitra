"""Knowledge seeding: section splits, idempotent imports, recall."""

from __future__ import annotations

from mitra.memory.store import MemoryStore
from mitra.seed import seed_project, split_sections


def test_split_sections_per_heading(tmp_path):
    path = tmp_path / "guide.md"
    path.write_text("# Guide\n\n## Memory\n\nSave facts.\n\n## Tasks\n\nTrack work.\n")
    sections = split_sections(path)
    assert len(sections) == 2
    assert all(kind == "note" for kind, _text in sections)
    assert "Memory" in sections[0][1] and "Save facts" in sections[0][1]


def test_split_standing_per_bullet(tmp_path):
    path = tmp_path / "standing-rules.md"
    path.write_text("# Rules\n\n* Cite sources.\n* Keep answers short.\n")
    sections = split_sections(path)
    assert [kind for kind, _text in sections] == ["standing", "standing"]
    assert sections[0][1] == "Cite sources."


def test_seed_is_idempotent(tmp_path):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "guide.md").write_text("# G\n\n## A\n\nAlpha facts.\n")
    store = MemoryStore(tmp_path / "seed.db")
    try:
        first = seed_project(store, "Mitra", knowledge)
        assert first == {"added": 1, "skipped": 0}
        second = seed_project(store, "Mitra", knowledge)
        assert second == {"added": 0, "skipped": 1}
        hits = store.recall("Mitra", "Alpha facts")
        assert len(hits) == 1
        assert hits[0].record.source == "seed:guide.md"
    finally:
        store.close()


def test_seed_respects_project_isolation(tmp_path):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "guide.md").write_text("# G\n\n## A\n\nAlpha facts.\n")
    store = MemoryStore(tmp_path / "seed.db")
    try:
        seed_project(store, "Mitra", knowledge)
        assert store.recall("Other", "Alpha facts") == []
        assert store.list_project("Other") == []
    finally:
        store.close()
