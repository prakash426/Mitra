"""Core orchestration loop. Transparent, typed, and testable.

One pass per message: parse intent, gather evidence, check policy, act or
pause for approval, audit, then render a conversational reply. Rule based
intent parsing ships first; model driven planning is a later gated upgrade.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import MitraConfig, ShellConfig, default_config
from .memory.store import MemoryHit, MemoryRecord, MemoryStore
from .tools.shell_tool import execute_approved_shell
from .providers.base import DRAFT_MARKER, ModelProvider, ProviderError
from .providers.mock import MockProvider
from .security.audit import AuditLog
from .security.policy import ApprovalGate, Policy
from .tools.base import ToolContext
from .tools.file_tools import execute_approved_write
from .tools.registry import ToolRegistry, default_registry

RENDER_INSTRUCTIONS = (
    "You are Mitra, a personal AI companion. Rephrase the draft below "
    "conversationally. Stick to the facts in the evidence. Never invent "
    "decisions, files, or events. Keep sources visible.\n\n" + DRAFT_MARKER + "\n"
)

_TOPIC_STOPWORDS = frozenset(
    "the and for with that this from into over after before about what when "
    "remember correction actually just really that".split()
)


@dataclass(frozen=True)
class EvidenceItem:
    text: str
    source: str
    updated: str


@dataclass(frozen=True)
class ApprovalRequest:
    proposal_id: str
    action: str
    target: str
    preview: str


@dataclass
class OrchestratorResponse:
    text: str
    actions: list[str] = field(default_factory=list)
    approval_request: ApprovalRequest | None = None
    evidence: list[EvidenceItem] = field(default_factory=list)


@dataclass(frozen=True)
class _Intent:
    kind: str
    arg: str = ""
    arg2: str = ""


def _topic_hint(text: str) -> str:
    for raw in text.split():
        cleaned = "".join(ch for ch in raw if ch.isalnum())
        if len(cleaned) >= 4 and cleaned.lower() not in _TOPIC_STOPWORDS:
            return cleaned
    return ""


def _kind_for_fact(text: str) -> str:
    lowered = text.lower()
    if "decid" in lowered or "decision" in lowered:
        return "decision"
    if "prefer" in lowered or "always" in lowered or "never" in lowered:
        return "preference"
    if lowered.startswith("todo") or "task" in lowered:
        return "task"
    return "fact"


def _parse(message: str) -> _Intent:
    text = message.strip()
    lowered = text.lower()

    match = re.match(r"(?is)^\s*approve\s+(\S+)\s*$", text)
    if match:
        return _Intent(kind="approve", arg=match.group(1))
    match = re.match(r"(?is)^\s*deny\s+(\S+)\s*$", text)
    if match:
        return _Intent(kind="deny", arg=match.group(1))
    if re.match(r"(?is)^\s*pending\s*$", text):
        return _Intent(kind="pending")
    if re.match(r"(?is)^\s*help\s*$", text) or "what can you do" in lowered:
        return _Intent(kind="help")

    match = re.match(r"(?is)^\s*correction\s*:\s*(.+)$", text)
    if match:
        return _Intent(kind="correction", arg=match.group(1).strip())
    match = re.match(r"(?is)^\s*actually[,\s]+(.+)$", text)
    if match:
        return _Intent(kind="correction", arg=match.group(1).strip())

    match = re.search(r"(?is)\bremember that\b\s*(.+)", text)
    if match:
        return _Intent(kind="remember", arg=match.group(1).strip())
    match = re.match(r"(?is)^\s*remember to\s+(.+)$", text)
    if match:
        return _Intent(kind="task_add", arg=match.group(1).strip())
    match = re.match(r"(?is)^\s*(?:todo|task|add task)\s*:\s*(.+)$", text)
    if match:
        return _Intent(kind="task_add", arg=match.group(1).strip())
    match = re.match(r"(?is)^\s*standing(?: instruction)?\s*:\s*(.+)$", text)
    if match:
        return _Intent(kind="standing_add", arg=match.group(1).strip())
    match = re.match(r"(?is)^\s*remember\s+(.+)$", text)
    if match:
        return _Intent(kind="remember", arg=match.group(1).strip())

    recall_markers = (
        "what did we decide",
        "what do you remember",
        "what did we say",
        "remind me",
        "recall",
    )
    if any(marker in lowered for marker in recall_markers):
        return _Intent(kind="recall", arg=text)
    if re.match(r"(?is)^\s*tasks?\s*$", text) or (
        "task" in lowered
        and any(word in lowered for word in ("list", "show", "my ", "all "))
    ):
        return _Intent(kind="task_list")
    match = re.match(
        r"(?is)^\s*(?:done|complete|finish|close)\s+(?:task\s+)?(\d+)\s*$", text
    )
    if match:
        return _Intent(kind="task_done", arg=match.group(1))
    match = re.match(r"(?is)^\s*task\s+(\d+)\s+(?:done|complete|finished)\s*$", text)
    if match:
        return _Intent(kind="task_done", arg=match.group(1))
    match = re.match(r"(?is)^\s*start\s+(?:task\s+)?(\d+)\s*$", text)
    if match:
        return _Intent(kind="task_start", arg=match.group(1))
    if "show me" in lowered and any(
        word in lowered for word in ("saved", "memor", "important", "project")
    ):
        return _Intent(kind="list_memories")
    if "list memories" in lowered or "important information" in lowered:
        return _Intent(kind="list_memories")

    match = re.match(r"(?is)^\s*(?:save|write)\s+(.+?)\s+to\s+(\S+)\s*$", text)
    if match:
        content = match.group(1).strip().strip("\"'")
        target = re.sub(r"\.$", "", match.group(2).strip())
        return _Intent(kind="stage_write", arg=target, arg2=content)
    match = re.match(r"(?is)^\s*update\s+(?:the\s+)?(\S+)\s+to\s+(.+)$", text)
    if match:
        target = re.sub(r"\.$", "", match.group(1).strip())
        return _Intent(kind="stage_write", arg=target, arg2=match.group(2).strip())
    match = re.match(r"(?is)^\s*update\s+(?:the\s+)?(\S+)\s*$", text)
    if match:
        target = re.sub(r"\.$", "", match.group(1).strip())
        return _Intent(kind="update_needs_content", arg=target)
    match = re.match(r"(?is)^\s*(?:list|ls|show files)(?:\s+(?:in\s+)?(\S+))?\s*$", text)
    if match:
        return _Intent(kind="list_dir", arg=(match.group(1) or "").strip())
    match = re.match(
        r"(?is)^\s*(?:search|find)(?: files?)?\s+(?:for\s+)?(\S+?)(?:\s+in\s+(\S+))?\s*$",
        text,
    )
    if match:
        return _Intent(
            kind="search_files",
            arg=match.group(1).strip(),
            arg2=(match.group(2) or "").strip(),
        )
    match = re.match(r"(?is)^\s*fetch\s+(\S+)\s*$", text)
    if match:
        return _Intent(kind="fetch_url", arg=match.group(1).strip())
    match = re.match(r"(?is)^\s*(?:run|shell|exec)\s*:?\s*(.+)$", text)
    if match:
        return _Intent(kind="run_shell", arg=match.group(1).strip())

    if re.search(r"(?is)\bread\b", text):
        return _Intent(kind="read_file", arg=text)

    return _Intent(kind="chat", arg=text)


def _evidence_from_hits(hits: list[MemoryHit]) -> list[EvidenceItem]:
    return [
        EvidenceItem(
            text=hit.record.text,
            source=hit.record.source,
            updated=hit.record.updated_at,
        )
        for hit in hits
    ]


def _evidence_from_records(records: list[MemoryRecord]) -> list[EvidenceItem]:
    return [
        EvidenceItem(text=r.text, source=r.source, updated=r.updated_at) for r in records
    ]


class Orchestrator:
    """Owns the loop. Holds references, creates nothing on the hot path."""

    def __init__(
        self,
        config: MitraConfig,
        provider: ModelProvider,
        memory: MemoryStore,
        audit: AuditLog,
        registry: ToolRegistry,
        gate: ApprovalGate,
        policy: Policy,
    ) -> None:
        self.config = config
        self.provider = provider
        self.memory = memory
        self.audit = audit
        self.registry = registry
        self.gate = gate
        self.policy = policy

    def close(self) -> None:
        self.memory.close()
        self.audit.close()
        self.gate.close()

    def handle_message(self, project: str, message: str) -> OrchestratorResponse:
        """Run one orchestration pass over a user message."""
        project = project.strip() or "default"
        message = message.strip()
        if not message:
            return OrchestratorResponse(text="Say something and I will help.")
        intent = _parse(message)
        ctx = ToolContext(
            project=project,
            policy=self.policy,
            gate=self.gate,
            audit=self.audit,
            shell=self.policy.shell or self.config.shell,
        )
        handler = {
            "remember": self._remember,
            "correction": self._correction,
            "recall": self._recall,
            "list_memories": self._list_memories,
            "task_add": self._task_add,
            "task_list": self._task_list,
            "task_done": self._task_done,
            "task_start": self._task_start,
            "standing_add": self._standing_add,
            "read_file": self._read_file,
            "list_dir": self._list_dir,
            "search_files": self._search_files,
            "fetch_url": self._fetch_url,
            "run_shell": self._run_shell,
            "stage_write": self._stage_write,
            "update_needs_content": self._update_needs_content,
            "approve": self._approve,
            "deny": self._deny,
            "pending": self._pending,
            "help": self._help,
            "chat": self._chat,
        }.get(intent.kind, self._chat)
        draft, actions, approval, evidence = handler(ctx, intent)
        text = self._render(ctx, draft, evidence)
        return OrchestratorResponse(
            text=text, actions=actions, approval_request=approval, evidence=evidence
        )

    def _render(
        self, ctx: ToolContext, draft: str, evidence: list[EvidenceItem]
    ) -> str:
        context_lines = [
            "- %s (source: %s, updated %s)" % (e.text, e.source, e.updated)
            for e in evidence
        ]
        try:
            generation = self.provider.generate(
                RENDER_INSTRUCTIONS + draft, "\n".join(context_lines)
            )
            return generation.text
        except ProviderError as exc:
            self.audit.log(
                actor="system",
                project=ctx.project,
                action="model_generate",
                target=self.provider.name,
                decision="error",
                rationale=str(exc),
            )
            return (
                "I could not reach the configured model (%s). "
                "Here is the direct result:\n\n%s" % (exc, draft)
            )

    def _remember(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        kind = _kind_for_fact(intent.arg)
        try:
            record = self.memory.add(ctx.project, kind, intent.arg, source="user")
        except ValueError as exc:
            return "I could not save that: %s" % exc, [], None, []
        self.audit.log(
            actor="user",
            project=ctx.project,
            action="memory_add",
            target="memory:%d" % record.id,
            decision="allowed",
            rationale="Explicit remember request.",
            result=record.text[:120],
        )
        draft = "Noted for %s. Saved as %s:\n%s" % (ctx.project, kind, record.text)
        return draft, ["memory_add:%d" % record.id], None, _evidence_from_records([record])

    def _correction(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        topic = _topic_hint(intent.arg)
        try:
            record = self.memory.add_correction(ctx.project, intent.arg, topic)
        except ValueError as exc:
            return "I could not save that correction: %s" % exc, [], None, []
        self.audit.log(
            actor="user",
            project=ctx.project,
            action="memory_correct",
            target="memory:%d" % record.id,
            decision="allowed",
            rationale="Explicit correction; older matches superseded.",
            result=record.text[:120],
        )
        draft = "Correction saved for %s. It now overrides older notes on '%s'." % (
            ctx.project,
            topic or "this topic",
        )
        return draft, ["memory_correct:%d" % record.id], None, _evidence_from_records([record])

    def _recall(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        hits = self.memory.recall(ctx.project, intent.arg, limit=5)
        if not hits:
            return (
                "I have nothing saved about that for %s yet." % ctx.project,
                ["memory_recall:0"],
                None,
                [],
            )
        lines = []
        for pos, hit in enumerate(hits, start=1):
            lines.append(
                "%d. %s\n   source: %s, updated %s"
                % (pos, hit.record.text, hit.record.source, hit.record.updated_at)
            )
        draft = "Here is what I have for %s:\n%s" % (ctx.project, "\n".join(lines))
        return draft, ["memory_recall:%d" % len(hits)], None, _evidence_from_hits(hits)

    def _list_memories(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        records = self.memory.list_project(ctx.project, limit=20)
        if not records:
            return (
                "No memories saved for %s yet. Tell me something with "
                "'remember ...'." % ctx.project,
                ["memory_list:0"],
                None,
                [],
            )
        lines = []
        for pos, record in enumerate(records, start=1):
            lines.append(
                "%d. [%s] %s\n   source: %s, updated %s"
                % (pos, record.kind, record.text, record.source, record.updated_at)
            )
        draft = "Saved for %s:\n%s" % (ctx.project, "\n".join(lines))
        return draft, ["memory_list:%d" % len(records)], None, _evidence_from_records(records)

    def _read_target(self, message: str) -> str | None:
        quoted = re.search(r"'([^']+)'|\"([^\"]+)\"", message)
        if quoted:
            return (quoted.group(1) or quoted.group(2)).strip()
        if "readme" in message.lower():
            for base in self.policy.allowed_dirs:
                for name in ("README.md", "README.rst", "README.txt", "README"):
                    candidate = base / name
                    if candidate.is_file():
                        return str(candidate)
                    upper = base / name.upper()
                    if upper.is_file():
                        return str(upper)
            first = self.policy.allowed_dirs[0] if self.policy.allowed_dirs else Path(".")
            return str(first / "README.md")
        match = re.search(r"(?is)\bread\s+(?:the\s+)?(\S+)", message)
        if match:
            return match.group(1).strip().strip(".,:;")
        return None

    def _read_file(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        target = self._read_target(intent.arg)
        if not target:
            return (
                "Which file should I read? Name a path inside the allowed "
                "directories.",
                [],
                None,
                [],
            )
        tool = self.registry.get("read_file")
        assert tool is not None
        result = tool.invoke({"path": target}, ctx)
        if not result.ok:
            return "I could not read that: %s" % result.error, ["read_file:error"], None, []
        lines = result.output.splitlines()
        head = "\n".join(lines[:12])
        if len(lines) > 12:
            head += "\n..."
        draft = "I read %s (%d lines):\n\n%s" % (result.target, len(lines), head)
        evidence = [
            EvidenceItem(text="file head shown above", source=result.target, updated="now")
        ]
        return draft, ["read_file:ok"], None, evidence

    def _stage_write(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        tool = self.registry.get("propose_write_file")
        assert tool is not None
        result = tool.invoke({"path": intent.arg, "content": intent.arg2}, ctx)
        if not result.ok:
            return "I could not stage that write: %s" % result.error, ["write_propose:error"], None, []
        preview = intent.arg2 if len(intent.arg2) <= 300 else intent.arg2[:300] + "..."
        draft = (
            "I staged this write for your approval:\nTarget: %s\nProposal: %s\n"
            "Preview:\n%s\n\nReply 'approve %s' to write, or 'deny %s' to discard."
            % (result.target, result.proposal_id, preview, result.proposal_id, result.proposal_id)
        )
        approval = ApprovalRequest(
            action="write_file",
            target=result.target,
            proposal_id=result.proposal_id,
            preview=preview,
        )
        return draft, ["write_proposed:%s" % result.proposal_id], approval, []

    def _update_needs_content(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        tool = self.registry.get("read_file")
        assert tool is not None
        result = tool.invoke({"path": intent.arg}, ctx)
        if not result.ok and "readme" in intent.arg.lower():
            fallback = self._read_target("read " + intent.arg)
            if fallback:
                result = tool.invoke({"path": fallback}, ctx)
        if not result.ok:
            return "I could not read that: %s" % result.error, ["read_file:error"], None, []
        lines = result.output.splitlines()
        head = "\n".join(lines[:8])
        filename = result.target.rsplit("/", 1)[-1]
        draft = (
            "I read %s (%d lines). I never invent file content, so tell me "
            "the exact new content like this:\n\nsave <your content> to %s\n\n"
            "I will stage it for approval before writing anything.\n\n"
            "Current head:\n%s" % (result.target, len(lines), filename, head)
        )
        return draft, ["read_file:ok"], None, []

    def decide(
        self, project: str, proposal_id: str, approve: bool
    ) -> tuple[bool, str, str]:
        """Shared decision path for chat and API. Returns (ok, message, tag)."""
        project = project.strip() or "default"
        ctx = ToolContext(
            project=project,
            policy=self.policy,
            gate=self.gate,
            audit=self.audit,
            shell=self.policy.shell or self.config.shell,
        )
        proposal = self.gate.get(proposal_id)
        if proposal is None:
            return (
                False,
                "I have no proposal '%s'. Type 'pending' to see open ones."
                % proposal_id,
                "",
            )
        if not approve:
            try:
                self.gate.deny(proposal_id)
            except ValueError:
                return False, "Proposal '%s' is already decided." % proposal_id, ""
            self.audit.log(
                actor="user",
                project=project,
                action="deny",
                target=proposal.target,
                decision="denied",
                rationale="User denied proposal %s. Nothing ran." % proposal.id,
            )
            prefix = "run_denied:" if proposal.action == "run_shell" else "write_denied:"
            return (
                True,
                "Discarded proposal %s. Nothing was written or run." % proposal.id,
                prefix + proposal.id,
            )
        try:
            token = self.gate.approve(proposal_id)
        except ValueError:
            return False, "Proposal '%s' is already decided." % proposal_id, ""
        self.audit.log(
            actor="user",
            project=project,
            action="approve",
            target=proposal.target,
            decision="approved",
            rationale="User approved proposal %s." % proposal.id,
        )
        if proposal.action == "run_shell":
            result = execute_approved_shell(proposal.id, token, ctx)
            if not result.ok:
                return (
                    False,
                    "Approved, but the command failed: %s" % result.error,
                    "run_execute:error",
                )
            return (
                True,
                "Approved and ran the command.\n%s" % result.output,
                "run_executed:" + proposal.id,
            )
        result = execute_approved_write(proposal.id, token, ctx)
        if not result.ok:
            return (
                False,
                "Approved, but the write failed: %s" % result.error,
                "write_execute:error",
            )
        return True, "Approved and wrote %s." % result.target, "write_executed:" + proposal.id

    def _approve(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        ok, message, tag = self.decide(ctx.project, intent.arg, True)
        actions = [tag] if tag and (ok or tag.endswith(":error")) else []
        return message, actions, None, []

    def _deny(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        ok, message, tag = self.decide(ctx.project, intent.arg, False)
        actions = [tag] if tag and ok else []
        return message, actions, None, []

    def _task_add(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        try:
            task = self.memory.task_add(ctx.project, intent.arg, source="user")
        except ValueError as exc:
            return "I could not track that task: %s" % exc, [], None, []
        self.audit.log(
            actor="user",
            project=ctx.project,
            action="task_add",
            target="task:%d" % task.id,
            decision="allowed",
            rationale="Explicit task request.",
            result=task.title[:120],
        )
        draft = "Task %d tracked for %s: %s" % (task.id, ctx.project, task.title)
        return draft, ["task_add:%d" % task.id], None, []

    def _task_list(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        tasks = self.memory.task_list(ctx.project)
        if not tasks:
            return (
                "No tasks tracked for %s yet. Add one with 'todo: ...'." % ctx.project,
                ["task_list:0"],
                None,
                [],
            )
        lines = ["%d. [%s] %s" % (t.id, t.state, t.title) for t in tasks]
        draft = "Tasks for %s:\n%s" % (ctx.project, "\n".join(lines))
        return draft, ["task_list:%d" % len(tasks)], None, []

    def _task_transition(
        self, ctx: ToolContext, task_id_text: str, state: str
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        try:
            task_id = int(task_id_text)
        except ValueError:
            return "Task '%s' is not a number." % task_id_text, [], None, []
        task = self.memory.task_get(task_id)
        if task is None or task.project != ctx.project:
            return "I have no task %d in %s." % (task_id, ctx.project), [], None, []
        updated = self.memory.task_set_state(task_id, state)
        assert updated is not None
        self.audit.log(
            actor="user",
            project=ctx.project,
            action="task_state",
            target="task:%d" % task_id,
            decision="allowed",
            rationale="State moved to %s." % state,
            result=updated.title[:120],
        )
        verb = "done" if state == "done" else "in progress"
        draft = "Task %d marked %s: %s" % (task_id, verb, updated.title)
        return draft, ["task_state:%d" % task_id], None, []

    def _task_done(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        return self._task_transition(ctx, intent.arg, "done")

    def _task_start(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        return self._task_transition(ctx, intent.arg, "doing")

    def _standing_add(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        try:
            record = self.memory.add(ctx.project, "standing", intent.arg, source="user")
        except ValueError as exc:
            return "I could not save that instruction: %s" % exc, [], None, []
        self.audit.log(
            actor="user",
            project=ctx.project,
            action="memory_add",
            target="memory:%d" % record.id,
            decision="allowed",
            rationale="Standing instruction saved.",
            result=record.text[:120],
        )
        draft = "Standing instruction saved for %s. I will apply it in our chats." % (
            ctx.project
        )
        return draft, ["memory_add:%d" % record.id], None, _evidence_from_records([record])

    def _standing(self, project: str) -> list[MemoryRecord]:
        records = self.memory.list_project(project, limit=50)
        return [r for r in records if r.kind == "standing"]

    def _list_dir(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        tool = self.registry.get("list_dir")
        assert tool is not None
        result = tool.invoke({"path": intent.arg or "."}, ctx)
        if not result.ok:
            return "I could not list that: %s" % result.error, ["list_dir:error"], None, []
        return result.output, ["list_dir:ok"], None, []

    def _search_files(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        tool = self.registry.get("search_files")
        assert tool is not None
        result = tool.invoke(
            {"pattern": intent.arg, "path": intent.arg2 or "."}, ctx
        )
        if not result.ok:
            return "Search failed: %s" % result.error, ["search_files:error"], None, []
        return result.output, ["search_files:ok"], None, []

    def _fetch_url(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        tool = self.registry.get("fetch_url")
        assert tool is not None
        result = tool.invoke({"url": intent.arg}, ctx)
        if not result.ok:
            return "I could not fetch that: %s" % result.error, ["fetch_url:error"], None, []
        evidence = [EvidenceItem(text="fetched page shown above", source=intent.arg, updated="now")]
        return result.output, ["fetch_url:ok"], None, evidence

    def _run_shell(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        tool = self.registry.get("run_shell")
        assert tool is not None
        result = tool.invoke({"command": intent.arg}, ctx)
        if not result.ok:
            return (
                "I could not stage that command: %s" % result.error,
                ["run_propose:error"],
                None,
                [],
            )
        preview = intent.arg if len(intent.arg) <= 300 else intent.arg[:300] + "..."
        draft = (
            "I staged this command for your approval:\n%s\nProposal: %s\n"
            "Reply 'approve %s' to run, or 'deny %s' to discard."
            % (intent.arg, result.proposal_id, result.proposal_id, result.proposal_id)
        )
        approval = ApprovalRequest(
            action="run_shell",
            target=result.target,
            proposal_id=result.proposal_id,
            preview=preview,
        )
        return draft, ["run_proposed:%s" % result.proposal_id], approval, []

    def _pending(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        items = self.gate.pending(ctx.project)
        if not items:
            return "No pending proposals for %s." % ctx.project, [], None, []
        lines = ["%s: %s %s" % (p.id, p.action, p.target) for p in items]
        return "Pending for %s:\n%s" % (ctx.project, "\n".join(lines)), [], None, []

    def _help(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        specs = self.registry.describe()
        tool_lines = [
            "- %s (%s): %s" % (s.name, "read only" if s.read_only else "needs approval", s.description)
            for s in specs
        ]
        draft = (
            "I can do this for project %s:\n\n"
            "remember <fact> : save a memory\n"
            "correction: <fix> : override an older note\n"
            "what did we decide about X? : recall with sources\n"
            "show me what is saved : list project memories\n"
            "read <path> : read a file inside allowed directories\n"
            "save <content> to <path> : stage a write for approval\n"
            "update <path> : read it, then I ask for exact content\n"
            "approve <id> / deny <id> : decide a staged write\n"
            "pending : list open proposals\n"
            "todo: <item> : track a task, 'done <id>' finishes it\n"
            "tasks : list tracked tasks\n"
            "standing: <rule> : save an always on instruction\n"
            "list [dir] : list a directory in scope\n"
            "search <pattern> : find files by name\n"
            "fetch <url> : fetch a page as text\n"
            "run <command> : stage an allowlisted shell command\n\nTools:\n%s"
            % (ctx.project, "\n".join(tool_lines))
        )
        return draft, [], None, []

    def _chat(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        hits = self.memory.recall(ctx.project, intent.arg, limit=3)
        standing = self._standing(ctx.project)
        prefix = ""
        if standing:
            prefix = "Standing instructions:\n" + "\n".join(
                "- %s" % record.text for record in standing
            ) + "\n\n"
        evidence = _evidence_from_hits(hits) + _evidence_from_records(standing)
        if hits:
            lines = ["- %s" % hit.record.text for hit in hits]
            draft = prefix + (
                "Related context for %s:\n%s\n\nTell me more, or type 'help' "
                "to see what I can do." % (ctx.project, "\n".join(lines))
            )
            return draft, ["chat:context"], None, evidence
        draft = prefix + (
            "I am Mitra, working on project %s. I remember facts, read files "
            "in scope, and stage writes for your approval. Type 'help' to see "
            "examples." % ctx.project
        )
        return draft, ["chat:help"], None, evidence


def build_orchestrator(
    config: MitraConfig | None = None,
    provider: ModelProvider | None = None,
    memory: MemoryStore | None = None,
    audit: AuditLog | None = None,
    registry: ToolRegistry | None = None,
    gate: ApprovalGate | None = None,
    policy: Policy | None = None,
) -> Orchestrator:
    """Assemble an orchestrator, filling gaps from config. Tests inject fakes."""
    from .providers import build_provider

    cfg = config or default_config()
    db_path = cfg.resolve_db_path()
    return Orchestrator(
        config=cfg,
        provider=provider or build_provider(cfg),
        memory=memory or MemoryStore(db_path),
        audit=audit or AuditLog(db_path),
        registry=registry or default_registry(),
        gate=gate or ApprovalGate(db_path, ttl_seconds=cfg.approvals.ttl_seconds),
        policy=policy or Policy(cfg.resolve_allowed_dirs(), shell=cfg.shell),
    )


def build_test_orchestrator(
    tmp_dir: str | Path, shell: ShellConfig | None = None
) -> Orchestrator:
    """In memory style orchestrator for tests. No network, no keys."""
    base = Path(tmp_dir)
    db_path = base / "test.db"
    return Orchestrator(
        config=default_config(base),
        provider=MockProvider(),
        memory=MemoryStore(db_path),
        audit=AuditLog(db_path),
        registry=default_registry(),
        gate=ApprovalGate(db_path),
        policy=Policy([base], shell=shell),
    )
