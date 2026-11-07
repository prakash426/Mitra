"""MCP stdio client plus policy gated tools."""

from __future__ import annotations

import sys
from dataclasses import replace

import pytest

from mitra.config import McpServerConfig, default_config
from mitra.mcp import McpError, call_tool, list_tools
from mitra.orchestrator import build_orchestrator, build_test_orchestrator
from mitra.security.audit import AuditLog
from mitra.security.policy import ApprovalGate, Policy
from mitra.tools.base import ToolContext
from mitra.tools.mcp_tools import McpTool

FAKE_SERVER = """\
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    if "id" not in msg:
        continue
    mid = msg["id"]
    method = msg.get("method")
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {},
                  "serverInfo": {"name": "fake", "version": "0"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "echo", "description": "Echo text."},
                            {"name": "add", "description": "Add numbers."}]}
    elif method == "tools/call":
        name = msg["params"]["name"]
        args = msg["params"].get("arguments", {})
        result = {"content": [{"type": "text",
                               "text": "called %s with %s" % (name, json.dumps(args, sort_keys=True))}]}
    else:
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid,
                                      "error": {"code": -32601, "message": "nope"}}) + "\\n")
        sys.stdout.flush()
        continue
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}) + "\\n")
    sys.stdout.flush()
"""


@pytest.fixture()
def fake_server(tmp_path):
    script = tmp_path / "fake_mcp.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    return script


def _ctx(tmp_path, servers):
    gate = ApprovalGate(tmp_path / "gate.db")
    audit = AuditLog(tmp_path / "audit.db")
    ctx = ToolContext(
        project="P",
        policy=Policy([tmp_path]),
        gate=gate,
        audit=audit,
        mcp_servers=servers,
    )
    return ctx, gate, audit


def test_list_and_call(fake_server):
    tools = list_tools(sys.executable, (str(fake_server),), timeout=10)
    assert [t["name"] for t in tools] == ["echo", "add"]
    out = call_tool(sys.executable, (str(fake_server),), "echo", {"x": 1}, timeout=10)
    assert out == 'called echo with {"x": 1}'


def test_bad_command_raises():
    with pytest.raises(McpError):
        list_tools("/nonexistent/mcp-server-xyz", (), timeout=5)


def test_exiting_server_raises():
    with pytest.raises(McpError):
        list_tools("/usr/bin/false", (), timeout=5)


def test_read_trust_runs_directly(tmp_path, fake_server):
    servers = {
        "fake": McpServerConfig(
            command=sys.executable, args=(str(fake_server),), trust="read"
        )
    }
    ctx, gate, audit = _ctx(tmp_path, servers)
    try:
        tool = McpTool("fake", "echo", "Echo text.", "read")
        assert tool.read_only
        assert tool.name == "mcp__fake__echo"
        result = tool.invoke({"x": 2}, ctx)
        assert result.ok and 'called echo with {"x": 2}' in result.output
        assert result.proposal_id == ""
    finally:
        gate.close()
        audit.close()


def test_staged_trust_needs_approval(tmp_path, fake_server):
    servers = {
        "fake": McpServerConfig(
            command=sys.executable, args=(str(fake_server),), trust="staged"
        )
    }
    ctx, gate, audit = _ctx(tmp_path, servers)
    try:
        tool = McpTool("fake", "echo", "Echo text.", "staged")
        assert not tool.read_only
        staged = tool.invoke({"x": 1}, ctx)
        assert staged.ok and staged.proposal_id.startswith("prop-")
        token = gate.approve(staged.proposal_id)
        from mitra.tools.mcp_tools import execute_approved_mcp

        done = execute_approved_mcp(staged.proposal_id, token, ctx)
        assert done.ok and "called echo" in done.output
    finally:
        gate.close()
        audit.close()


def test_unknown_server_fails_closed(tmp_path):
    ctx, gate, audit = _ctx(tmp_path, {})
    try:
        result = McpTool("ghost", "echo", "", "read").invoke({}, ctx)
        assert not result.ok and "Unknown MCP server" in result.error
    finally:
        gate.close()
        audit.close()


def test_chat_mcp_flow(tmp_path, fake_server):
    orch = build_test_orchestrator(tmp_path)
    try:
        orch.config = replace(
            orch.config,
            mcp_servers={
                "fake": McpServerConfig(
                    command=sys.executable, args=(str(fake_server),), trust="read"
                )
            },
        )
        orch.registry.register(McpTool("fake", "echo", "Echo text.", "read"))
        response = orch.handle_message("P", 'mcp fake echo {"x": 3}')
        assert "mcp:ok" in response.actions
        assert "called echo" in response.text
        bad = orch.handle_message("P", "mcp fake echo {nope")
        assert "mcp:error" in bad.actions
        missing = orch.handle_message("P", "mcp fake ghost")
        assert "mcp:error" in missing.actions
    finally:
        orch.close()


def test_chat_mcp_staged_flow(tmp_path, fake_server):
    orch = build_test_orchestrator(tmp_path)
    try:
        orch.config = replace(
            orch.config,
            mcp_servers={
                "fake": McpServerConfig(
                    command=sys.executable, args=(str(fake_server),), trust="staged"
                )
            },
        )
        orch.registry.register(McpTool("fake", "echo", "Echo text.", "staged"))
        staged = orch.handle_message("P", "mcp fake echo")
        assert staged.approval_request.action == "mcp_call"
        prop = staged.approval_request.proposal_id
        done = orch.handle_message("P", "approve %s" % prop)
        assert "mcp_executed:" + prop in done.actions
        assert "called echo" in done.text
    finally:
        orch.close()


def test_builder_discovers_and_skips_dead(tmp_path, fake_server):
    cfg = replace(
        default_config(tmp_path),
        mcp_servers={
            "fake": McpServerConfig(
                command=sys.executable, args=(str(fake_server),), trust="read"
            ),
            "dead": McpServerConfig(command="/nonexistent/xyz", trust="read"),
        },
    )
    orch = build_orchestrator(config=cfg)
    try:
        assert orch.registry.get("mcp__fake__echo") is not None
        assert orch.registry.get("mcp__fake__add") is not None
        assert orch.registry.get("mcp__dead__echo") is None
    finally:
        orch.close()
