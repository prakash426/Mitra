"""Append only audit log with tamper evident hash chaining.

There is deliberately no update or delete API. Each entry hashes the
previous entry hash plus its own fields, so any edit breaks the chain
from that point on. Older databases without hash columns are migrated
and backfilled on open.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

GENESIS_HASH = "GENESIS"


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


def _entry_hash(
    prev: str,
    created_at: str,
    actor: str,
    project: str,
    action: str,
    target: str,
    decision: str,
    rationale: str,
    result: str,
) -> str:
    payload = "|".join(
        [prev, created_at, actor, project, action, target, decision, rationale, result]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
                result TEXT NOT NULL,
                prev_hash TEXT NOT NULL DEFAULT '',
                hash TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_project ON audit(project, id DESC)"
        )
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        columns = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(audit)")
        }
        if "prev_hash" not in columns:
            self._conn.execute("ALTER TABLE audit ADD COLUMN prev_hash TEXT DEFAULT ''")
        if "hash" not in columns:
            self._conn.execute("ALTER TABLE audit ADD COLUMN hash TEXT DEFAULT ''")
        prev = GENESIS_HASH
        rows = self._conn.execute("SELECT * FROM audit ORDER BY id").fetchall()
        for row in rows:
            if row["hash"]:
                prev = row["hash"]
                continue
            digest = _entry_hash(
                prev,
                row["created_at"],
                row["actor"],
                row["project"],
                row["action"],
                row["target"],
                row["decision"],
                row["rationale"],
                row["result"],
            )
            self._conn.execute(
                "UPDATE audit SET prev_hash = ?, hash = ? WHERE id = ?",
                (prev, digest, row["id"]),
            )
            prev = digest

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
        last = self._conn.execute(
            "SELECT hash FROM audit ORDER BY id DESC LIMIT 1"
        ).fetchone()
        prev = last["hash"] if last and last["hash"] else GENESIS_HASH
        digest = _entry_hash(
            prev, now, actor, project, action, target, decision, rationale, result
        )
        cur = self._conn.execute(
            "INSERT INTO audit (created_at, actor, project, action, target,"
            " decision, rationale, result, prev_hash, hash)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now, actor, project, action, target, decision, rationale, result, prev, digest),
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

    def verify_chain(self) -> tuple[bool, int]:
        """Recompute the chain. Returns (True, count) or (False, bad_id)."""
        rows = self._conn.execute("SELECT * FROM audit ORDER BY id").fetchall()
        prev = GENESIS_HASH
        for row in rows:
            if row["prev_hash"] != prev:
                return False, row["id"]
            digest = _entry_hash(
                prev,
                row["created_at"],
                row["actor"],
                row["project"],
                row["action"],
                row["target"],
                row["decision"],
                row["rationale"],
                row["result"],
            )
            if digest != row["hash"]:
                return False, row["id"]
            prev = digest
        return True, len(rows)

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
