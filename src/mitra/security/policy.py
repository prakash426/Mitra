"""Policy gate: scope confinement plus explicit approvals.

Reads inside scope are allowed. Writes and shell commands inside scope need
approval. Anything outside scope is denied. Proposals persist in SQLite with
expiry, so pending decisions survive restarts but never linger forever.
"""

from __future__ import annotations

import secrets
import shlex
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import ShellConfig

SHELL_METACHARS = frozenset(";|&$`()<>{}!\\\n\r")


@dataclass(frozen=True)
class Decision:
    allowed: bool
    needs_approval: bool
    reason: str

    @staticmethod
    def allow(reason: str) -> "Decision":
        return Decision(allowed=True, needs_approval=False, reason=reason)

    @staticmethod
    def approval(reason: str) -> "Decision":
        return Decision(allowed=False, needs_approval=True, reason=reason)

    @staticmethod
    def deny(reason: str) -> "Decision":
        return Decision(allowed=False, needs_approval=False, reason=reason)


@dataclass(frozen=True)
class Proposal:
    id: str
    action: str
    target: str
    details: str
    project: str
    created_at: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ApprovalGate:
    """Persistent approval queue. Tokens are random, single use, expiring."""

    def __init__(self, db_path: str | Path = ":memory:", ttl_seconds: int = 3600) -> None:
        self.db_path = str(db_path)
        self.ttl_seconds = ttl_seconds
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                target TEXT NOT NULL,
                details TEXT NOT NULL,
                project TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                token TEXT NOT NULL DEFAULT '',
                decided_at TEXT NOT NULL DEFAULT '',
                executed INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_proposals_status"
            " ON proposals(status, expires_at)"
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _display_id(pk: int) -> str:
        return "prop-%d" % pk

    @staticmethod
    def _parse_id(proposal_id: str) -> int | None:
        text = proposal_id.strip().lower()
        if not text.startswith("prop-"):
            return None
        try:
            return int(text[len("prop-"):])
        except ValueError:
            return None

    def _sweep_expired(self) -> None:
        now = _utcnow().isoformat(timespec="seconds")
        self._conn.execute(
            "UPDATE proposals SET status = 'expired' WHERE status = 'pending'"
            " AND expires_at <= ?",
            (now,),
        )
        self._conn.commit()

    def _row_to_proposal(self, row: sqlite3.Row) -> Proposal:
        return Proposal(
            id=self._display_id(row["id"]),
            action=row["action"],
            target=row["target"],
            details=row["details"],
            project=row["project"],
            created_at=row["created_at"],
        )

    def stage(self, action: str, target: str, details: str, project: str) -> Proposal:
        self._sweep_expired()
        now = _utcnow()
        expires = now + timedelta(seconds=self.ttl_seconds)
        cur = self._conn.execute(
            "INSERT INTO proposals (action, target, details, project, created_at,"
            " expires_at, status) VALUES (?, ?, ?, ?, ?, ?, 'pending')",
            (
                action,
                target,
                details,
                project,
                now.isoformat(timespec="seconds"),
                expires.isoformat(timespec="seconds"),
            ),
        )
        self._conn.commit()
        pk = cur.lastrowid or 0
        row = self._conn.execute(
            "SELECT * FROM proposals WHERE id = ?", (pk,)
        ).fetchone()
        return self._row_to_proposal(row)

    def pending(self, project: str = "") -> list[Proposal]:
        self._sweep_expired()
        if project.strip():
            rows = self._conn.execute(
                "SELECT * FROM proposals WHERE status = 'pending' AND project = ?"
                " ORDER BY id",
                (project.strip(),),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM proposals WHERE status = 'pending' ORDER BY id"
            ).fetchall()
        return [self._row_to_proposal(row) for row in rows]

    def get(self, proposal_id: str) -> Proposal | None:
        """Find a proposal unless it expired. Decided rows stay visible so
        retries report 'already decided' and executors can read details."""
        self._sweep_expired()
        pk = self._parse_id(proposal_id)
        if pk is None:
            return None
        row = self._conn.execute(
            "SELECT * FROM proposals WHERE id = ? AND status != 'expired'", (pk,)
        ).fetchone()
        return self._row_to_proposal(row) if row else None

    def approve(self, proposal_id: str) -> str:
        """Approve a pending proposal. Returns its single use token."""
        self._sweep_expired()
        pk = self._parse_id(proposal_id)
        row = None
        if pk is not None:
            row = self._conn.execute(
                "SELECT * FROM proposals WHERE id = ?", (pk,)
            ).fetchone()
        if row is None:
            raise KeyError("unknown proposal '%s'." % proposal_id)
        if row["status"] != "pending":
            raise ValueError("proposal '%s' is already decided." % proposal_id)
        token = secrets.token_hex(8)
        self._conn.execute(
            "UPDATE proposals SET status = 'approved', token = ?, decided_at = ?"
            " WHERE id = ?",
            (token, _utcnow().isoformat(timespec="seconds"), pk),
        )
        self._conn.commit()
        return token

    def deny(self, proposal_id: str) -> None:
        self._sweep_expired()
        pk = self._parse_id(proposal_id)
        row = None
        if pk is not None:
            row = self._conn.execute(
                "SELECT * FROM proposals WHERE id = ?", (pk,)
            ).fetchone()
        if row is None:
            raise KeyError("unknown proposal '%s'." % proposal_id)
        if row["status"] != "pending":
            raise ValueError("proposal '%s' is already decided." % proposal_id)
        self._conn.execute(
            "UPDATE proposals SET status = 'denied', decided_at = ? WHERE id = ?",
            (_utcnow().isoformat(timespec="seconds"), pk),
        )
        self._conn.commit()

    def consume(self, proposal_id: str, token: str) -> bool:
        """Validate and burn a token. Returns True only on first valid use."""
        self._sweep_expired()
        pk = self._parse_id(proposal_id)
        if pk is None or not token:
            return False
        row = self._conn.execute(
            "SELECT * FROM proposals WHERE id = ?", (pk,)
        ).fetchone()
        if row is None:
            return False
        if row["status"] != "approved":
            return False
        if not secrets.compare_digest(row["token"] or "", token):
            return False
        if row["executed"]:
            return False
        self._conn.execute("UPDATE proposals SET executed = 1 WHERE id = ?", (pk,))
        self._conn.commit()
        return True


