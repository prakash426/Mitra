"""Model driven planning, checkpoints, and consented synthesis."""

from __future__ import annotations

import json

import pytest

from mitra.orchestrator import build_test_orchestrator
from mitra.planning import (
    PlanError,
    PlanStep,
    PlanStore,
    looks_like_plan,
    request_plan,
)
from mitra.providers.base import Generation, ModelProvider
from mitra.tools.base import ToolSpec


class ScriptedProvider(ModelProvider):
    name = "scripted"
    can_reason = True

    def __init__(self, responses):
        self._responses = list(responses)

    def generate(self, prompt, context=""):
        if self._responses:
            text = self._responses.pop(0)
        elif "DRAFT:" in prompt:
            text = prompt.split("DRAFT:", 1)[1].strip()
        else:
            text = prompt[-500:]
        return Generation(text=text, model="s", provider="scripted")


def _specs():
    return [
        ToolSpec(name="read_file", description="Read a file. Args: {path}.", read_only=True),
        ToolSpec(name="list_dir", description="List a dir. Args: {path}.", read_only=True),
        ToolSpec(
            name="propose_write_file",
            description="Stage a write. Args: {path, content}.",
            read_only=False,
        ),
    ]


def _orch(workdir, responses):
    orch = build_test_orchestrator(workdir)
    orch.provider = ScriptedProvider(responses)
    return orch


def test_looks_like_plan_triggers():
    assert looks_like_plan("do X and then do Y")
    assert looks_like_plan("plan: summarize notes")
    assert looks_like_plan("First this, then that")
    assert not looks_like_plan("what did we decide?")
    assert not looks_like_plan("read the README")


def test_request_plan_happy_path():
    provider = ScriptedProvider(
        ['{"steps": [{"tool": "read_file", "args": {"path": "a"}}]}']
    )
    steps = request_plan(provider, "read a", _specs())
    assert [s.tool for s in steps] == ["read_file"]
    assert steps[0].args == {"path": "a"}


def test_request_plan_accepts_fences():
    provider = ScriptedProvider(
        ['```json\n{"steps": [{"tool": "list_dir", "args": {}}]}\n```']
    )
    assert request_plan(provider, "list", _specs())[0].tool == "list_dir"


@pytest.mark.parametrize(
    "text",
    [
        "not json at all",
        '{"steps": []}',
        '{"steps": [{"tool": "nope", "args": {}}]}',
        '{"steps": [{"tool": "read_file", "args": [1]}]}',
        '{"nope": 1}',
    ],
)
def test_request_plan_rejects_bad_plans(text):
    with pytest.raises(PlanError):
        request_plan(ScriptedProvider([text]), "x", _specs())


def test_request_plan_caps_steps():
    many = {"steps": [{"tool": "read_file", "args": {}}] * 9}
    with pytest.raises(PlanError):
        request_plan(ScriptedProvider([json.dumps(many)]), "x", _specs())


def test_plan_store_roundtrip(tmp_path):
    store = PlanStore(tmp_path / "plans.db")
    try:
        record = store.create(
            "P", "goal", [PlanStep(tool="read_file", args={"path": "a"})]
        )
        assert record.id == "plan-1" and record.status == "active"
        assert store.get("plan-1").goal == "goal"
        assert store.get("plan-9") is None
        assert store.get("nope") is None
        assert len(store.active("P")) == 1
        assert store.active("Other") == []
        done = (PlanStep(tool="read_file", args={"path": "a"}, done=True, result="r"),)
        store.save_steps("plan-1", done)
        assert store.get("plan-1").steps[0].done
        store.finish("plan-1", "done")
        assert store.get("plan-1").status == "done"
        assert store.active("P") == []
    finally:
        store.close()


def test_read_only_plan_runs_to_done(workdir):
    plan = json.dumps(
        {
            "steps": [
                {"tool": "list_dir", "args": {"path": "."}},
                {"tool": "read_file", "args": {"path": "README.md"}},
            ]
        }
    )
    orch = _orch(workdir, [plan, "finished summary"])
    try:
        response = orch.handle_message("P", "plan: notes and then readme")
        assert "plan_done:plan-1" in response.actions
        assert len(response.evidence) == 2
        assert response.approval_request is None
        assert orch.planner.get("plan-1").status == "done"
    finally:
        orch.close()


def test_mutation_step_pauses_and_resumes(workdir):
    plan = json.dumps(
        {
            "steps": [
                {"tool": "read_file", "args": {"path": "README.md"}},
                {
                    "tool": "propose_write_file",
                    "args": {"path": "OUT.md", "content": "hello"},
                },
            ]
        }
    )
    orch = _orch(workdir, [plan, "paused draft", "done draft"])
    try:
        first = orch.handle_message("P", "plan: review and then save an update")
        assert "plan_paused:plan-1" in first.actions
        assert first.approval_request is not None
        prop = first.approval_request.proposal_id
        assert not (workdir / "OUT.md").exists()
        # Continuing before approval must not stage a second proposal.
        again = orch.handle_message("P", "continue plan-1")
        assert "plan_paused:plan-1" in again.actions
        assert len(orch.gate.pending("P")) == 1
        orch.handle_message("P", "approve %s" % prop)
        done = orch.handle_message("P", "continue plan-1")
        assert "plan_done:plan-1" in done.actions
        assert (workdir / "OUT.md").read_text() == "hello"
    finally:
        orch.close()


