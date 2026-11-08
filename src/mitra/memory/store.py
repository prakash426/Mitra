"""SQLite memory store with FTS5 retrieval and correction override.

Every record carries project, kind, source, and timestamps. Retrieval always
filters by project. Superseded records stay for history but never surface in
recall results.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

VALID_KINDS = ("preference", "fact", "decision", "correction", "task", "note", "standing")
TASK_STATES = ("open", "doing", "done")

_STOPWORDS = frozenset(
    "a an the and or but if then what when where which who whom why how "
    "did do does is are was were will would can could should shall may "
    "we you they them our your their this that these those about for with "
    "from into over after before remember recall remind decide decided "
    "show list tell give what".split()
)


@dataclass(frozen=True)
class MemoryRecord:
    id: int
    project: str
    kind: str
    text: str
    source: str
    created_at: str
    updated_at: str
    superseded_by: int | None = None


@dataclass(frozen=True)
class MemoryHit:
    record: MemoryRecord
    snippet: str


@dataclass(frozen=True)
class Task:
    id: int
    project: str
    title: str
    state: str
    source: str
    created_at: str
    updated_at: str


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _tokens(query: str) -> list[str]:
    words = []
    for raw in query.lower().split():
        cleaned = "".join(ch for ch in raw if ch.isalnum())
        if len(cleaned) >= 3 and cleaned not in _STOPWORDS:
            words.append(cleaned)
    return words


class MemoryStore:
    """Project scoped memory over one SQLite file."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                kind TEXT NOT NULL,
                text TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                superseded_by INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_memories_project
                ON memories(project, updated_at DESC);
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
                USING fts5(text, content='memories', content_rowid='id');
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories
            BEGIN
                INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text);
            END;
            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories
            BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, text)
                    VALUES ('delete', old.id, old.text);
            END;
            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE OF text
                ON memories
            BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, text)
                    VALUES ('delete', old.id, old.text);
                INSERT INTO memories_fts(rowid, text)
                    VALUES (new.id, new.text);
            END;
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                title TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'open',
                source TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_project
                ON tasks(project, state, updated_at DESC);
            CREATE TABLE IF NOT EXISTS chat_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_turns_project
                ON chat_turns(project, id DESC);
            CREATE TABLE IF NOT EXISTS chat_state (
                project TEXT PRIMARY KEY,
                pending_kind TEXT NOT NULL DEFAULT '',
                pending_target TEXT NOT NULL DEFAULT '',
                last_topic TEXT NOT NULL DEFAULT '',
                last_target TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _row_to_record(self, row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"],
            project=row["project"],
            kind=row["kind"],
            text=row["text"],
            source=row["source"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            superseded_by=row["superseded_by"],
        )

    def add(
        self, project: str, kind: str, text: str, source: str = "user"
    ) -> MemoryRecord:
        """Store a record. Raises ValueError on empty input or bad kind."""
        project = project.strip()
        text = text.strip()
        if not project:
            raise ValueError("project must be a non empty string.")
        if kind not in VALID_KINDS:
            raise ValueError("kind must be one of: %s." % ", ".join(VALID_KINDS))
        if not text:
            raise ValueError("text must be a non empty string.")
        now = _utcnow()
        cur = self._conn.execute(
            "INSERT INTO memories (project, kind, text, source, created_at,"
            " updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (project, kind, text, source.strip() or "user", now, now),
        )
        self._conn.commit()
        return self.get(cur.lastrowid or 0) or MemoryRecord(
            id=cur.lastrowid or 0,
            project=project,
            kind=kind,
            text=text,
            source=source,
            created_at=now,
            updated_at=now,
        )

    def add_correction(
        self, project: str, text: str, topic: str, source: str = "user"
    ) -> MemoryRecord:
        """Store a correction and supersede older active matches on topic."""
        record = self.add(project, "correction", text, source)
        hint = "".join(ch for ch in topic.lower() if ch.isalnum() or ch == " ").strip()
        if hint:
            like = "%" + hint + "%"
            self._conn.execute(
                "UPDATE memories SET superseded_by = ?, updated_at = ?"
                " WHERE project = ? AND id != ? AND superseded_by IS NULL"
                " AND lower(text) LIKE ?",
                (record.id, _utcnow(), project.strip(), record.id, like),
            )
            self._conn.commit()
        return self.get(record.id) or record

    def get(self, record_id: int) -> MemoryRecord | None:
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id = ?", (record_id,)
        ).fetchone()
        return self._row_to_record(row) if row else None

    def update_text(self, record_id: int, text: str) -> MemoryRecord | None:
        """Replace record text. Returns the updated record or None."""
        text = text.strip()
        if not text:
            raise ValueError("text must be a non empty string.")
        self._conn.execute(
            "UPDATE memories SET text = ?, updated_at = ? WHERE id = ?",
            (text, _utcnow(), record_id),
        )
        self._conn.commit()
        return self.get(record_id)

    def delete(self, record_id: int) -> bool:
        """Delete a record. Returns True when a row was removed."""
        cur = self._conn.execute("DELETE FROM memories WHERE id = ?", (record_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def list_project(self, project: str, limit: int = 20) -> list[MemoryRecord]:
        """Active records for one project, newest first."""
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE project = ? AND superseded_by IS NULL"
            " ORDER BY updated_at DESC LIMIT ?",
            (project.strip(), max(1, limit)),
        ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def recall(
        self, project: str, query: str, limit: int = 5
    ) -> list[MemoryHit]:
        """Ranked active records for one project with evidence snippets."""
        project = project.strip()
        tokens = _tokens(query)
        if not tokens:
            return [
                MemoryHit(record=r, snippet=r.text[:160])
                for r in self.list_project(project, limit)
            ]
        match = " OR ".join(tokens)
        try:
            rows = self._conn.execute(
                "SELECT m.*, snippet(memories_fts, 0, '', '', '...', 24) AS snip"
                " FROM memories_fts JOIN memories m ON m.id = memories_fts.rowid"
                " WHERE memories_fts MATCH ? AND m.project = ?"
                " AND m.superseded_by IS NULL ORDER BY rank LIMIT ?",
                (match, project, max(1, limit)),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        if not rows:
            clauses = " OR ".join(["lower(text) LIKE ?"] * len(tokens))
            likes = ["%" + token + "%" for token in tokens]
            rows = self._conn.execute(
                "SELECT *, substr(text, 1, 160) AS snip FROM memories"
                " WHERE project = ? AND superseded_by IS NULL"
                " AND (%s) ORDER BY updated_at DESC LIMIT ?" % clauses,
                (project, *likes, max(1, limit)),
            ).fetchall()
        hits = []
        for row in rows:
            snippet = row["snip"] or row["text"][:160]
            hits.append(MemoryHit(record=self._row_to_record(row), snippet=snippet))
        if len(hits) < max(1, limit):
            seen = {hit.record.id for hit in hits}
            longest = max(tokens, key=len)
            if len(longest) >= 4:
                extra = self._conn.execute(
                    "SELECT *, substr(text, 1, 160) AS snip FROM memories"
                    " WHERE project = ? AND superseded_by IS NULL"
                    " AND lower(text) LIKE ? ORDER BY updated_at DESC LIMIT ?",
                    (project, "%" + longest + "%", max(1, limit)),
                ).fetchall()
                for row in extra:
                    if row["id"] in seen:
                        continue
                    snippet = row["snip"] or row["text"][:160]
                    hits.append(
                        MemoryHit(record=self._row_to_record(row), snippet=snippet)
                    )
                    seen.add(row["id"])
                    if len(hits) >= max(1, limit):
                        break
        return hits

    def _row_to_task(self, row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"],
            project=row["project"],
            title=row["title"],
            state=row["state"],
            source=row["source"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def task_add(self, project: str, title: str, source: str = "user") -> Task:
        """Track a new task in state open. Raises ValueError on bad input."""
        project = project.strip()
        title = title.strip()
        if not project:
            raise ValueError("project must be a non empty string.")
        if not title:
            raise ValueError("title must be a non empty string.")
        now = _utcnow()
        cur = self._conn.execute(
            "INSERT INTO tasks (project, title, state, source, created_at,"
            " updated_at) VALUES (?, ?, 'open', ?, ?, ?)",
            (project, title, source.strip() or "user", now, now),
        )
        self._conn.commit()
        fetched = self.task_get(cur.lastrowid or 0)
        assert fetched is not None
        return fetched

    def task_get(self, task_id: int) -> Task | None:
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return self._row_to_task(row) if row else None

    def task_list(self, project: str, state: str = "") -> list[Task]:
        """Tasks for one project. Optional state filter."""
        if state and state not in TASK_STATES:
            raise ValueError("state must be one of: %s." % ", ".join(TASK_STATES))
        if state:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE project = ? AND state = ?"
                " ORDER BY updated_at DESC",
                (project.strip(), state),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE project = ? ORDER BY updated_at DESC",
                (project.strip(),),
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def task_set_state(self, task_id: int, state: str) -> Task | None:
        """Move a task to a new state. Returns None when missing."""
        if state not in TASK_STATES:
            raise ValueError("state must be one of: %s." % ", ".join(TASK_STATES))
        self._conn.execute(
            "UPDATE tasks SET state = ?, updated_at = ? WHERE id = ?",
            (state, _utcnow(), task_id),
        )
        self._conn.commit()
        return self.task_get(task_id)

    def turn_add(self, project: str, role: str, text: str) -> int:
        """Append one chat turn. Keeps the newest 60 per project."""
        project = project.strip() or "default"
        role = role.strip() or "user"
        text = text.strip()
        if not text:
            raise ValueError("text must be a non empty string.")
        cur = self._conn.execute(
            "INSERT INTO chat_turns (project, role, text, created_at)"
            " VALUES (?, ?, ?, ?)",
            (project, role, text[:4000], _utcnow()),
        )
        self._conn.commit()
        turn_id = cur.lastrowid or 0
        self._conn.execute(
            "DELETE FROM chat_turns WHERE project = ? AND id NOT IN"
            " (SELECT id FROM chat_turns WHERE project = ?"
            " ORDER BY id DESC LIMIT 60)",
            (project, project),
        )
        self._conn.commit()
        return turn_id

    def recent_turns(self, project: str, limit: int = 10) -> list[dict[str, str]]:
        """Newest conversation turns, oldest first, for prompt context."""
        rows = self._conn.execute(
            "SELECT role, text, created_at FROM chat_turns WHERE project = ?"
            " ORDER BY id DESC LIMIT ?",
            (project.strip() or "default", max(1, limit)),
        ).fetchall()
        turns = [
            {"role": row["role"], "text": row["text"], "created_at": row["created_at"]}
            for row in rows
        ]
        turns.reverse()
        return turns

    def last_activity(self, project: str) -> str:
        """Newest turn timestamp, empty when the project never chatted."""
        row = self._conn.execute(
            "SELECT created_at FROM chat_turns WHERE project = ?"
            " ORDER BY id DESC LIMIT 1",
            (project.strip() or "default",),
        ).fetchone()
        return row["created_at"] if row else ""

    def state_get(self, project: str) -> dict[str, str]:
        """Pending question plus last topic and target for followups."""
        row = self._conn.execute(
            "SELECT pending_kind, pending_target, last_topic, last_target"
            " FROM chat_state WHERE project = ?",
            (project.strip() or "default",),
        ).fetchone()
        if row is None:
            return {"pending_kind": "", "pending_target": "", "last_topic": "", "last_target": ""}
        return {
            "pending_kind": row["pending_kind"] or "",
            "pending_target": row["pending_target"] or "",
            "last_topic": row["last_topic"] or "",
            "last_target": row["last_target"] or "",
        }

    def state_set(
        self,
        project: str,
        pending_kind: str | None = None,
        pending_target: str | None = None,
        last_topic: str | None = None,
        last_target: str | None = None,
    ) -> dict[str, str]:
        """Upsert conversation state. None fields keep their values."""
        key = project.strip() or "default"
        current = self.state_get(key)
        merged = {
            "pending_kind": current["pending_kind"] if pending_kind is None else pending_kind,
            "pending_target": (
                current["pending_target"] if pending_target is None else pending_target
            ),
            "last_topic": current["last_topic"] if last_topic is None else last_topic,
            "last_target": current["last_target"] if last_target is None else last_target,
        }
        self._conn.execute(
            "INSERT INTO chat_state"
            " (project, pending_kind, pending_target, last_topic, last_target,"
            " updated_at) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(project) DO UPDATE SET pending_kind = ?,"
            " pending_target = ?, last_topic = ?, last_target = ?,"
            " updated_at = ?",
            (
                key,
                merged["pending_kind"],
                merged["pending_target"],
                merged["last_topic"],
                merged["last_target"],
                _utcnow(),
                merged["pending_kind"],
                merged["pending_target"],
                merged["last_topic"],
                merged["last_target"],
                _utcnow(),
            ),
        )
        self._conn.commit()
        return merged

    def state_clear_pending(self, project: str) -> None:
        """Drop a pending question after it resolves or a new task starts."""
        self.state_set(project, pending_kind="", pending_target="")
