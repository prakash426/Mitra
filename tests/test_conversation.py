"""Conversation memory: short term context, followups, multi fact topics."""

from __future__ import annotations


def test_two_facts_same_topic_both_recalled(orch):
    orch.handle_message("P", "Remember that Atlas uses Postgres.")
    orch.handle_message("P", "Remember that Atlas deploys on Fridays.")
    response = orch.handle_message("P", "What did we decide about Atlas?")
    assert "Postgres" in response.text
    assert "Fridays" in response.text
    assert len(response.evidence) >= 2


def test_multi_fact_single_message_saves_each(orch):
    response = orch.handle_message(
        "P", "Remember that Atlas uses Postgres. Remember that Atlas deploys on Fridays."
    )
    assert "2 facts" in response.text or "Saved 2" in response.text
    records = orch.memory.list_project("P")
    assert len(records) == 2


def test_update_followup_uses_pending_target(orch, workdir):
    (workdir / "doc.txt").write_text("old", encoding="utf-8")
    first = orch.handle_message("P", "Update doc.txt")
    assert "exact new content" in first.text
    second = orch.handle_message("P", "brand new text from followup")
    assert second.approval_request is not None
    assert "approve" in second.text.lower()
    assert "exact new content" not in second.text


def test_read_followup_uses_pending_question(orch):
    first = orch.handle_message("P", "Read")
    assert "Which file" in first.text
    second = orch.handle_message("P", "README.md")
    assert "local first workflow" in second.text or "I read" in second.text


def test_recent_turns_feed_next_prompt(orch):
    orch.handle_message("P", "Remember that Atlas uses Postgres.")
    orch.handle_message("P", "What did we decide about Atlas?")
    turns = orch.memory.recent_turns("P", limit=6)
    assert len(turns) >= 4
    assert turns[-2]["role"] == "user"
    prompt, _ctx = orch.provider.calls[-1]
    assert "Atlas" in prompt


def test_briefing_recalls_last_session(orch):
    orch.handle_message("P", "Remember that Atlas uses Postgres.")
    orch.handle_message("P", "todo: Ship the Atlas demo.")
    briefing = orch.briefing("P")
    assert "Atlas" in briefing["greeting"] or "Atlas" in briefing["recap"]
    assert briefing["tasks_open"] >= 1
    assert briefing["suggestions"]


def test_howto_gives_exact_commands(orch):
    assert "todo:" in orch.handle_message("P", "How do I track a task?").text
    assert "github read" in orch.handle_message("P", "How can I use github?").text
    assert "save <content>" in orch.handle_message("P", "How to save a file?").text
    fallback = orch.handle_message("P", "How do I?")
    assert "help" in fallback.text


def test_morning_scan_combines_sources(orch, monkeypatch):
    monkeypatch.setattr(
        "mitra.tools.macos_tools.shutil.which", lambda n: "/usr/bin/osascript"
    )

    class Done:
        stdout = "Standup | Monday\n"
        stderr = ""
        returncode = 0

    monkeypatch.setattr(
        "mitra.tools.macos_tools.subprocess.run", lambda *a, **k: Done()
    )
    orch.handle_message("P", "todo: Ship the demo.")
    scan = orch.morning_scan("P")
    assert "Standup" in scan["text"]
    assert "Ship the demo" in scan["text"]
    assert "First move" in scan["text"]
    assert scan["tasks_open"] == 1


def test_morning_scan_degrades_gracefully(orch, monkeypatch):
    monkeypatch.setattr("mitra.tools.macos_tools.shutil.which", lambda n: None)
    scan = orch.morning_scan("P")
    assert "unavailable" in scan["text"]
    assert "Open work" in scan["text"]
