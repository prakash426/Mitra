"""Core orchestration loop. Transparent, typed, and testable.

One pass per message: parse intent, gather evidence, check policy, act or
pause for approval, audit, then render a conversational reply. Rule based
intent parsing ships first; model driven planning is a later gated upgrade.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import MitraConfig, ShellConfig, default_config
from .memory.store import VALID_KINDS, MemoryHit, MemoryRecord, MemoryStore
from .planning import (
    PlanError,
    PlanRecord,
    PlanStep,
    PlanStore,
    looks_like_plan,
    request_plan,
)
from .memory.vectors import (
    EmbeddingError,
    OllamaEmbeddingBackend,
    VectorIndex,
    embed_record,
    recall_hybrid,
)
from .tools.shell_tool import execute_approved_shell
from .providers.base import DRAFT_MARKER, ModelProvider, ProviderError
from .providers.mock import MockProvider
from .security.audit import AuditLog
from .security.policy import ApprovalGate, Policy
from .tools.base import ToolContext
from .mcp import McpError, list_tools as mcp_list_tools
from .tools.file_tools import execute_approved_write
from .tools.github_tools import execute_approved_pr
from .tools.mcp_tools import McpTool, execute_approved_mcp, mcp_tool_name
from .tools.registry import ToolRegistry, default_registry

RENDER_INSTRUCTIONS = (
    "You are Mitra, a personal AI companion talking with someone you know "
    "well. Rephrase the draft below conversationally. Stick to the facts "
    "in the evidence. Never invent decisions, files, or events. Keep "
    "sources visible. Output only the answer itself: no greeting, no "
    "preamble, no announcement that you rephrased anything, no stock "
    "openers. Just continue the conversation.\n\n" + DRAFT_MARKER + "\n"
)

OPEN_CHAT_INSTRUCTIONS = (
    "You are Mitra, a personal AI companion in an ongoing conversation "
    "with someone you know well. Answer the user directly and helpfully. "
    "Apply the standing instructions. Use the memory context when relevant "
    "and cite sources for remembered facts. For general questions with no "
    "stored facts, answer from your own knowledge. Do not greet unless "
    "they greet you. Never open with stock phrases or announcements. Just "
    "continue the conversation naturally."
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
    arg3: str = ""
    arg4: str = ""


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


MAX_IMPORT_RECORDS = 200


def _parse_import_file(target: str, content: str) -> list[dict[str, str]]:
    """Parse user supplied import data. JSON stays structured, plain text
    becomes one note per non empty line. Raises ValueError when unreadable."""
    suffix = target.rsplit(".", 1)[-1].lower() if "." in target else ""
    records: list[dict[str, str]] = []
    if suffix == "json":
        try:
            data = json.loads(content)
        except ValueError as exc:
            raise ValueError("invalid JSON: %s." % exc)
        items = data.get("memories", data) if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise ValueError("JSON must hold a list or a memories list object.")
        for item in items:
            if isinstance(item, str):
                records.append({"kind": "note", "text": item.strip()})
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                kind = str(item.get("kind", "note")).strip() or "note"
                if kind not in VALID_KINDS:
                    raise ValueError("unknown kind '%s'." % kind)
                records.append({"kind": kind, "text": item["text"].strip()})
            else:
                raise ValueError("each item needs a text field.")
    else:
        for line in content.splitlines():
            cleaned = line.strip().lstrip("-*").strip()
            if cleaned:
                records.append({"kind": "note", "text": cleaned})
    return [r for r in records if r["text"]][:MAX_IMPORT_RECORDS]


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
    match = re.match(r"(?is)^\s*import\s+(?:memories\s+)?from\s+(.+?)\s*$", text)
    if match:
        target = match.group(1).strip().strip("\"'")
        return _Intent(kind="import_memories", arg=target)
    match = re.match(
        r"(?is)^\s*github\s+(?:read|show|get)\s+(\S+)\s+(\S+?)(?:\s+at\s+(\S+))?\s*$",
        text,
    )
    if match:
        return _Intent(
            kind="github_read",
            arg=match.group(1).strip(),
            arg2=match.group(2).strip(),
            arg3=(match.group(3) or "").strip(),
        )
    match = re.match(r"(?is)^\s*github\s+(?:prs|pulls)\s+(\S+)(?:\s+(open|closed|all))?\s*$", text)
    if match:
        return _Intent(
            kind="github_prs",
            arg=match.group(1).strip(),
            arg2=(match.group(2) or "").strip(),
        )
    match = re.match(
        r"(?is)^\s*github\s+open\s+pr\s+(\S+)\s+from\s+(\S+)\s+to\s+(\S+)\s+titled\s+(.+)$",
        text,
    )
    if match:
        return _Intent(
            kind="github_open_pr",
            arg=match.group(1).strip(),
            arg2=match.group(2).strip(),
            arg3=match.group(3).strip(),
            arg4=match.group(4).strip(),
        )
    match = re.match(r"(?is)^\s*continue\s+(plan-\d+)\s*$", text)
    if match:
        return _Intent(kind="plan_continue", arg=match.group(1).lower())
    if re.match(r"(?is)^\s*plans\s*$", text):
        return _Intent(kind="plan_list")
    match = re.match(r"(?is)^\s*abandon\s+(plan-\d+)\s*$", text)
    if match:
        return _Intent(kind="plan_abandon", arg=match.group(1).lower())
    match = re.match(r"(?is)^\s*compare\s+(.+?)\s+across\s+(.+?)\s*$", text)
    if match:
        return _Intent(
            kind="synthesis",
            arg=match.group(1).strip(),
            arg2=match.group(2).strip(),
        )
    match = re.match(r"(?is)^\s*mcp\s+(\S+)\s+(\S+)(?:\s+(.+))?\s*$", text)
    if match:
        return _Intent(
            kind="mcp_call",
            arg=match.group(1).strip(),
            arg2=match.group(2).strip(),
            arg3=(match.group(3) or "").strip(),
        )
    if any(
        marker in lowered
        for marker in (
            "calendar",
            "upcoming events",
            "my schedule",
            "what is on",
            "whats on",
        )
    ):
        days = ""
        day_match = re.search(r"(\d+)\s*days?", lowered)
        if day_match:
            days = day_match.group(1)
        return _Intent(kind="calendar_next", arg=days)
    if any(
        marker in lowered
        for marker in (
            "inbox",
            "recent mail",
            "recent email",
            "my mail",
            "my email",
            "check mail",
        )
    ):
        return _Intent(kind="mail_recent")

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
        embedding=None,
        vindex=None,
        planner: PlanStore | None = None,
    ) -> None:
        self.config = config
        self.provider = provider
        self.memory = memory
        self.audit = audit
        self.registry = registry
        self.gate = gate
        self.policy = policy
        self.embedding = embedding
        self.vindex = vindex
        self.planner = planner

    def close(self) -> None:
        self.memory.close()
        self.audit.close()
        self.gate.close()
        if self.vindex is not None:
            self.vindex.close()
        if self.planner is not None:
            self.planner.close()

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
            fetch_allowed_hosts=self.config.tools.fetch_allowed_hosts,
            fetch_private_hosts=self.config.tools.fetch_private_hosts,
            mcp_servers=self.config.mcp_servers,
        )
        if (
            intent.kind == "chat"
            and self.planner is not None
            and self.config.orchestrator.planning
            and self.provider.can_reason
            and looks_like_plan(message)
        ):
            return self._plan_message(ctx, message)
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
            "import_memories": self._import_memories,
            "github_read": self._github_read,
            "github_prs": self._github_prs,
            "github_open_pr": self._github_open_pr,
            "calendar_next": self._calendar_next,
            "mail_recent": self._mail_recent,
            "stage_write": self._stage_write,
            "update_needs_content": self._update_needs_content,
            "approve": self._approve,
            "deny": self._deny,
            "pending": self._pending,
            "plan_continue": self._plan_continue,
            "plan_list": self._plan_list,
            "plan_abandon": self._plan_abandon,
            "synthesis": self._synthesis_stage,
            "mcp_call": self._mcp_call,
            "help": self._help,
            "chat": self._chat,
        }.get(intent.kind, self._chat)
        draft, actions, approval, evidence = handler(ctx, intent)
        text = self._render(ctx, intent, message, draft, evidence)
        return OrchestratorResponse(
            text=text, actions=actions, approval_request=approval, evidence=evidence
        )

    def _open_prompt(self, message: str, evidence: list[EvidenceItem]) -> str:
        lines = [
            "- %s (source: %s, updated %s)" % (e.text, e.source, e.updated)
            for e in evidence
        ]
        return (
            OPEN_CHAT_INSTRUCTIONS
            + "\n\nMEMORY CONTEXT:\n"
            + ("\n".join(lines) if lines else "(none stored)")
            + "\n\nUSER:\n"
            + message
        )

    def _render(
        self,
        ctx: ToolContext,
        intent: _Intent,
        message: str,
        draft: str,
        evidence: list[EvidenceItem],
    ) -> str:
        open_answer = intent.kind == "chat" and self.provider.can_reason
        if open_answer:
            prompt: str = self._open_prompt(message, evidence)
            context = ""
        else:
            context_lines = [
                "- %s (source: %s, updated %s)" % (e.text, e.source, e.updated)
                for e in evidence
            ]
            prompt = RENDER_INSTRUCTIONS + draft
            context = "\n".join(context_lines)
        try:
            generation = self.provider.generate(prompt, context)
            if open_answer and not generation.text.strip():
                return draft
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

    def _hybrid(self, project: str, query: str, limit: int) -> list[MemoryHit]:
        if self.embedding is None or self.vindex is None:
            return self.memory.recall(project, query, limit=limit)
        return recall_hybrid(
            self.memory, self.vindex, self.embedding, project, query, limit=limit
        )

    def _embed_best_effort(self, record: MemoryRecord) -> None:
        if self.embedding is None or self.vindex is None:
            return
        try:
            embed_record(self.vindex, self.embedding, record)
        except EmbeddingError:
            pass

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
        self._embed_best_effort(record)
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
        self._embed_best_effort(record)
        draft = "Correction saved for %s. It now overrides older notes on '%s'." % (
            ctx.project,
            topic or "this topic",
        )
        return draft, ["memory_correct:%d" % record.id], None, _evidence_from_records([record])

    def _recall(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        hits = self._hybrid(ctx.project, intent.arg, limit=5)
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
            fetch_allowed_hosts=self.config.tools.fetch_allowed_hosts,
            fetch_private_hosts=self.config.tools.fetch_private_hosts,
            mcp_servers=self.config.mcp_servers,
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
        if proposal.action == "github_open_pr":
            result = execute_approved_pr(proposal.id, token, ctx)
            if not result.ok:
                return (
                    False,
                    "Approved, but GitHub failed: %s" % result.error,
                    "pr_execute:error",
                )
            return True, "Approved. %s" % result.output, "pr_executed:" + proposal.id
        if proposal.action == "memory_import":
            return self._execute_import(ctx, proposal, token)
        if proposal.action == "cross_project":
            return self._execute_synthesis(ctx, proposal, token)
        if proposal.action == "mcp_call":
            result = execute_approved_mcp(proposal.id, token, ctx)
            if not result.ok:
                return (
                    False,
                    "Approved, but the MCP call failed: %s" % result.error,
                    "mcp_execute:error",
                )
            return (
                True,
                "Approved and ran MCP %s.\n%s" % (result.target, result.output),
                "mcp_executed:" + proposal.id,
            )
        if proposal.action == "write_file":
            result = execute_approved_write(proposal.id, token, ctx)
            if not result.ok:
                return (
                    False,
                    "Approved, but the write failed: %s" % result.error,
                    "write_execute:error",
                )
            return (
                True,
                "Approved and wrote %s." % result.target,
                "write_executed:" + proposal.id,
            )
        return (
            False,
            "Unknown proposal action '%s'." % proposal.action,
            "execute:error",
        )

    def _execute_import(
        self, ctx: ToolContext, proposal, token: str
    ) -> tuple[bool, str, str]:
        if not self.gate.consume(proposal.id, token):
            self.audit.log(
                actor="user",
                project=ctx.project,
                action="memory_import",
                target=proposal.target,
                decision="denied",
                rationale="Approval token invalid, already used, or not approved.",
            )
            return (
                False,
                "Approval missing or already used for '%s'." % proposal.id,
                "import_execute:error",
            )
        try:
            items = json.loads(proposal.details)
        except ValueError:
            return False, "Import data is unreadable.", "import_execute:error"
        source = "import:" + proposal.target.rsplit("/", 1)[-1]
        count = 0
        for item in items:
            try:
                record = self.memory.add(
                    proposal.project,
                    item.get("kind", "note"),
                    item.get("text", ""),
                    source=source,
                )
            except (ValueError, AttributeError):
                continue
            count += 1
            if count <= 100:
                self._embed_best_effort(record)
        self.audit.log(
            actor="user",
            project=proposal.project,
            action="memory_import",
            target=proposal.target,
            decision="allowed",
            rationale="Proposal %s approved and executed." % proposal.id,
            result="%d records imported" % count,
        )
        return (
            True,
            "Approved and imported %d records into %s." % (count, proposal.project),
            "import_executed:" + proposal.id,
        )

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
        self._embed_best_effort(record)
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

    def _import_memories(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        tool = self.registry.get("read_file")
        assert tool is not None
        result = tool.invoke({"path": intent.arg}, ctx)
        if not result.ok:
            return (
                "I could not read that import file: %s" % result.error,
                ["import:error"],
                None,
                [],
            )
        try:
            records = _parse_import_file(result.target, result.output)
        except ValueError as exc:
            return (
                "I could not parse that import file: %s" % exc,
                ["import:error"],
                None,
                [],
            )
        if not records:
            return "That file holds no importable records.", ["import:empty"], None, []
        proposal = self.gate.stage(
            action="memory_import",
            target=result.target,
            details=json.dumps(records),
            project=ctx.project,
        )
        self.audit.log(
            actor="user",
            project=ctx.project,
            action="memory_import",
            target=result.target,
            decision="approval_requested",
            rationale="Proposal %s staged." % proposal.id,
            result="%d records proposed" % len(records),
        )
        preview_lines = ["[%s] %s" % (r["kind"], r["text"][:80]) for r in records[:5]]
        if len(records) > 5:
            preview_lines.append("... and %d more" % (len(records) - 5))
        preview = "\n".join(preview_lines)
        draft = (
            "I found %d records in %s. Proposal %s is staged.\nPreview:\n%s\n\n"
            "Reply 'approve %s' to import, or 'deny %s' to discard."
            % (
                len(records),
                result.target,
                proposal.id,
                preview,
                proposal.id,
                proposal.id,
            )
        )
        approval = ApprovalRequest(
            action="memory_import",
            target=result.target,
            proposal_id=proposal.id,
            preview=preview,
        )
        return draft, ["import_proposed:%s" % proposal.id], approval, []

    def _github_read(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        tool = self.registry.get("github_read_file")
        assert tool is not None
        result = tool.invoke(
            {"repo": intent.arg, "path": intent.arg2, "ref": intent.arg3}, ctx
        )
        if not result.ok:
            return (
                "I could not read that repo file: %s" % result.error,
                ["github_read:error"],
                None,
                [],
            )
        evidence = [
            EvidenceItem(
                text="repo file shown above",
                source="%s:%s" % (intent.arg, intent.arg2),
                updated="now",
            )
        ]
        return result.output, ["github_read:ok"], None, evidence

    def _github_prs(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        tool = self.registry.get("github_list_prs")
        assert tool is not None
        result = tool.invoke({"repo": intent.arg, "state": intent.arg2 or "open"}, ctx)
        if not result.ok:
            return (
                "I could not list pull requests: %s" % result.error,
                ["github_prs:error"],
                None,
                [],
            )
        return result.output, ["github_prs:ok"], None, []

    def _github_open_pr(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        tool = self.registry.get("github_open_pr")
        assert tool is not None
        result = tool.invoke(
            {
                "repo": intent.arg,
                "title": intent.arg4,
                "head": intent.arg2,
                "base": intent.arg3,
                "body": "",
            },
            ctx,
        )
        if not result.ok:
            return (
                "I could not stage that pull request: %s" % result.error,
                ["pr_propose:error"],
                None,
                [],
            )
        preview = "%s (%s into %s)" % (intent.arg4, intent.arg2, intent.arg3)
        draft = (
            "I staged this pull request for your approval:\n%s\nProposal: %s\n"
            "Reply 'approve %s' to open it, or 'deny %s' to discard."
            % (
                preview,
                result.proposal_id,
                result.proposal_id,
                result.proposal_id,
            )
        )
        approval = ApprovalRequest(
            action="github_open_pr",
            target=result.target,
            proposal_id=result.proposal_id,
            preview=preview,
        )
        return draft, ["pr_proposed:%s" % result.proposal_id], approval, []

    def _calendar_next(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        tool = self.registry.get("calendar_next")
        assert tool is not None
        result = tool.invoke({"days": intent.arg or 7}, ctx)
        if not result.ok:
            return (
                "I could not check the calendar: %s" % result.error,
                ["calendar:error"],
                None,
                [],
            )
        return result.output, ["calendar:ok"], None, []

    def _mail_recent(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        tool = self.registry.get("mail_recent")
        assert tool is not None
        result = tool.invoke({}, ctx)
        if not result.ok:
            return (
                "I could not check mail: %s" % result.error,
                ["mail:error"],
                None,
                [],
            )
        return result.output, ["mail:ok"], None, []

    def _pending(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        items = self.gate.pending(ctx.project)
        lines = ["%s: %s %s" % (p.id, p.action, p.target) for p in items]
        if self.planner is not None:
            for plan in self.planner.active(ctx.project):
                done = sum(1 for s in plan.steps if s.done)
                lines.append(
                    "%s: plan %s (%d of %d steps done)"
                    % (plan.id, plan.goal[:50], done, len(plan.steps))
                )
        if not lines:
            return "No pending proposals for %s." % ctx.project, [], None, []
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
            "run <command> : stage an allowlisted shell command\n"
            "import memories from <file> : preview and stage an import\n"
            "github read <repo> <path> : read a repo file\n"
            "github prs <repo> : list pull requests\n"
            "github open pr <repo> from <head> to <base> titled <title>\n"
            "calendar : upcoming events\n"
            "check mail : recent inbox subjects\n"
            "<do X and then Y> : model planned multi step task\n"
            "continue <plan-id> / abandon <plan-id> : resume or drop a plan\n"
            "plans : list active plans\n"
            "compare <topic> across <A> and <B> : consented comparison\n"
            "mcp <server> <tool> [json] : call an MCP tool\n\nTools:\n%s"
            % (ctx.project, "\n".join(tool_lines))
        )
        return draft, [], None, []

    def _plan_message(self, ctx: ToolContext, message: str) -> OrchestratorResponse:
        """Planning intercept. Any planning failure falls back to chat."""
        assert self.planner is not None
        chat_intent = _Intent(kind="chat", arg=message)
        try:
            steps = request_plan(self.provider, message, self.registry.describe())
        except PlanError as exc:
            self.audit.log(
                actor="system",
                project=ctx.project,
                action="plan_rejected",
                target="planner",
                decision="error",
                rationale="Falling back to chat: %s" % exc,
            )
            draft, actions, approval, evidence = self._chat(ctx, chat_intent)
            text = self._render(ctx, chat_intent, message, draft, evidence)
            return OrchestratorResponse(
                text=text, actions=actions, approval_request=approval, evidence=evidence
            )
        plan = self.planner.create(ctx.project, message[:200], steps)
        self.audit.log(
            actor="system",
            project=ctx.project,
            action="plan_created",
            target=plan.id,
            decision="allowed",
            rationale="%d steps planned." % len(steps),
        )
        draft, actions, approval, evidence = self._run_plan_steps(ctx, plan)
        return self._finish_plan(ctx, message, draft, actions, approval, evidence)

    def _finish_plan(
        self,
        ctx: ToolContext,
        message: str,
        draft: str,
        actions: list[str],
        approval: ApprovalRequest | None,
        evidence: list[EvidenceItem],
    ) -> OrchestratorResponse:
        intent = _Intent(kind="plan")
        text = self._render(ctx, intent, message, draft, evidence)
        return OrchestratorResponse(
            text=text, actions=actions, approval_request=approval, evidence=evidence
        )

    def _run_plan_steps(
        self, ctx: ToolContext, plan: PlanRecord
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        """Execute undone steps. Read steps run at once; the first staged
        mutation pauses the plan for approval."""
        assert self.planner is not None
        steps = list(plan.steps)
        evidence: list[EvidenceItem] = []
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for index, step in enumerate(steps):
            if step.done:
                if step.result and not step.result.startswith("staged:"):
                    evidence.append(
                        EvidenceItem(
                            text=step.result[:500],
                            source="tool:" + step.tool,
                            updated=now,
                        )
                    )
                continue
            if step.result.startswith("staged:"):
                return self._resume_staged(ctx, plan, steps, index, evidence)
            tool = self.registry.get(step.tool)
            if tool is None:
                self.planner.finish(plan.id, "abandoned")
                draft = "Plan %s stopped: tool '%s' is not available." % (
                    plan.id,
                    step.tool,
                )
                self.audit.log(
                    actor="system",
                    project=ctx.project,
                    action="plan_abandoned",
                    target=plan.id,
                    decision="error",
                    rationale=draft,
                )
                return draft, ["plan_abandoned:" + plan.id], None, evidence
            result = tool.invoke(step.args, ctx)
            if not result.ok:
                self.planner.finish(plan.id, "abandoned")
                draft = "Plan %s stopped at step %d (%s): %s" % (
                    plan.id,
                    index + 1,
                    step.tool,
                    result.error,
                )
                self.audit.log(
                    actor="system",
                    project=ctx.project,
                    action="plan_abandoned",
                    target=plan.id,
                    decision="error",
                    rationale=draft,
                )
                return draft, ["plan_abandoned:" + plan.id], None, evidence
            if result.proposal_id:
                steps[index] = PlanStep(
                    tool=step.tool,
                    args=step.args,
                    done=False,
                    result="staged:" + result.proposal_id,
                )
                self.planner.save_steps(plan.id, tuple(steps))
                self.audit.log(
                    actor="system",
                    project=ctx.project,
                    action="plan_paused",
                    target=plan.id,
                    decision="approval_requested",
                    rationale="Step %d staged %s." % (index + 1, result.proposal_id),
                )
                draft = (
                    "Plan %s paused at step %d: %s needs your approval.\n"
                    "Proposal %s is staged. Approve it, then say "
                    "'continue %s'." % (plan.id, index + 1, step.tool, result.proposal_id, plan.id)
                )
                approval = ApprovalRequest(
                    action="plan_step",
                    target=result.target or step.tool,
                    proposal_id=result.proposal_id,
                    preview=(result.output or step.tool)[:300],
                )
                return draft, ["plan_paused:" + plan.id], approval, evidence
            steps[index] = PlanStep(
                tool=step.tool, args=step.args, done=True, result=result.output[:1000]
            )
            evidence.append(
                EvidenceItem(
                    text=result.output[:500] or "(no output)",
                    source="tool:" + step.tool,
                    updated=now,
                )
            )
            self.audit.log(
                actor="system",
                project=ctx.project,
                action="plan_step",
                target=plan.id,
                decision="allowed",
                rationale="Step %d (%s) ran read only." % (index + 1, step.tool),
            )
        self.planner.save_steps(plan.id, tuple(steps))
        self.planner.finish(plan.id, "done")
        self.audit.log(
            actor="system",
            project=ctx.project,
            action="plan_done",
            target=plan.id,
            decision="allowed",
            rationale="%d steps finished." % len(steps),
        )
        lines = [
            "%d. %s: %s" % (i + 1, s.tool, (s.result or "(no output)")[:200])
            for i, s in enumerate(steps)
        ]
        draft = "Plan %s finished in %d steps:\n%s" % (plan.id, len(steps), "\n".join(lines))
        return draft, ["plan_done:" + plan.id], None, evidence

    def _resume_staged(
        self, ctx: ToolContext, plan: PlanRecord, steps: list[PlanStep], index: int,
        evidence: list[EvidenceItem],
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        """Recheck a step that paused for approval before continuing."""
        assert self.planner is not None
        step = steps[index]
        proposal_id = step.result[len("staged:"):]
        proposal = self.gate.get(proposal_id)
        if proposal is None:
            self.planner.finish(plan.id, "abandoned")
            draft = "Plan %s stopped: proposal %s expired or vanished." % (
                plan.id,
                proposal_id,
            )
            return draft, ["plan_abandoned:" + plan.id], None, evidence
        if proposal.status == "pending":
            draft = (
                "Plan %s is still waiting on proposal %s. Approve it, then "
                "say 'continue %s'." % (plan.id, proposal_id, plan.id)
            )
            approval = ApprovalRequest(
                action="plan_step",
                target=proposal.target,
                proposal_id=proposal_id,
                preview=proposal.details[:300],
            )
            return draft, ["plan_paused:" + plan.id], approval, evidence
        if proposal.status != "approved":
            self.planner.finish(plan.id, "abandoned")
            draft = "Plan %s stopped: proposal %s was %s. Nothing further ran." % (
                plan.id,
                proposal_id,
                proposal.status,
            )
            self.audit.log(
                actor="system",
                project=ctx.project,
                action="plan_abandoned",
                target=plan.id,
                decision="denied",
                rationale=draft,
            )
            return draft, ["plan_abandoned:" + plan.id], None, evidence
        steps[index] = PlanStep(tool=step.tool, args=step.args, done=True, result="")
        self.planner.save_steps(plan.id, tuple(steps))
        resumed = PlanRecord(
            id=plan.id,
            project=plan.project,
            goal=plan.goal,
            steps=tuple(steps),
            status=plan.status,
            created_at=plan.created_at,
        )
        return self._run_plan_steps(ctx, resumed)

    def _plan_continue(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        if self.planner is None:
            return "Planning is not available here.", [], None, []
        plan = self.planner.get(intent.arg)
        if plan is None or plan.status != "active":
            return "I have no active %s." % intent.arg, [], None, []
        if plan.project != ctx.project:
            return (
                "Plan %s belongs to project %s, not %s."
                % (plan.id, plan.project, ctx.project),
                [],
                None,
                [],
            )
        return self._run_plan_steps(ctx, plan)

    def _plan_list(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        if self.planner is None:
            return "Planning is not available here.", [], None, []
        plans = self.planner.active(ctx.project)
        if not plans:
            return "No active plans for %s." % ctx.project, [], None, []
        lines = []
        for plan in plans:
            done = sum(1 for s in plan.steps if s.done)
            lines.append(
                "%s: %s (%d of %d steps done)"
                % (plan.id, plan.goal[:60], done, len(plan.steps))
            )
        return "Active plans for %s:\n%s" % (ctx.project, "\n".join(lines)), [], None, []

    def _plan_abandon(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        if self.planner is None:
            return "Planning is not available here.", [], None, []
        plan = self.planner.get(intent.arg)
        if plan is None or plan.status != "active":
            return "I have no active %s." % intent.arg, [], None, []
        if plan.project != ctx.project:
            return (
                "Plan %s belongs to project %s, not %s."
                % (plan.id, plan.project, ctx.project),
                [],
                None,
                [],
            )
        self.planner.finish(plan.id, "abandoned")
        self.audit.log(
            actor="user",
            project=ctx.project,
            action="plan_abandoned",
            target=plan.id,
            decision="denied",
            rationale="User abandoned the plan.",
        )
        return "Abandoned %s. Finished steps stay finished." % plan.id, [], None, []

    def _synthesis_stage(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        projects = [
            part.strip()
            for part in re.split(r",|\band\b", intent.arg2)
            if part.strip()
        ]
        projects = [p for p in projects if p.lower() not in ("across",)]
        problems = [
            p
            for p in projects
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.-]*", p)
        ]
        if problems or not 2 <= len(projects) <= 4 or len(set(projects)) != len(projects):
            return (
                "Name two to four distinct projects, like "
                "'compare deploy steps across CancerLens and Demo'.",
                ["synthesis:error"],
                None,
                [],
            )
        spec = {"topic": intent.arg[:200], "projects": projects}
        proposal = self.gate.stage(
            action="cross_project",
            target=",".join(projects),
            details=json.dumps(spec),
            project=ctx.project,
        )
        self.audit.log(
            actor="user",
            project=ctx.project,
            action="cross_project",
            target=proposal.target,
            decision="approval_requested",
            rationale="Proposal %s staged." % proposal.id,
            result="consent to read: %s" % proposal.target,
        )
        draft = (
            "Comparing '%s' across %s reads memories outside %s, so I need "
            "explicit consent. Proposal %s is staged.\nReply 'approve %s' "
            "to compare, or 'deny %s' to discard."
            % (
                spec["topic"],
                ", ".join(projects),
                ctx.project,
                proposal.id,
                proposal.id,
                proposal.id,
            )
        )
        approval = ApprovalRequest(
            action="cross_project",
            target=proposal.target,
            proposal_id=proposal.id,
            preview="compare '%s' across %s" % (spec["topic"], ", ".join(projects)),
        )
        return draft, ["synthesis_proposed:%s" % proposal.id], approval, []

    def _execute_synthesis(
        self, ctx: ToolContext, proposal, token: str
    ) -> tuple[bool, str, str]:
        if not self.gate.consume(proposal.id, token):
            self.audit.log(
                actor="user",
                project=ctx.project,
                action="cross_project",
                target=proposal.target,
                decision="denied",
                rationale="Approval token invalid, already used, or not approved.",
            )
            return (
                False,
                "Approval missing or already used for '%s'." % proposal.id,
                "synthesis_execute:error",
            )
        try:
            spec = json.loads(proposal.details)
            topic = str(spec.get("topic", "")).strip()
            projects = [str(p) for p in spec.get("projects", [])]
        except (ValueError, AttributeError):
            return False, "Comparison data is unreadable.", "synthesis_execute:error"
        if not topic or not 2 <= len(projects) <= 4:
            return False, "Comparison data is unreadable.", "synthesis_execute:error"
        sections = []
        total = 0
        for name in projects:
            hits = self._hybrid(name, topic, limit=5)
            total += len(hits)
            if not hits:
                sections.append("**%s**: nothing saved on this topic." % name)
                continue
            lines = [
                "- %s (source: %s, updated %s)"
                % (hit.record.text[:200], hit.record.source, hit.record.updated_at)
                for hit in hits
            ]
            sections.append("**%s**:\n%s" % (name, "\n".join(lines)))
        self.audit.log(
            actor="user",
            project=ctx.project,
            action="cross_project",
            target=proposal.target,
            decision="allowed",
            rationale="Proposal %s approved and executed." % proposal.id,
            result="%d records compared" % total,
        )
        draft = "Comparing '%s' (consented %s):\n\n%s" % (
            topic,
            proposal.id,
            "\n\n".join(sections),
        )
        return True, draft, "synthesis_executed:" + proposal.id

    def _mcp_call(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        tool = self.registry.get(mcp_tool_name(intent.arg, intent.arg2))
        if tool is None:
            return (
                "I have no MCP tool '%s' on server '%s'." % (intent.arg2, intent.arg),
                ["mcp:error"],
                None,
                [],
            )
        if intent.arg3:
            try:
                call_args = json.loads(intent.arg3)
            except ValueError:
                return (
                    "MCP arguments must be a JSON object.",
                    ["mcp:error"],
                    None,
                    [],
                )
            if not isinstance(call_args, dict):
                return "MCP arguments must be a JSON object.", ["mcp:error"], None, []
        else:
            call_args = {}
        result = tool.invoke(call_args, ctx)
        if not result.ok:
            return "The MCP call failed: %s" % result.error, ["mcp:error"], None, []
        if result.proposal_id:
            draft = (
                "I staged this MCP call for your approval:\n%s\nProposal: %s\n"
                "Reply 'approve %s' to run, or 'deny %s' to discard."
                % (
                    result.target,
                    result.proposal_id,
                    result.proposal_id,
                    result.proposal_id,
                )
            )
            approval = ApprovalRequest(
                action="mcp_call",
                target=result.target,
                proposal_id=result.proposal_id,
                preview=result.output[:300],
            )
            return draft, ["mcp_proposed:%s" % result.proposal_id], approval, []
        return result.output, ["mcp:ok"], None, []

    def _chat(
        self, ctx: ToolContext, intent: _Intent
    ) -> tuple[str, list[str], ApprovalRequest | None, list[EvidenceItem]]:
        hits = self._hybrid(ctx.project, intent.arg, limit=3)
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
    embedding = None
    vindex = None
    if cfg.memory.embeddings == "ollama":
        embedding = OllamaEmbeddingBackend(
            model=cfg.memory.embed_model, base_url=cfg.memory.embed_base_url
        )
        vindex = VectorIndex(db_path)
    audit_log = audit or AuditLog(db_path)
    tool_registry = registry or default_registry()
    _register_mcp_tools(tool_registry, cfg, audit_log)
    return Orchestrator(
        config=cfg,
        provider=provider or build_provider(cfg),
        memory=memory or MemoryStore(db_path),
        audit=audit_log,
        registry=tool_registry,
        gate=gate or ApprovalGate(
            db_path,
            ttl_seconds=cfg.approvals.ttl_seconds,
            retain_days=cfg.approvals.retain_days,
        ),
        policy=policy or Policy(cfg.resolve_allowed_dirs(), shell=cfg.shell),
        embedding=embedding,
        vindex=vindex,
        planner=PlanStore(db_path),
    )


def _register_mcp_tools(registry: ToolRegistry, cfg: MitraConfig, audit: AuditLog) -> None:
    """Discover MCP server tools at startup. Failures skip with an audit."""
    for name, server in cfg.mcp_servers.items():
        try:
            tools = mcp_list_tools(server.command, server.args, timeout=10)
        except McpError as exc:
            audit.log(
                actor="system",
                project="",
                action="mcp_discover",
                target=name,
                decision="error",
                rationale=str(exc),
            )
            continue
        for entry in tools:
            try:
                registry.register(
                    McpTool(name, entry["name"], entry["description"], server.trust)
                )
            except ValueError:
                continue


def build_test_orchestrator(
    tmp_dir: str | Path, shell: ShellConfig | None = None, embedding=None
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
        embedding=embedding,
        vindex=VectorIndex(db_path) if embedding is not None else None,
        planner=PlanStore(db_path),
    )
