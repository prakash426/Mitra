"""Gated shell: allowlist, metachars, confinement, approval flow."""

from __future__ import annotations

from mitra.config import ShellConfig
from mitra.security.audit import AuditLog
from mitra.security.policy import ApprovalGate, Policy
from mitra.tools.base import ToolContext
from mitra.tools.shell_tool import RunShellTool, execute_approved_shell


def _ctx(tmp_path, enabled=True):
    shell = ShellConfig(enabled=enabled, allowlist=("echo", "pwd"))
    gate = ApprovalGate()
    audit = AuditLog(tmp_path / "audit.db")
    ctx = ToolContext(
        project="P", policy=Policy([tmp_path], shell=shell), gate=gate, audit=audit,
        shell=shell,
    )
    return ctx, gate, audit


def test_shell_disabled_by_default(tmp_path):
    ctx, gate, audit = _ctx(tmp_path, enabled=False)
    try:
        result = RunShellTool().invoke({"command": "echo hi"}, ctx)
        assert not result.ok and "disabled" in result.error
    finally:
        gate.close()
        audit.close()


def test_shell_disabled_without_config(tmp_path):
    policy = Policy([tmp_path])
    assert not policy.check_shell("echo hi").needs_approval


def test_shell_allowlist_enforced(tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    try:
        result = RunShellTool().invoke({"command": "rm minus rf"}, ctx)
        assert not result.ok and "allowlist" in result.error
    finally:
        gate.close()
        audit.close()


def test_shell_metachars_rejected(tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    try:
        bad_commands = (
            "echo hi; echo pwned",
            "echo a | cat",
            "echo $(whoami)",
            "echo `id`",
            "echo a && echo b",
            "echo a > out.txt",
        )
        for bad in bad_commands:
            result = RunShellTool().invoke({"command": bad}, ctx)
            assert not result.ok, bad
        assert gate.pending("P") == []
    finally:
        gate.close()
        audit.close()


def test_shell_path_args_confined(tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    try:
        assert not RunShellTool().invoke({"command": "echo /etc/passwd"}, ctx).ok
        assert not RunShellTool().invoke({"command": "echo ../secret"}, ctx).ok
        assert not RunShellTool().invoke({"command": "echo ~/secret"}, ctx).ok
    finally:
        gate.close()
        audit.close()


def test_shell_stage_and_execute(tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    try:
        staged = RunShellTool().invoke({"command": "echo hello-shell"}, ctx)
        assert staged.ok and staged.proposal_id.startswith("prop-")
        token = gate.approve(staged.proposal_id)
        done = execute_approved_shell(staged.proposal_id, token, ctx)
        assert done.ok
        assert "hello-shell" in done.output and "exit 0" in done.output
        decisions = {e.decision for e in audit.list_recent("P")}
        assert {"approval_requested", "allowed"} <= decisions
    finally:
        gate.close()
        audit.close()


def test_shell_missing_binary(tmp_path):
    shell = ShellConfig(enabled=True, allowlist=("no-such-bin-xyz",))
    gate = ApprovalGate()
    audit = AuditLog(tmp_path / "a.db")
    ctx = ToolContext(
        project="P", policy=Policy([tmp_path], shell=shell), gate=gate, audit=audit,
        shell=shell,
    )
    try:
        staged = RunShellTool().invoke({"command": "no-such-bin-xyz"}, ctx)
        token = gate.approve(staged.proposal_id)
        done = execute_approved_shell(staged.proposal_id, token, ctx)
        assert not done.ok and "not found" in done.error
    finally:
        gate.close()
        audit.close()
