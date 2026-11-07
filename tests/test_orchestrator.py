"""End to end orchestration with the mock provider. Covers scenarios A to D."""

from __future__ import annotations

import json

from mitra.orchestrator import (
    OPEN_CHAT_INSTRUCTIONS,
    RENDER_INSTRUCTIONS,
    build_test_orchestrator,
)
from mitra.providers.base import Generation, ModelProvider, ProviderError


def test_scenario_a_remember_and_recall(orch):
    first = orch.handle_message(
        "CancerLens", "Remember that CancerLens uses a local first workflow."
    )
    assert "Noted" in first.text
    assert first.actions[0].startswith("memory_add:")

    second = orch.handle_message("CancerLens", "What did we decide about CancerLens?")
    assert "local first workflow" in second.text
    assert "source" in second.text
    assert len(second.evidence) == 1
    assert second.evidence[0].source == "user"


def test_recall_empty_project(orch):
    response = orch.handle_message("Fresh", "What did we decide about Fresh?")
    assert "nothing saved" in response.text


def test_correction_overrides(orch):
    orch.handle_message("P", "Remember that CancerLens uses waterfall.")
    orch.handle_message("P", "Correction: CancerLens uses local first.")
    response = orch.handle_message("P", "What did we decide about CancerLens?")
    assert "local first" in response.text
    assert "waterfall" not in response.text


def test_scenario_b_list_project_memories(orch):
    orch.handle_message("P", "Remember that CancerLens uses local first.")
    response = orch.handle_message("P", "Show me what is saved for my project.")
    assert "CancerLens" in response.text
    assert "source" in response.text
    assert len(response.evidence) == 1


def test_scenario_c_read_readme(orch, workdir):
    response = orch.handle_message(
        "P", "Read the README in my project directory and summarize it."
    )
    assert "local first workflow" in response.text
    assert str(workdir) in response.text
    assert response.actions == ["read_file:ok"]


def test_read_missing_file_graceful(orch):
    response = orch.handle_message("P", "Read /definitely/not/here.txt")
    assert "could not read" in response.text


def test_scenario_d_stage_approve_write(orch, workdir):
    target = workdir / "notes.txt"
    staged = orch.handle_message("P", "Save hello mitra to notes.txt")
    assert staged.approval_request is not None
    assert "approve" in staged.text
    assert not target.exists()

    pid = staged.approval_request.proposal_id
    done = orch.handle_message("P", "approve " + pid)
    assert "wrote" in done.text.lower()
    assert target.read_text(encoding="utf-8") == "hello mitra"

    entries = orch.audit.list_recent("P")
    actions = {e.action for e in entries}
    assert {"propose_write_file", "approve", "write_file"} <= actions


def test_denial_blocks_write(orch, workdir):
    target = workdir / "blocked.txt"
    staged = orch.handle_message("P", "Save secret data to blocked.txt")
    pid = staged.approval_request.proposal_id
    denied = orch.handle_message("P", "deny " + pid)
    assert "Nothing was written" in denied.text
    assert not target.exists()
    retry = orch.handle_message("P", "approve " + pid)
    assert "already decided" in retry.text


def test_approve_unknown_proposal(orch):
    response = orch.handle_message("P", "approve prop-99")
    assert "no proposal" in response.text


def test_update_without_content_reads_and_asks(orch):
    response = orch.handle_message("P", "Update README.md")
    assert "exact new content" in response.text
    assert "never invent" in response.text


def test_update_with_content_stages(orch, workdir):
    (workdir / "doc.txt").write_text("old", encoding="utf-8")
    response = orch.handle_message("P", "Update doc.txt to brand new text")
    assert response.approval_request is not None
    assert (workdir / "doc.txt").read_text(encoding="utf-8") == "old"


def test_update_the_readme_sentence_form(orch):
    response = orch.handle_message("P", "Update the README.")
    assert "exact new content" in response.text
    assert "README.md" in response.text


def test_save_ignores_sentence_dot(orch, workdir):
    staged = orch.handle_message("P", "Save dotted to dot.txt.")
    assert staged.approval_request is not None
    orch.handle_message("P", "approve " + staged.approval_request.proposal_id)
    assert (workdir / "dot.txt").read_text(encoding="utf-8") == "dotted"
    assert not (workdir / "dot.txt.").exists()


def test_pending_lists_open_proposals(orch):
    assert "No pending" in orch.handle_message("P", "pending").text
    orch.handle_message("P", "Save x to a.txt")
    listing = orch.handle_message("P", "pending")
    assert "prop-1" in listing.text