def test_denied_mutation_abandons_plan(workdir):
    plan = json.dumps(
        {"steps": [{"tool": "propose_write_file", "args": {"path": "X.md", "content": "x"}}]}
    )
    orch = _orch(workdir, [plan, "paused draft"])
    try:
        first = orch.handle_message("P", "plan: save an update")
        prop = first.approval_request.proposal_id
        orch.handle_message("P", "deny %s" % prop)
        done = orch.handle_message("P", "continue plan-1")
        assert "plan_abandoned:plan-1" in done.actions
        assert "was denied" in done.text
        assert not (workdir / "X.md").exists()
    finally:
        orch.close()


def test_bad_plan_falls_back_to_chat(workdir):
    orch = _orch(workdir, ["not json", "chat reply"])
    try:
        response = orch.handle_message("P", "plan: something odd")
        assert response.actions == ["chat:help"]
        assert response.text == "chat reply"
        assert orch.planner.active("P") == []
    finally:
        orch.close()


def test_planning_off_or_mock_stays_chat(workdir):
    from dataclasses import replace

    from mitra.config import OrchestratorConfig
    from mitra.providers.mock import MockProvider

    orch = _orch(workdir, [])
    try:
        orch.provider = MockProvider()
        response = orch.handle_message("P", "do X and then do Y")
        assert orch.planner.active("P") == []
        assert "plan_done" not in " ".join(response.actions)
    finally:
        orch.close()
    orch2 = _orch(workdir, [])
    try:
        orch2.config = replace(
            orch2.config, orchestrator=OrchestratorConfig(planning=False)
        )
        response = orch2.handle_message("P", "do X and then do Y")
        assert orch2.planner.active("P") == []
        assert "plan_done" not in " ".join(response.actions)
    finally:
        orch2.close()


def test_plans_show_while_paused_and_abandon_works(workdir):
    plan = json.dumps(
        {"steps": [{"tool": "propose_write_file", "args": {"path": "Y.md", "content": "y"}}]}
    )
    orch = _orch(workdir, [plan, "paused draft"])
    try:
        orch.handle_message("P", "plan: save something")
        listing = orch.handle_message("P", "plans").text
        assert "plan-1" in listing and "0 of 1" in listing
        assert "plan-1" in orch.handle_message("P", "pending").text
        dropped = orch.handle_message("P", "abandon plan-1")
        assert "Abandoned plan-1" in dropped.text
        assert orch.planner.get("plan-1").status == "abandoned"
        assert "no active plan-1" in orch.handle_message("P", "continue plan-1").text
    finally:
        orch.close()


def test_plan_is_project_scoped(workdir):
    plan = json.dumps({"steps": [{"tool": "read_file", "args": {"path": "README.md"}}]})
    orch = _orch(workdir, [plan, "done"])
    try:
        orch.handle_message("P", "plan: quick look")
        other = orch.handle_message("Other", "continue plan-1")
        assert "belongs to project P" in other.text or "no active" in other.text
    finally:
        orch.close()


def test_synthesis_needs_consent(workdir):
    orch = build_test_orchestrator(workdir)
    try:
        orch.handle_message("CancerLens", "remember that deploy needs two approvals")
        orch.handle_message("Demo", "remember that deploy is fully automatic")
        staged = orch.handle_message("P", "compare deploy steps across CancerLens and Demo")
        assert staged.approval_request.action == "cross_project"
        prop = staged.approval_request.proposal_id
        # Nothing leaks before approval.
        assert "automatic" not in staged.text
        done = orch.handle_message("P", "approve %s" % prop)
        assert "synthesis_executed:" + prop in done.actions
        assert "two approvals" in done.text and "automatic" in done.text
        assert "CancerLens" in done.text and "Demo" in done.text
    finally:
        orch.close()


def test_synthesis_deny_and_validation(workdir):
    orch = build_test_orchestrator(workdir)
    try:
        staged = orch.handle_message("P", "compare X across A and B")
        prop = staged.approval_request.proposal_id
        denied = orch.handle_message("P", "deny %s" % prop)
        assert "Discarded proposal %s" % prop in denied.text
        bad1 = orch.handle_message("P", "compare X across Solo")
        assert "synthesis:error" in bad1.actions
        bad2 = orch.handle_message("P", "compare X across A and A")
        assert "synthesis:error" in bad2.actions
        bad3 = orch.handle_message("P", "compare X across A, B, C, D, E")
        assert "synthesis:error" in bad3.actions
    finally:
        orch.close()


def test_proposal_status_visible():
    from mitra.security.policy import ApprovalGate

    gate = ApprovalGate(":memory:")
    try:
        proposal = gate.stage("write_file", "t", "{}", "P")
        assert proposal.status == "pending"
        assert gate.get(proposal.id).status == "pending"
        gate.approve(proposal.id)
        assert gate.get(proposal.id).status == "approved"
    finally:
        gate.close()
