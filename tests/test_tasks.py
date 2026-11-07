"""Tracked tasks with state, plus standing instructions at the store level."""

from __future__ import annotations

import pytest

from mitra.memory.store import MemoryStore


@pytest.fixture()
def store(tmp_path):
    instance = MemoryStore(tmp_path / "tasks.db")
    yield instance
    instance.close()


def test_task_crud(store):
    task = store.task_add("P", "Ship M2.")
    assert task.state == "open"
    assert store.task_get(task.id).title == "Ship M2."
    assert store.task_set_state(task.id, "doing").state == "doing"
    assert store.task_set_state(task.id, "done").state == "done"
    assert store.task_get(999) is None
    assert store.task_set_state(999, "done") is None


def test_task_list_and_state_filter(store):
    store.task_add("P", "One.")
    two = store.task_add("P", "Two.")
    store.task_add("Q", "Other.")
    store.task_set_state(two.id, "done")
    assert len(store.task_list("P")) == 2
    assert [t.title for t in store.task_list("P", state="done")] == ["Two."]
    assert len(store.task_list("Q")) == 1
    with pytest.raises(ValueError):
        store.task_list("P", state="bogus")


def test_task_validation(store):
    with pytest.raises(ValueError):
        store.task_add("P", "  ")
    with pytest.raises(ValueError):
        store.task_add("  ", "x")
    with pytest.raises(ValueError):
        store.task_set_state(1, "bogus")


def test_standing_kind_stores(store):
    record = store.add("P", "standing", "Always cite sources.")
    assert record.kind == "standing"
    hits = store.recall("P", "cite sources")
    assert len(hits) == 1