def test_provider_failure_stays_graceful(tmp_path):
    class Failing:
        name = "failing"

        def generate(self, prompt, context=""):
            raise ProviderError("model is down")

    orch = build_test_orchestrator(tmp_path)
    orch.provider = Failing()
    try:
        orch.handle_message("P", "Remember that CancerLens uses local first.")
        response = orch.handle_message("P", "What did we decide about CancerLens?")
        assert "could not reach" in response.text
        assert "local first" in response.text
        failures = [e for e in orch.audit.list_recent("P") if e.action == "model_generate"]
        assert len(failures) == 2
    finally:
        orch.close()


def test_empty_message_and_help(orch):
    assert "Say something" in orch.handle_message("P", "   ").text
    assert "remember" in orch.handle_message("P", "help").text.lower()


def test_provider_called_on_chat(orch):
    orch.handle_message("P", "hello there")
    assert len(orch.provider.calls) >= 1


def test_memory_isolated_between_projects(orch):
    orch.handle_message("A", "Remember that Project A uses tabs.")
    response = orch.handle_message("B", "What did we decide about tabs?")
    assert "nothing saved" in response.text


def test_tasks_flow(orch):
    added = orch.handle_message("P", "todo: Ship milestone two.")
    assert "Task 1 tracked" in added.text
    orch.handle_message("P", "remember to water the plants.")
    listing = orch.handle_message("P", "tasks")
    assert "Ship milestone two" in listing.text
    assert "water the plants" in listing.text
    started = orch.handle_message("P", "start 1")
    assert "in progress" in started.text
    done = orch.handle_message("P", "done 1")
    assert "marked done" in done.text
    assert orch.memory.task_get(1).state == "done"


def test_standing_surfaces_in_chat(orch):
    orch.handle_message("P", "standing: Always answer with sources.")
    response = orch.handle_message("P", "hello there")
    assert "Standing instructions" in response.text
    assert "Always answer with sources." in response.text
    assert any(e.source == "user" for e in response.evidence)


def test_list_search_intents(orch):
    listing = orch.handle_message("P", "list")
    assert "README.md" in listing.text
    found = orch.handle_message("P", "search *.md")
    assert "README.md" in found.text
    missing = orch.handle_message("P", "search *.zzz")
    assert "No matches" in missing.text


def test_fetch_rejects_bad_scheme(orch):
    assert "could not fetch" in orch.handle_message("P", "fetch file:///x").text


def test_run_shell_disabled_default(orch):
    response = orch.handle_message("P", "run echo hello")
    assert "could not stage" in response.text
    assert "disabled" in response.text


def test_run_shell_approve_flow(tmp_path):
    from mitra.config import ShellConfig

    orch = build_test_orchestrator(
        tmp_path, shell=ShellConfig(enabled=True, allowlist=("echo",))
    )
    try:
        staged = orch.handle_message("P", "run echo shell-ok")
        assert staged.approval_request is not None
        assert staged.approval_request.action == "run_shell"
        done = orch.handle_message("P", "approve " + staged.approval_request.proposal_id)
        assert "shell-ok" in done.text and "exit 0" in done.text
    finally:
        orch.close()


def test_decide_shared_path(orch, workdir):
    staged = orch.handle_message("P", "Save via decide to decide.txt")
    pid = staged.approval_request.proposal_id
    ok, message, tag = orch.decide("P", pid, True)
    assert ok and "wrote" in message.lower()
    assert tag == "write_executed:" + pid
    assert (workdir / "decide.txt").exists()
    ok2, message2, _tag2 = orch.decide("P", pid, False)
    assert not ok2 and "already decided" in message2


class ReasoningStub(ModelProvider):
    name = "stub"
    can_reason = True

    def __init__(self, reply="Stub answer."):
        self.reply = reply
        self.calls = []

    def generate(self, prompt, context=""):
        self.calls.append((prompt, context))
        return Generation(text=self.reply, model="stub", provider="stub")


def test_open_answer_uses_model_text(tmp_path):
    orch = build_test_orchestrator(tmp_path)
    stub = ReasoningStub()
    orch.provider = stub
    try:
        orch.handle_message("P", "standing: Keep answers short.")
        orch.handle_message("P", "Remember that P uses tabs.")
        response = orch.handle_message("P", "Why do we use tabs?")
        assert response.text == "Stub answer."
        prompt = stub.calls[-1][0]
        assert "Why do we use tabs?" in prompt
        assert "Keep answers short." in prompt
        assert "P uses tabs." in prompt
    finally:
        orch.close()


