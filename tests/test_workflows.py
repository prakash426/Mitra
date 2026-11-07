"""Saved parameterized workflows built from plans."""

from __future__ import annotations

import json

import pytest

from mitra.orchestrator import build_test_orchestrator
from mitra.planning import (
    PlanStep,
    PlanStore,
    parametrize,
    substitute,
    workflow_params,
)
from mitra.providers.base import Generation, ModelProvider


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


def test_substitute_nested_and_missing():
    args = {"path": "{{file}}", "opts": ["{{a}}", "plain", 3], "n": 1}
    assert substitute(args, {"file": "R", "a": "b"}) == {
        "path": "R",
        "opts": ["b", "plain", 3],
        "n": 1,
    }
    with pytest.raises(KeyError):
        substitute({"path": "{{file}}"}, {})
    assert substitute({"p": "a {{ }} b {{x"}, {}) == {"p": "a {{ }} b {{x"}


def test_parametrize_and_params():
    steps = (
        PlanStep(tool="read_file", args={"path": "README.md"}),
        PlanStep(tool="read_file", args={"path": "README.md"}),
    )
    filled = parametrize([s.args for s in steps], [("README.md", "file")])
    assert filled == [{"path": "{{file}}"}, {"path": "{{file}}"}]
    param_steps = tuple(PlanStep(tool=s.tool, args=a) for s, a in zip(steps, filled))
    assert workflow_params(param_steps) == ["file"]
    assert workflow_params(steps) == []


def test_workflow_store_crud(tmp_path):
    store = PlanStore(tmp_path / "w.db")
    try:
        assert store.get_workflow("P", "x") is None
        assert store.list_workflows("P") == []
        record = store.save_workflow(
            "P", "brief", [PlanStep(tool="read_file", args={"path": "R"})]
        )
        assert record.name == "brief"
        assert store.get_workflow("P", "brief").steps[0].tool == "read_file"
        assert store.get_workflow("Other", "brief") is None
        store.save_workflow("P", "brief", [PlanStep(tool="list_dir", args={})])
        assert store.get_workflow("P", "brief").steps[0].tool == "list_dir"
        assert [w.name for w in store.list_workflows("P")] == ["brief"]
        assert store.delete_workflow("P", "brief")
        assert not store.delete_workflow("P", "brief")
    finally:
        store.close()


def _planned_orch(workdir, plan_text):
    orch = build_test_orchestrator(workdir)
    orch.provider = ScriptedProvider([plan_text, "rendered"])
    orch.handle_message("P", "plan: look at the readme")
    return orch


def test_save_and_run_roundtrip(workdir):
    plan = json.dumps({"steps": [{"tool": "read_file", "args": {"path": "README.md"}}]})
    orch = _planned_orch(workdir, plan)
    try:
        saved = orch.handle_message("P", "save plan-1 as brief")
        assert "workflow_saved:brief" in saved.actions
        assert "1 steps" in saved.text
        assert "brief: 1 steps" in orch.handle_message("P", "workflows").text
        rerun = orch.handle_message("P", "run workflow brief")
        assert "plan_done:plan-2" in rerun.actions
        assert "Sample Project" in rerun.evidence[0].text
    finally:
        orch.close()


def test_parameterized_save_and_run(workdir):
    (workdir / "NOTES.md").write_text("release notes here", encoding="utf-8")
    plan = json.dumps({"steps": [{"tool": "read_file", "args": {"path": "README.md"}}]})
    orch = _planned_orch(workdir, plan)
    try:
        saved = orch.handle_message("P", "save plan-1 as brief with README.md as file")
        assert "Needs parameters: file" in saved.text
        missing = orch.handle_message("P", "run workflow brief")
        assert "workflow:error" in missing.actions
        assert "needs parameter 'file'" in missing.text
        rerun = orch.handle_message("P", "run workflow brief file=NOTES.md")
        assert "plan_done:plan-2" in rerun.actions
        assert "release notes here" in rerun.evidence[0].text
    finally:
        orch.close()


def test_run_with_quoted_value(workdir):
    (workdir / "my notes.md").write_text("spaced content", encoding="utf-8")
    plan = json.dumps({"steps": [{"tool": "read_file", "args": {"path": "README.md"}}]})
    orch = _planned_orch(workdir, plan)
    try:
        orch.handle_message("P", "save plan-1 as brief with README.md as file")
        rerun = orch.handle_message("P", 'run workflow brief file="my notes.md"')
        assert "spaced content" in rerun.evidence[0].text
    finally:
        orch.close()


def test_workflow_errors_and_forget(workdir):
    plan = json.dumps({"steps": [{"tool": "read_file", "args": {"path": "README.md"}}]})
    orch = _planned_orch(workdir, plan)
    try:
        assert "no workflow 'ghost'" in orch.handle_message("P", "run workflow ghost").text
        assert "no plan-9" in orch.handle_message("P", "save plan-9 as x").text
        bad = orch.handle_message("P", "save plan-1 as 'bad/name'")
        assert "workflow:error" in bad.actions
        orch.handle_message("P", "save plan-1 as brief")
        assert "Forgot workflow 'brief'" in orch.handle_message("P", "forget workflow brief").text
        assert "No workflows for P" in orch.handle_message("P", "workflows").text
        other = orch.handle_message("Other", "save plan-1 as steal")
        assert "belongs to project P" in other.text
    finally:
        orch.close()


def test_save_run_parse_does_not_steal_shell_or_write(workdir):
    orch = build_test_orchestrator(workdir)
    try:
        staged = orch.handle_message("P", "save hello to out.txt")
        assert staged.approval_request.action == "write_file"
        shell = orch.handle_message("P", "run echo hi")
        assert "workflow" not in " ".join(shell.actions)
        assert "plan_done" not in " ".join(shell.actions)
    finally:
        orch.close()
