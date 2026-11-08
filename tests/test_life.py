"""Life easy set: due dates, standup, export, page memory, booking, watch."""

from __future__ import annotations

import httpx
import pytest

from mitra.config import GithubConfig
from mitra.memory.store import MemoryStore


def test_task_due_flow(orch):
    orch.handle_message("P", "todo: Ship it.")
    assert "now due 2026-11-10" in orch.handle_message("P", "due 1 2026-11-10").text
    assert orch.memory.task_get(1).due_date == "2026-11-10"
    assert "(due 2026-11-10)" in orch.handle_message("P", "tasks").text
    assert "Cleared" in orch.handle_message("P", "due 1 clear").text
    assert orch.memory.task_get(1).due_date == ""
    assert "due 1 2026-11-10" in orch.handle_message("P", "due 1 tomorrow").text
    assert "no task 9" in orch.handle_message("P", "due 9 2026-11-10").text


def test_task_due_guarded_by_project(orch):
    orch.handle_message("A", "todo: Secret.")
    assert "no task 1" in orch.handle_message("B", "due 1 2026-11-10").text


def test_task_set_due_validation(tmp_path):
    store = MemoryStore(tmp_path / "due.db")
    try:
        task = store.task_add("P", "x")
        with pytest.raises(ValueError):
            store.task_set_due(task.id, "tomorrow")
        assert store.task_set_due(999, "2026-11-10") is None
    finally:
        store.close()


def test_briefing_nudges_overdue(orch):
    orch.handle_message("P", "todo: Old thing.")
    orch.handle_message("P", "due 1 2020-01-01")
    briefing = orch.briefing("P")
    assert any("Overdue" in tip for tip in briefing["suggestions"])


def test_standup_sections(orch):
    orch.handle_message("P", "todo: Ship it.")
    orch.handle_message("P", "todo: Write docs.")
    orch.handle_message("P", "done 1")
    orch.handle_message("P", "start 2")
    orch.handle_message("P", "Save x to staged.txt")
    text = orch.handle_message("P", "standup").text
    assert "Standup for P" in text
    assert "Ship it" in text
    assert "Write docs" in text
    assert "prop-1" in text


def test_export_notes_flow(orch, workdir):
    orch.handle_message("P", "Remember that P uses tabs.")
    orch.handle_message("P", "todo: Ship it.")
    staged = orch.handle_message("P", "export notes")
    assert staged.approval_request is not None
    assert not (workdir / "P-notes.md").exists()
    done = orch.handle_message("P", "approve " + staged.approval_request.proposal_id)
    assert "wrote" in done.text.lower()
    body = (workdir / "P-notes.md").read_text(encoding="utf-8")
    assert "tabs" in body and "Ship it" in body


def test_export_empty_project(orch):
    assert "Nothing to export" in orch.handle_message("Fresh", "export notes").text


class _FakeStream:
    def __init__(self, body=b""):
        self._body = body
        self.status_code = 200
        self.headers = {"content-type": "text/html"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        pass

    def iter_bytes(self, size):
        yield self._body


def _public_resolve(host, port, *args, **kwargs):
    return [(None, None, None, None, ("93.184.216.34", 0))]


def test_remember_page_saves_note(orch, monkeypatch):
    monkeypatch.setattr("mitra.tools.safe_tools.getaddrinfo", _public_resolve)
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: _FakeStream(b"<h1>giant squid</h1>"))
    response = orch.handle_message("P", "remember page https://example.com/squid")
    assert "Saved that page" in response.text
    records = orch.memory.list_project("P")
    assert len(records) == 1
    assert "giant squid" in records[0].text
    assert records[0].source == "https://example.com/squid"


def test_remember_page_failure(orch):
    assert "could not fetch" in orch.handle_message("P", "remember page file:///x").text


def test_addevent_flow(orch, monkeypatch):
    monkeypatch.setattr(
        "mitra.tools.macos_tools.shutil.which", lambda n: "/usr/bin/osascript"
    )
    seen = []

    class Done:
        stdout = ""
        stderr = ""
        returncode = 0

    def fake_run(argv, **kwargs):
        seen.append(argv)
        return Done()

    monkeypatch.setattr("mitra.tools.macos_tools.subprocess.run", fake_run)
    staged = orch.handle_message("P", "addevent Team lunch on 2026-11-10 at 12:30 for 45")
    assert staged.approval_request is not None
    assert staged.approval_request.action == "calendar_add"
    assert seen == []
    done = orch.handle_message("P", "approve " + staged.approval_request.proposal_id)
    assert "Booked" in done.text
    assert len(seen) == 1
    assert "Team lunch" in seen[0][2]
    entries = orch.audit.list_recent("P")
    assert {e.action for e in entries} >= {"calendar_add", "approve"}


def test_addevent_denial_and_help(orch):
    staged = orch.handle_message("P", "addevent Dentist on 2026-11-11 at 09:00")
    pid = staged.approval_request.proposal_id
    denied = orch.handle_message("P", "deny " + pid)
    assert "Nothing was written" in denied.text
    assert "addevent" in orch.handle_message("P", "addevent sometime").text
    assert "YYYY-MM-DD" in orch.handle_message("P", "addevent X on 11-10 at 9").text


def test_addevent_bad_time_denied(orch):
    assert "HH:MM" in orch.handle_message("P", "addevent X on 2026-11-10 at 25:00").text


def test_morning_watch_repos(orch, monkeypatch):
    from dataclasses import replace

    orch.config = replace(orch.config, github=GithubConfig(watch_repos=("octo/demo",)))
    monkeypatch.setattr("mitra.tools.macos_tools.shutil.which", lambda n: None)

    class Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return [{"number": 3, "title": "Fix it", "user": {"login": "octo"}}]

    monkeypatch.setattr(httpx, "get", lambda *a, **k: Resp())
    scan = orch.morning_scan("P")
    assert "Watched repos" in scan["text"]
    assert "octo/demo" in scan["text"]
    assert "Fix it" in scan["text"]


def test_tasks_api_carries_due_date(orch):
    from fastapi.testclient import TestClient

    from mitra.api import build_app

    client = TestClient(build_app(orch))
    orch.handle_message("P", "todo: Ship it.")
    orch.handle_message("P", "due 1 2026-11-10")
    body = client.get("/tasks", params={"project": "P"}).json()
    assert body[0]["due_date"] == "2026-11-10"
