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
import json, os, sys
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
    elif method == "resources/list":
        result = {"resources": [{"uri": "note://one", "name": "First"},
                                {"uri": "note://two", "name": "Second"}]}
    elif method == "resources/read":
        uri = msg["params"]["uri"]
        result = {"contents": [{"uri": uri, "text": "content of %s" % uri}]}
    elif method == "tools/call":
        name = msg["params"]["name"]
        args = msg["params"].get("arguments", {})
        result = {"content": [{"type": "text",
                               "text": "called %s pid=%d with %s" % (name, os.getpid(), json.dumps(args, sort_keys=True))}]}
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
    assert out.startswith("called echo pid=") and out.endswith('with {"x": 1}')


def _pid_of(text):
    return int(text.split("pid=")[1].split()[0])


def test_pool_reuses_process(fake_server):
    from mitra.mcp import McpPool, result_text

    pool = McpPool(timeout=10)
    try:
        first = pool.call(
            sys.executable, (str(fake_server),), "tools/call",
            {"name": "echo", "arguments": {}},
        )
        second = pool.call(
            sys.executable, (str(fake_server),), "tools/call",
            {"name": "echo", "arguments": {}},
        )
        assert _pid_of(result_text(first, "echo")) == _pid_of(result_text(second, "echo"))
    finally:
        pool.close()


def test_pool_respawns_dead_server(fake_server):
    import time

    from mitra.mcp import McpPool, result_text

    pool = McpPool(timeout=10)
    try:
        first = pool.call(
            sys.executable, (str(fake_server),), "tools/call",
            {"name": "echo", "arguments": {}},
        )
        pid_before = _pid_of(result_text(first, "echo"))
        session = pool._sessions[(sys.executable, (str(fake_server),))]
        session._proc.kill()
        session._proc.wait(timeout=10)
        time.sleep(0.1)
        again = pool.call(
            sys.executable, (str(fake_server),), "tools/call",
            {"name": "echo", "arguments": {}},
        )
        assert _pid_of(result_text(again, "echo")) != pid_before
    finally:
        pool.close()


def test_list_and_read_resources(fake_server):
    from mitra.mcp import list_resources, read_resource

    items = list_resources(sys.executable, (str(fake_server),), timeout=10)
    assert [(i["uri"], i["name"]) for i in items] == [
        ("note://one", "First"),
        ("note://two", "Second"),
    ]
    assert (
        read_resource(sys.executable, (str(fake_server),), "note://one", timeout=10)
        == "content of note://one"
    )


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
        assert result.ok and "called echo pid=" in result.output
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


def test_mcp_tool_uses_pool(tmp_path, fake_server):
    from mitra.mcp import McpPool

    servers = {
        "fake": McpServerConfig(
            command=sys.executable, args=(str(fake_server),), trust="read"
        )
    }
    pool = McpPool(timeout=10)
    gate = ApprovalGate(tmp_path / "gate.db")
    audit = AuditLog(tmp_path / "audit.db")
    ctx = ToolContext(
        project="P",
        policy=Policy([tmp_path]),
        gate=gate,
        audit=audit,
        mcp_servers=servers,
        mcp_pool=pool,
    )
    try:
        tool = McpTool("fake", "echo", "Echo text.", "read")
        first = tool.invoke({}, ctx)
        second = tool.invoke({}, ctx)
        assert _pid_of(first.output) == _pid_of(second.output)
    finally:
        gate.close()
        audit.close()
        pool.close()


def test_chat_resource_read_trust(tmp_path, fake_server):
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
        listed = orch.handle_message("P", "mcp resources fake")
        assert "mcp:ok" in listed.actions
        assert "note://one" in listed.text and "note://two" in listed.text
        read = orch.handle_message("P", "mcp resource fake note://one")
        assert "mcp:ok" in read.actions
        assert "content of note://one" in read.text
        assert read.approval_request is None
    finally:
        orch.close()


def test_chat_resource_staged_trust(tmp_path, fake_server):
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
        staged = orch.handle_message("P", "mcp resource fake note://two")
        assert staged.approval_request.action == "mcp_call"
        assert "content of" not in staged.text
        prop = staged.approval_request.proposal_id
        done = orch.handle_message("P", "approve %s" % prop)
        assert "mcp_executed:" + prop in done.actions
        assert "content of note://two" in done.text
    finally:
        orch.close()


def test_old_staged_spec_without_kind_still_runs(tmp_path, fake_server):
    import json

    from mitra.tools.mcp_tools import execute_approved_mcp

    servers = {
        "fake": McpServerConfig(
            command=sys.executable, args=(str(fake_server),), trust="staged"
        )
    }
    ctx, gate, audit = _ctx(tmp_path, servers)
    try:
        proposal = gate.stage(
            action="mcp_call",
            target="fake:echo",
            details=json.dumps({"server": "fake", "tool": "echo", "args": {}}),
            project="P",
        )
        token = gate.approve(proposal.id)
        done = execute_approved_mcp(proposal.id, token, ctx)
        assert done.ok and "called echo pid=" in done.output
    finally:
        gate.close()
        audit.close()


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
