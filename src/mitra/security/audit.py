"""Append only audit log. There is deliberately no update or delete API."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class AuditEntry:
    id: int
    created_at: str
    actor: str
    project: str
    action: str
    target: str
    decision: str
    rationale: str
    result: str


class AuditLog:
    """Audit trail in its own table inside the Mitra database file."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                actor TEXT NOT NULL,
                project TEXT NOT NULL,
                action TEXT NOT NULL,
                target TEXT NOT NULL,
                decision TEXT NOT NULL,
                rationale TEXT NOT NULL,
                result TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_project ON audit(project, id DESC)"
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def log(
        self,
        actor: str,
        project: str,
        action: str,
        target: str,
        decision: str,
        rationale: str = "",
        result: str = "",
    ) -> AuditEntry:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cur = self._conn.execute(
            "INSERT INTO audit (created_at, actor, project, action, target,"
            " decision, rationale, result) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (now, actor, project, action, target, decision, rationale, result),
        )
        self._conn.commit()
        entry_id = cur.lastrowid or 0
        row = self._conn.execute(
            "SELECT * FROM audit WHERE id = ?", (entry_id,)
        ).fetchone()
        return AuditEntry(
            id=entry_id,
            created_at=row["created_at"],
            actor=row["actor"],
            project=row["project"],
            action=row["action"],
            target=row["target"],
            decision=row["decision"],
            rationale=row["rationale"],
            result=row["result"],
        )

    def list_recent(self, project: str | None = None, limit: int = 50) -> list[AuditEntry]:
        if project:
            rows = self._conn.execute(
                "SELECT * FROM audit WHERE project = ? ORDER BY id DESC LIMIT ?",
                (project, max(1, limit)),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (max(1, limit),)
            ).fetchall()
        return [
            AuditEntry(
                id=row["id"],
                created_at=row["created_at"],
                actor=row["actor"],
                project=row["project"],
                action=row["action"],
                target=row["target"],
                decision=row["decision"],
                rationale=row["rationale"],
                result=row["result"],
            )
            for row in rows
        ]
