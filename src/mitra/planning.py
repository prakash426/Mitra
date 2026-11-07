"""Model driven plans with persisted checkpoints.

The planner is one swappable step in the loop: a reasoning model turns a
multi step request into tool calls, the orchestrator runs read steps at
once and pauses at the first mutation for approval. Progress persists in
SQLite, so "continue plan-N" resumes even after a restart.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .providers.base import ModelProvider, ProviderError
from .tools.base import ToolSpec

MAX_PLAN_STEPS = 5

PLAN_TRIGGERS = (" and then ", " then ", " after that", " followed by ")

PLAN_INSTRUCTIONS = (
    "You are Mitra's planner. Turn the user request into a short sequence "
    "of tool calls using ONLY the tools listed below. Reply with a single "
    "JSON object and nothing else: "
    '{"steps": [{"tool": "<name>", "args": {<tool args>}}]}. '
    "Use at most %d steps. Prefer read only tools. Never invent tool names "
    "or arguments outside what each description documents. If the request "
    "cannot be done with these tools, reply "
    '{"steps": []}.' % MAX_PLAN_STEPS
)


class PlanError(Exception):
    """The model returned no usable plan."""


@dataclass(frozen=True)
class PlanStep:
    tool: str
    args: dict = field(default_factory=dict)
    done: bool = False
    result: str = ""


@dataclass(frozen=True)
class PlanRecord:
    id: str
    project: str
    goal: str
    steps: tuple
    status: str
    created_at: str


def looks_like_plan(message: str) -> bool:
    lowered = " " + message.strip().lower()
    if lowered.strip().startswith("plan:"):
        return True
    return any(marker in lowered for marker in PLAN_TRIGGERS)


def _strip_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines)
    return cleaned.strip()


def request_plan(
    provider: ModelProvider, message: str, specs: list[ToolSpec]
) -> list[PlanStep]:
    """Ask the model for a tool plan. Raises PlanError when unusable."""
    lines = [
        "- %s (%s): %s" % (s.name, "read only" if s.read_only else "mutation", s.description)
        for s in specs
    ]
    prompt = (
        PLAN_INSTRUCTIONS + "\n\nTOOLS:\n" + "\n".join(lines) + "\n\nREQUEST:\n" + message.strip()
    )
    try:
        generation = provider.generate(prompt, "")
    except ProviderError as exc:
        raise PlanError("model call failed: %s" % exc)
    try:
        data = json.loads(_strip_fences(generation.text))
    except ValueError:
        raise PlanError("planner did not return JSON.")
    raw_steps = data.get("steps") if isinstance(data, dict) else None
    if not isinstance(raw_steps, list) or not raw_steps:
        raise PlanError("planner returned no steps.")
    if len(raw_steps) > MAX_PLAN_STEPS:
        raise PlanError("planner returned too many steps.")
    known = {s.name for s in specs}
    steps: list[PlanStep] = []
    for entry in raw_steps:
        if not isinstance(entry, dict):
            raise PlanError("each step must be an object.")
        tool = entry.get("tool", "")
        args = entry.get("args", {})
        if not isinstance(tool, str) or tool not in known:
            raise PlanError("unknown tool '%s' in plan." % tool)
        if not isinstance(args, dict):
            raise PlanError("args for '%s' must be an object." % tool)
        steps.append(PlanStep(tool=tool, args=args))
    return steps


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PlanStore:
    """Persisted plan checkpoints beside the approval queue."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                goal TEXT NOT NULL,
                steps TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _display_id(pk: int) -> str:
        return "plan-%d" % pk

    @staticmethod
    def _parse_id(plan_id: str) -> int | None:
        text = plan_id.strip().lower()
        if not text.startswith("plan-"):
            return None
        try:
            return int(text[len("plan-"):])
        except ValueError:
            return None

    def _row_to_record(self, row: sqlite3.Row) -> PlanRecord:
        try:
            raw_steps = json.loads(row["steps"])
        except ValueError:
            raw_steps = []
        steps = tuple(
            PlanStep(
                tool=str(s.get("tool", "")),
                args=dict(s.get("args", {})),
                done=bool(s.get("done", False)),
                result=str(s.get("result", "")),
            )
            for s in raw_steps
            if isinstance(s, dict)
        )
        return PlanRecord(
            id=self._display_id(row["id"]),
            project=row["project"],
            goal=row["goal"],
            steps=steps,
            status=row["status"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _steps_to_json(steps: tuple) -> str:
        return json.dumps(
            [
                {"tool": s.tool, "args": s.args, "done": s.done, "result": s.result}
                for s in steps
            ]
        )

    def create(self, project: str, goal: str, steps: list[PlanStep]) -> PlanRecord:
        cur = self._conn.execute(
            "INSERT INTO plans (project, goal, steps, status, created_at)"
            " VALUES (?, ?, ?, 'active', ?)",
            (project, goal, self._steps_to_json(tuple(steps)), _utcnow()),
        )
        self._conn.commit()
        record = self.get(self._display_id(cur.lastrowid or 0))
        if record is None:
            raise PlanError("could not read back the stored plan.")
        return record

    def get(self, plan_id: str) -> PlanRecord | None:
        pk = self._parse_id(plan_id)
        if pk is None:
            return None
        row = self._conn.execute(
            "SELECT * FROM plans WHERE id = ?", (pk,)
        ).fetchone()
        return self._row_to_record(row) if row else None

    def active(self, project: str = "") -> list[PlanRecord]:
        if project:
            rows = self._conn.execute(
                "SELECT * FROM plans WHERE status = 'active' AND project = ?"
                " ORDER BY id",
                (project,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM plans WHERE status = 'active' ORDER BY id"
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def save_steps(self, plan_id: str, steps: tuple) -> None:
        pk = self._parse_id(plan_id)
        if pk is None:
            return
        self._conn.execute(
            "UPDATE plans SET steps = ? WHERE id = ?",
            (self._steps_to_json(steps), pk),
        )
        self._conn.commit()

    def finish(self, plan_id: str, status: str) -> None:
        pk = self._parse_id(plan_id)
        if pk is None or status not in ("done", "abandoned"):
            return
        self._conn.execute(
            "UPDATE plans SET status = ? WHERE id = ?", (status, pk)
        )
        self._conn.commit()
