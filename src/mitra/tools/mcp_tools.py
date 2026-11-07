"""MCP tools behind the policy gate.

Servers trusted as "read" run directly. All other servers stage an
approval proposal per call, exactly like shell and file writes.
"""

from __future__ import annotations

import json

from ..mcp import McpError, call_tool, read_resource, result_text
from .base import Tool, ToolContext, ToolResult


def _call_via_pool_or_spawn(ctx: ToolContext, server: str, tool: str, args: dict) -> str:
    cfg = ctx.mcp_servers.get(server)
    if cfg is None:
        raise McpError("Unknown MCP server '%s'." % server)
    if ctx.mcp_pool is not None:
        result = ctx.mcp_pool.call(
            cfg.command, cfg.args, "tools/call", {"name": tool, "arguments": args}
        )
        return result_text(result, tool)
    return call_tool(cfg.command, cfg.args, tool, args)


def read_via_pool_or_spawn(ctx: ToolContext, server: str, uri: str) -> str:
    cfg = ctx.mcp_servers.get(server)
    if cfg is None:
        raise McpError("Unknown MCP server '%s'." % server)
    if ctx.mcp_pool is not None:
        result = ctx.mcp_pool.call(cfg.command, cfg.args, "resources/read", {"uri": uri})
        parts = []
        contents = result.get("contents", [])
        if isinstance(contents, list):
            for block in contents:
                if isinstance(block, dict) and "text" in block:
                    parts.append(str(block.get("text", "")))
        return "\n".join(parts) if parts else "(no text content)"
    return read_resource(cfg.command, cfg.args, uri)


def mcp_tool_name(server: str, tool: str) -> str:
    return "mcp__%s__%s" % (server, tool)


class McpTool(Tool):
    """One tool on one configured MCP server."""

    def __init__(self, server: str, tool_name: str, description: str, trust: str) -> None:
        self._server = server
        self._tool_name = tool_name
        self.name = mcp_tool_name(server, tool_name)
        self.description = description or "MCP tool %s on %s." % (tool_name, server)
        self.read_only = trust == "read"

    def invoke(self, args: dict, ctx: ToolContext) -> ToolResult:
        cfg = ctx.mcp_servers.get(self._server)
        if cfg is None:
            return ToolResult(ok=False, error="Unknown MCP server '%s'." % self._server)
        if not isinstance(args, dict):
            return ToolResult(ok=False, error="MCP arguments must be an object.")
        target = "%s:%s" % (self._server, self._tool_name)
        if self.read_only:
            try:
                text = _call_via_pool_or_spawn(ctx, self._server, self._tool_name, args)
            except McpError as exc:
                return ToolResult(ok=False, error=str(exc), target=target)
            ctx.audit.log(
                actor="user",
                project=ctx.project,
                action="mcp_call",
                target=target,
                decision="allowed",
                rationale="trusted read server",
                result="%d chars" % len(text),
            )
            return ToolResult(ok=True, output=text, target=target)
        spec = {
            "kind": "tool",
            "server": self._server,
            "tool": self._tool_name,
            "args": args,
        }
        proposal = ctx.gate.stage(
            action="mcp_call",
            target=target,
            details=json.dumps(spec),
            project=ctx.project,
        )
        ctx.audit.log(
            actor="user",
            project=ctx.project,
            action="mcp_call",
            target=target,
            decision="approval_requested",
            rationale="Proposal %s staged." % proposal.id,
        )
        return ToolResult(
            ok=True,
            output="Proposal %s staged: MCP %s." % (proposal.id, target),
            target=target,
            proposal_id=proposal.id,
        )


def execute_approved_mcp(proposal_id: str, token: str, ctx: ToolContext) -> ToolResult:
    """Run a staged MCP call after approval. Burns the token."""
    proposal = ctx.gate.get(proposal_id)
    if proposal is None:
        return ToolResult(ok=False, error="Unknown proposal '%s'." % proposal_id)
    if not ctx.gate.consume(proposal_id, token):
        ctx.audit.log(
            actor="user",
            project=ctx.project,
            action="mcp_call",
            target=proposal.target,
            decision="denied",
            rationale="Approval token invalid, already used, or not approved.",
        )
        return ToolResult(
            ok=False,
            error="Approval missing or already used for '%s'." % proposal_id,
            target=proposal.target,
        )
    try:
        spec = json.loads(proposal.details)
        kind = str(spec.get("kind", "tool"))
        server = str(spec["server"])
    except (ValueError, KeyError, TypeError, AttributeError):
        return ToolResult(ok=False, error="Proposal details are unreadable.")
    if kind not in ("tool", "resource"):
        return ToolResult(ok=False, error="Proposal details are unreadable.")
    try:
        if kind == "resource":
            text = read_via_pool_or_spawn(ctx, server, str(spec.get("uri", "")))
        else:
            text = _call_via_pool_or_spawn(
                ctx, server, str(spec["tool"]), dict(spec.get("args", {}))
            )
    except McpError as exc:
        return ToolResult(ok=False, error=str(exc), target=proposal.target)
    except (KeyError, TypeError, AttributeError):
        return ToolResult(ok=False, error="Proposal details are unreadable.")
    ctx.audit.log(
        actor="user",
        project=ctx.project,
        action="mcp_call",
        target=proposal.target,
        decision="allowed",
        rationale="Proposal %s approved and executed." % proposal_id,
        result="%d chars" % len(text),
    )
    return ToolResult(ok=True, output=text, target=proposal.target)