def test_open_answer_empty_falls_back_to_draft(tmp_path):
    orch = build_test_orchestrator(tmp_path)
    orch.provider = ReasoningStub(reply="   ")
    try:
        response = orch.handle_message("P", "hello there")
        assert "I am Mitra" in response.text
    finally:
        orch.close()


def test_open_answer_error_stays_graceful(tmp_path):
    class FailingReasoning(ReasoningStub):
        def generate(self, prompt, context=""):
            raise ProviderError("down")

    orch = build_test_orchestrator(tmp_path)
    orch.provider = FailingReasoning()
    try:
        response = orch.handle_message("P", "hello there")
        assert "could not reach" in response.text
        assert "I am Mitra" in response.text
    finally:
        orch.close()


def test_recall_stays_grounded_with_reasoning_provider(tmp_path):
    orch = build_test_orchestrator(tmp_path)
    stub = ReasoningStub()
    orch.provider = stub
    try:
        orch.handle_message("P", "Remember that P uses tabs.")
        orch.handle_message("P", "What did we decide about tabs?")
        assert "P uses tabs." in stub.calls[-1][0]
    finally:
        orch.close()


def test_prompts_forbid_greetings_and_preambles():
    for text in (OPEN_CHAT_INSTRUCTIONS, RENDER_INSTRUCTIONS):
        lowered = text.lower()
        assert "greet" in lowered
        assert "stock" in lowered
        assert "preamble" in lowered or "announcement" in lowered


def test_import_json_flow(orch, workdir):
    (workdir / "lib.json").write_text(
        json.dumps([{"kind": "fact", "text": "Imported fact."}, "Plain string note."]),
        encoding="utf-8",
    )
    staged = orch.handle_message("P", "import memories from lib.json")
    assert staged.approval_request is not None
    assert staged.approval_request.action == "memory_import"
    assert orch.memory.list_project("P") == []
    done = orch.handle_message("P", "approve " + staged.approval_request.proposal_id)
    assert "imported 2 records" in done.text
    records = orch.memory.list_project("P")
    assert len(records) == 2
    assert all(r.source == "import:lib.json" for r in records)


def test_import_text_and_errors(orch, workdir):
    (workdir / "notes.txt").write_text("- first note\n\n- second note\n", encoding="utf-8")
    staged = orch.handle_message("P", "import from notes.txt")
    assert "2 records" in staged.text
    (workdir / "bad.json").write_text("{nope", encoding="utf-8")
    response = orch.handle_message("P", "import from bad.json")
    assert "could not parse" in response.text
    missing = orch.handle_message("P", "import from missing.txt")
    assert "could not read" in missing.text


def test_github_read_intent(orch, monkeypatch):
    import base64
    import httpx

    class Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "content": base64.b64encode(b"readme text").decode(),
                "encoding": "base64",
            }

    monkeypatch.setattr(httpx, "get", lambda *a, **k: Resp())
    response = orch.handle_message("P", "github read octo/demo README.md")
    assert "readme text" in response.text


def test_github_open_pr_flow(orch, monkeypatch):
    import httpx

    posts = []

    def fake_post(url, **kwargs):
        posts.append((url, kwargs))

        class Resp:
            status_code = 201

            def raise_for_status(self):
                pass

            def json(self):
                return {"number": 9, "html_url": "http://x/pr/9"}

        return Resp()

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setenv("MITRA_GITHUB_TOKEN", "tok")
    staged = orch.handle_message(
        "P", "github open pr octo/demo from feat to main titled Cool thing"
    )
    assert staged.approval_request is not None
    assert posts == []
    done = orch.handle_message("P", "approve " + staged.approval_request.proposal_id)
    assert "Opened PR #9" in done.text
    assert len(posts) == 1
    assert posts[0][0].endswith("/repos/octo/demo/pulls")


def test_calendar_intent(orch, monkeypatch):
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
    response = orch.handle_message("P", "what is on my calendar?")
    assert "Standup" in response.text


def test_mail_intent(orch, monkeypatch):
    monkeypatch.setattr(
        "mitra.tools.macos_tools.shutil.which", lambda n: "/usr/bin/osascript"
    )

    class Done:
        stdout = "Hello | a@b.c\n"
        stderr = ""
        returncode = 0

    monkeypatch.setattr(
        "mitra.tools.macos_tools.subprocess.run", lambda *a, **k: Done()
    )
    assert "Hello" in orch.handle_message("P", "check my inbox").text