class Policy:
    """Scope confinement for file and shell actions."""

    def __init__(
        self, allowed_dirs: list[Path], shell: "ShellConfig | None" = None
    ) -> None:
        self.allowed_dirs = [d.resolve() for d in allowed_dirs]
        self.shell = shell

    def resolve_inside(self, path: str | Path) -> Path | None:
        """Resolve a path. Relative paths anchor to the first allowed dir.
        Returns the path only when the result sits inside an allowed dir."""
        try:
            candidate = Path(path).expanduser()
            if not candidate.is_absolute():
                anchor = self.allowed_dirs[0] if self.allowed_dirs else Path.cwd()
                candidate = anchor / candidate
            candidate = candidate.resolve()
        except OSError:
            return None
        for base in self.allowed_dirs:
            try:
                candidate.relative_to(base)
                return candidate
            except ValueError:
                continue
        return None

    def check_read(self, path: str | Path) -> Decision:
        resolved = self.resolve_inside(path)
        if resolved is None:
            return Decision.deny("Path sits outside the allowed directories.")
        if not resolved.exists():
            return Decision.deny("File does not exist: %s." % resolved)
        if not resolved.is_file():
            return Decision.deny("Not a regular file: %s." % resolved)
        return Decision.allow("Read inside allowed scope.")

    def check_write(self, path: str | Path) -> Decision:
        resolved = self.resolve_inside(path)
        if resolved is None:
            return Decision.deny("Path sits outside the allowed directories.")
        if resolved.exists() and not resolved.is_file():
            return Decision.deny("Not a regular file: %s." % resolved)
        return Decision.approval("Writes need explicit approval.")

    def check_dir(self, path: str | Path) -> Decision:
        resolved = self.resolve_inside(path)
        if resolved is None:
            return Decision.deny("Path sits outside the allowed directories.")
        if not resolved.exists():
            return Decision.deny("Directory does not exist: %s." % resolved)
        if not resolved.is_dir():
            return Decision.deny("Not a directory: %s." % resolved)
        return Decision.allow("Directory inside allowed scope.")

    def check_shell(self, command: str) -> Decision:
        """Every shell command needs approval. Most things are denied first."""
        if self.shell is None or not self.shell.enabled:
            return Decision.deny("The shell tool is disabled in config.")
        text = command.strip()
        if not text:
            return Decision.deny("Empty shell command.")
        if any(ch in SHELL_METACHARS for ch in text):
            return Decision.deny("Shell metacharacters are not allowed.")
        try:
            argv = shlex.split(text)
        except ValueError as exc:
            return Decision.deny("Cannot parse command: %s." % exc)
        if not argv:
            return Decision.deny("Empty shell command.")
        name = Path(argv[0]).name
        if name not in self.shell.allowlist:
            return Decision.deny(
                "Command '%s' is not in the shell allowlist." % name
            )
        for arg in argv[1:]:
            if arg.startswith("/") or arg.startswith("~") or ".." in arg:
                return Decision.deny(
                    "Path arguments must stay inside the working directory."
                )
        return Decision.approval("Shell commands need explicit approval.")
