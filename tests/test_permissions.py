"""Policy decisions: allowed reads, gated writes, hard denials."""

from __future__ import annotations

import pytest

from mitra.config import ShellConfig
from mitra.security.policy import ApprovalGate, Policy


@pytest.fixture()
def policy(tmp_path):
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / "a.txt").write_text("a", encoding="utf-8")
    return Policy([tmp_path / "proj"])


def test_calendar_add_needs_approval(policy):
    decision = policy.check_calendar_add("Dentist", "2026-11-11", "09:00")
    assert decision.needs_approval and not decision.allowed


def test_calendar_add_denies_bad_shapes(policy):
    assert not policy.check_calendar_add("", "2026-11-11", "09:00").needs_approval
    assert not policy.check_calendar_add("x", "11-11", "09:00").needs_approval
    assert not policy.check_calendar_add("x", "2026-11-11", "9am").needs_approval
    assert not policy.check_calendar_add("x" * 201, "2026-11-11", "09:00").needs_approval


def test_read_allowed_inside(policy, tmp_path):
    decision = policy.check_read(tmp_path / "proj" / "a.txt")
    assert decision.allowed and not decision.needs_approval


def test_read_denied_outside(policy, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    decision = policy.check_read(outside)
    assert not decision.allowed and not decision.needs_approval


def test_read_denied_missing_and_directory(policy, tmp_path):
    assert not policy.check_read(tmp_path / "proj" / "nope.txt").allowed
    assert not policy.check_read(tmp_path / "proj").allowed


def test_write_needs_approval_inside(policy, tmp_path):
    decision = policy.check_write(tmp_path / "proj" / "new.txt")
    assert not decision.allowed and decision.needs_approval


def test_write_denied_outside(policy, tmp_path):
    decision = policy.check_write(tmp_path / "evil.txt")
    assert not decision.allowed and not decision.needs_approval


def test_dotdot_escape_rejected(policy, tmp_path):
    escape = tmp_path / "proj" / ".." / "evil.txt"
    assert policy.resolve_inside(str(escape)) is None
    assert not policy.check_write(str(escape)).needs_approval


def test_relative_paths_anchor_to_first_allowed_dir(policy, tmp_path):
    resolved = policy.resolve_inside("a.txt")
    assert resolved == (tmp_path / "proj" / "a.txt").resolve()
    assert policy.check_read("a.txt").allowed
    assert policy.check_write("new.txt").needs_approval
    assert policy.resolve_inside("../evil.txt") is None


def test_gate_approve_deny_consume():
    gate = ApprovalGate()
    proposal = gate.stage("write_file", "/tmp/x", "data", "P")
    assert gate.get(proposal.id) == proposal
    assert len(gate.pending()) == 1
    token = gate.approve(proposal.id)
    assert gate.consume(proposal.id, token) is True
    assert gate.consume(proposal.id, token) is False
    assert gate.consume(proposal.id, "token-nope") is False


def test_gate_denial_blocks_consume():
    gate = ApprovalGate()
    proposal = gate.stage("write_file", "/tmp/x", "data", "P")
    gate.deny(proposal.id)
    assert gate.consume(proposal.id, "token-" + proposal.id) is False
    assert gate.pending() == []


def test_gate_unknown_and_double_decide():
    gate = ApprovalGate()
    with pytest.raises(KeyError):
        gate.approve("prop-99")
    with pytest.raises(KeyError):
        gate.deny("prop-99")
    proposal = gate.stage("write_file", "/tmp/x", "data", "P")
    gate.approve(proposal.id)
    with pytest.raises(ValueError):
        gate.deny(proposal.id)


def test_gate_persists_across_instances(tmp_path):
    db = tmp_path / "gate.db"
    first = ApprovalGate(db)
    proposal = first.stage("write_file", "/tmp/x", "data", "P")
    first.close()
    second = ApprovalGate(db)
    try:
        assert second.get(proposal.id) is not None
        assert [p.id for p in second.pending("P")] == [proposal.id]
        token = second.approve(proposal.id)
        assert second.consume(proposal.id, token) is True
    finally:
        second.close()


def test_gate_expiry(tmp_path):
    db = tmp_path / "gate.db"
    gate = ApprovalGate(db, ttl_seconds=3600)
    try:
        proposal = gate.stage("write_file", "/tmp/x", "data", "P")
        gate._conn.execute(
            "UPDATE proposals SET expires_at = '2000-01-01T00:00:00+00:00'"
        )
        gate._conn.commit()
        assert gate.pending("P") == []
        assert gate.get(proposal.id) is None
    finally:
        gate.close()


def test_gate_tokens_are_random():
    gate = ApprovalGate()
    try:
        first = gate.stage("write_file", "/a", "x", "P")
        second = gate.stage("write_file", "/b", "y", "P")
        assert gate.approve(first.id) != gate.approve(second.id)
    finally:
        gate.close()


def test_gate_purges_old_decided(tmp_path):
    gate = ApprovalGate(tmp_path / "gate.db")
    try:
        old = gate.stage("write_file", "/a", "x", "P")
        gate.approve(old.id)
        gate._conn.execute(
            "UPDATE proposals SET decided_at = '2001-01-01T00:00:00+00:00' WHERE id = 1"
        )
        gate._conn.commit()
        fresh = gate.stage("write_file", "/b", "y", "P")
        gate.approve(fresh.id)
        gate.pending("P")
        assert gate.get(old.id) is None
        assert gate.get(fresh.id) is not None
    finally:
        gate.close()


def test_flag_rules_enforced(tmp_path):
    shell = ShellConfig(enabled=True, allowlist=("git",), flag_rules={"git": ("status",)})
    policy = Policy([tmp_path], shell=shell)
    assert policy.check_shell("git status").needs_approval
    assert policy.check_shell("git show HEAD").needs_approval
    denied = policy.check_shell("git status --short")
    assert not denied.needs_approval and not denied.allowed
    assert "Flag" in denied.reason
