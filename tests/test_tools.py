"""Tool registration, scoped reads, staged writes, and token use."""

from __future__ import annotations

import pytest

from mitra.security.audit import AuditLog
from mitra.security.policy import ApprovalGate, Policy
from mitra.tools.base import ToolContext
from mitra.tools.file_tools import (
    ProposeWriteFileTool,
    ReadFileTool,
    execute_approved_write,
)
from mitra.tools.registry import ToolRegistry, default_registry


@pytest.fixture()
def ctx(tmp_path):
    gate = ApprovalGate()
    audit = AuditLog(tmp_path / "audit.db")
    yield ToolContext(
        project="P", policy=Policy([tmp_path]), gate=gate, audit=audit
    )
    audit.close()


def test_registry_registers_and_describes():
    registry = ToolRegistry()
    registry.register(ReadFileTool())
    assert registry.get("read_file") is not None
    assert registry.get("missing") is None
    specs = registry.describe()
    assert specs[0].read_only is True
    with pytest.raises(ValueError):
        registry.register(ReadFileTool())


def test_default_registry_has_m2_tools():
    registry = default_registry()
    names = {spec.name for spec in registry.describe()}
    assert names == {
        "read_file",
        "propose_write_file",
        "list_dir",
        "search_files",
        "fetch_url",
        "run_shell",
    }


def test_read_file_ok(tmp_path, ctx):
    target = tmp_path / "hello.txt"
    target.write_text("hello world", encoding="utf-8")
    result = ReadFileTool().invoke({"path": str(target)}, ctx)
    assert result.ok and result.output == "hello world"


def test_read_file_denied_outside_scope(tmp_path, ctx):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    result = ReadFileTool().invoke({"path": str(outside)}, ctx)
    assert not result.ok
    assert "outside" in result.error


def test_read_file_rejects_escape(tmp_path, ctx):
    escape = tmp_path / "sub" / ".." / ".." / "escaped.txt"
    result = ReadFileTool().invoke({"path": str(escape)}, ctx)
    assert not result.ok


def test_read_file_missing_and_args(ctx):
    assert not ReadFileTool().invoke({}, ctx).ok
    assert "Missing" in ReadFileTool().invoke({}, ctx).error
    missing = ReadFileTool().invoke({"path": "/nonexistent-x"}, ctx)
    assert not missing.ok


def test_read_file_size_limit(tmp_path, ctx):
    big = tmp_path / "big.txt"
    big.write_text("x" * 101_000, encoding="utf-8")
    result = ReadFileTool().invoke({"path": str(big)}, ctx)
    assert not result.ok
    assert "over the" in result.error


def test_propose_write_stages_without_touching_disk(tmp_path, ctx):
    target = tmp_path / "new.txt"
    result = ProposeWriteFileTool().invoke(
        {"path": str(target), "content": "data"}, ctx
    )
    assert result.ok
    assert result.proposal_id.startswith("prop-")
    assert not target.exists()
    assert len(ctx.gate.pending()) == 1


def test_propose_write_denied_outside_scope(tmp_path, ctx):
    outside = tmp_path.parent / "evil.txt"
    result = ProposeWriteFileTool().invoke(
        {"path": str(outside), "content": "x"}, ctx
    )
    assert not result.ok
    assert not outside.exists()


def test_execute_write_needs_valid_token(tmp_path, ctx):
    target = tmp_path / "new.txt"
    staged = ProposeWriteFileTool().invoke(
        {"path": str(target), "content": "data"}, ctx
    )
    bad = execute_approved_write(staged.proposal_id, "token-wrong", ctx)
    assert not bad.ok
    assert not target.exists()


def test_execute_write_single_use_token(tmp_path, ctx):
    target = tmp_path / "new.txt"
    staged = ProposeWriteFileTool().invoke(
        {"path": str(target), "content": "data"}, ctx
    )
    token = ctx.gate.approve(staged.proposal_id)
    first = execute_approved_write(staged.proposal_id, token, ctx)
    assert first.ok
    assert target.read_text(encoding="utf-8") == "data"
    second = execute_approved_write(staged.proposal_id, token, ctx)
    assert not second.ok
