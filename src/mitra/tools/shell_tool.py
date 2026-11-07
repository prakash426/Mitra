"""Gated shell execution. Allowlisted commands, confined cwd, approval first.

No shell interpreter is ever spawned. Commands run as argv with the working
directory pinned to the first allowed dir, a timeout, and capped output.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

from .base import Tool, ToolContext, ToolResult


class RunShellTool(Tool):
    """Stage a shell command for approval. Nothing runs until approval."""

    name = "run_shell"
    description = "Stage a shell command. Args: {command}."
    read_only = False

    def invoke(self, args: dict, ctx: ToolContext) -> ToolResult:
        command = str(args.get("command", "")).strip()
        if not command:
            return ToolResult(ok=False, error="Missing 'command' argument.")
        decision = ctx.policy.check_shell(command)
        if not decision.needs_approval:
            ctx.audit.log(
                actor="user",
                project=ctx.project,
                action="run_shell",
                target=command[:120],
                decision="denied",
                rationale=decision.reason,
            )
            return ToolResult(ok=False, error=decision.reason, target=command[:120])
        cwd = ctx.policy.allowed_dirs[0] if ctx.policy.allowed_dirs else Path.cwd()
        proposal = ctx.gate.stage(
            action="run_shell",
            target=str(cwd),
            details=command,
            project=ctx.project,
        )
        ctx.audit.log(
            actor="user",
            project=ctx.project,
            action="run_shell",
            target=str(cwd),
            decision="approval_requested",
            rationale="Proposal %s staged: %s" % (proposal.id, command[:120]),
        )
        return ToolResult(
            ok=True,
            output="Proposal %s staged: %s\nReply 'approve %s' to run, or 'deny %s'."
            % (proposal.id, command, proposal.id, proposal.id),
            target=str(cwd),
            proposal_id=proposal.id,
        )


def execute_approved_shell(
    proposal_id: str, token: str, ctx: ToolContext
) -> ToolResult:
    """Run a staged command after approval. Burns the token on success."""
    proposal = ctx.gate.get(proposal_id)
    if proposal is None:
        return ToolResult(ok=False, error="Unknown proposal '%s'." % proposal_id)
    if not ctx.gate.consume(proposal_id, token):
        ctx.audit.log(
            actor="user",
            project=ctx.project,
            action="run_shell",
            target=proposal.target,
            decision="denied",
            rationale="Approval token invalid, already used, or not approved.",
        )
        return ToolResult(
            ok=False,
            error="Approval missing or already used for '%s'." % proposal_id,
            target=proposal.target,
        )
    shell = ctx.shell
    if shell is None or not shell.enabled:
        return ToolResult(ok=False, error="The shell tool is disabled in config.")
    try:
        argv = shlex.split(proposal.details)
    except ValueError as exc:
        return ToolResult(ok=False, error="Cannot parse command: %s." % exc)
    if not argv:
        return ToolResult(ok=False, error="Empty shell command.")
    exe = shutil.which(Path(argv[0]).name)
    if exe is None:
        return ToolResult(
            ok=False, error="Command '%s' not found on PATH." % Path(argv[0]).name
        )
    cwd = ctx.policy.allowed_dirs[0] if ctx.policy.allowed_dirs else Path.cwd()
    try:
        completed = subprocess.run(
            [exe, *argv[1:]],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=shell.timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        ctx.audit.log(
            actor="user",
            project=ctx.project,
            action="run_shell",
            target=str(cwd),
            decision="error",
            rationale="Proposal %s timed out." % proposal_id,
        )
        return ToolResult(
            ok=False,
            error="Command timed out after %d seconds." % shell.timeout_seconds,
            target=str(cwd),
        )
    except OSError as exc:
        return ToolResult(ok=False, error="Cannot run command: %s." % exc)
    output = completed.stdout or ""
    if completed.stderr:
        output += ("\n" if output else "") + "[stderr]\n" + completed.stderr
    output = output.strip()
    if len(output) > shell.max_output_chars:
        output = output[: shell.max_output_chars] + "\n... truncated"
    ctx.audit.log(
        actor="user",
        project=ctx.project,
        action="run_shell",
        target=str(cwd),
        decision="allowed",
        rationale="Proposal %s approved and executed." % proposal_id,
        result="exit %d, %d chars" % (completed.returncode, len(output)),
    )
    return ToolResult(
        ok=True,
        output="exit %d in %s:\n%s" % (completed.returncode, cwd, output or "(no output)"),
        target=str(cwd),
    )
