"""Tool contract and shared context. Tools never decide policy."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..config import ShellConfig
from ..security.audit import AuditLog
from ..security.policy import ApprovalGate, Policy


@dataclass(frozen=True)
class ToolContext:
    project: str
    policy: Policy
    gate: ApprovalGate
    audit: AuditLog
    shell: ShellConfig | None = None
    fetch_allowed_hosts: tuple[str, ...] = ()
    fetch_private_hosts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    output: str = ""
    error: str = ""
    target: str = ""
    proposal_id: str = ""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    read_only: bool


class Tool(ABC):
    """One capability. Declare read only status honestly."""

    name: str = "base"
    description: str = "base tool"
    read_only: bool = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name, description=self.description, read_only=self.read_only
        )

    @abstractmethod
    def invoke(self, args: dict, ctx: ToolContext) -> ToolResult:
        """Run the tool. Return errors in ToolResult, never raise for input."""
