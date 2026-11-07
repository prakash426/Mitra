"""File tools. Read is direct inside scope. Write is staged, never direct."""

from __future__ import annotations

from .base import Tool, ToolContext, ToolResult

MAX_READ_BYTES = 100_000


class ReadFileTool(Tool):
    """Read a text file inside the allowed directories."""

    name = "read_file"
    description = "Read a text file. Args: {path}."
    read_only = True

    def invoke(self, args: dict, ctx: ToolContext) -> ToolResult:
        path = str(args.get("path", "")).strip()
        if not path:
            return ToolResult(ok=False, error="Missing 'path' argument.")
        decision = ctx.policy.check_read(path)
        if not decision.allowed:
            ctx.audit.log(
                actor="user",
                project=ctx.project,
                action="read_file",
                target=path,
                decision="denied",
                rationale=decision.reason,
            )
            return ToolResult(ok=False, error=decision.reason, target=path)
        resolved = ctx.policy.resolve_inside(path)
        assert resolved is not None
        try:
            size = resolved.stat().st_size
        except OSError as exc:
            return ToolResult(ok=False, error="Cannot stat file: %s." % exc, target=path)
        if size > MAX_READ_BYTES:
            return ToolResult(
                ok=False,
                error="File is %d bytes, over the %d byte read limit."
                % (size, MAX_READ_BYTES),
                target=str(resolved),
            )
        try:
            content = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolResult(
                ok=False, error="Cannot read file: %s." % exc, target=str(resolved)
            )
        ctx.audit.log(
            actor="user",
            project=ctx.project,
            action="read_file",
            target=str(resolved),
            decision="allowed",
            rationale=decision.reason,
            result="%d chars read" % len(content),
        )
        return ToolResult(ok=True, output=content, target=str(resolved))


class ProposeWriteFileTool(Tool):
    """Stage a file write proposal. Nothing touches disk until approval."""

    name = "propose_write_file"
    description = "Stage a file write for approval. Args: {path, content}."
    read_only = False

    def invoke(self, args: dict, ctx: ToolContext) -> ToolResult:
        path = str(args.get("path", "")).strip()
        content = args.get("content", "")
        if not path:
            return ToolResult(ok=False, error="Missing 'path' argument.")
        if not isinstance(content, str) or not content:
            return ToolResult(ok=False, error="Missing 'content' argument.")
        decision = ctx.policy.check_write(path)
        if not decision.needs_approval:
            ctx.audit.log(
                actor="user",
                project=ctx.project,
                action="propose_write_file",
                target=path,
                decision="denied",
                rationale=decision.reason,
            )
            return ToolResult(ok=False, error=decision.reason, target=path)
        resolved = ctx.policy.resolve_inside(path)
        assert resolved is not None
        proposal = ctx.gate.stage(
            action="write_file",
            target=str(resolved),
            details=content,
            project=ctx.project,
        )
        ctx.audit.log(
            actor="user",
            project=ctx.project,
            action="propose_write_file",
            target=str(resolved),
            decision="approval_requested",
            rationale="Proposal %s staged." % proposal.id,
            result="%d chars proposed" % len(content),
        )
        preview = content if len(content) <= 500 else content[:500] + "..."
        return ToolResult(
            ok=True,
            output="Proposal %s staged for %s.\nPreview:\n%s"
            % (proposal.id, resolved, preview),
            target=str(resolved),
            proposal_id=proposal.id,
        )


def execute_approved_write(
    proposal_id: str, token: str, ctx: ToolContext
) -> ToolResult:
    """Write a staged proposal after approval. Burns the token on success."""
    proposal = ctx.gate.get(proposal_id)
    if proposal is None:
        return ToolResult(ok=False, error="Unknown proposal '%s'." % proposal_id)
    if not ctx.gate.consume(proposal_id, token):
        ctx.audit.log(
            actor="user",
            project=ctx.project,
            action="write_file",
            target=proposal.target,
            decision="denied",
            rationale="Approval token invalid, already used, or not approved.",
        )
        return ToolResult(
            ok=False,
            error="Approval missing or already used for '%s'." % proposal_id,
            target=proposal.target,
        )
    resolved = ctx.policy.resolve_inside(proposal.target)
    if resolved is None:
        return ToolResult(
            ok=False,
            error="Target sits outside the allowed directories.",
            target=proposal.target,
        )
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(proposal.details, encoding="utf-8")
    except OSError as exc:
        return ToolResult(
            ok=False, error="Cannot write file: %s." % exc, target=str(resolved)
        )
    ctx.audit.log(
        actor="user",
        project=ctx.project,
        action="write_file",
        target=str(resolved),
        decision="allowed",
        rationale="Proposal %s approved and executed." % proposal_id,
        result="%d chars written" % len(proposal.details),
    )
    return ToolResult(ok=True, output="Wrote %s." % resolved, target=str(resolved))
