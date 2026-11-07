"""Audit log: append only, ordered, filterable, hash chained."""

from __future__ import annotations

import sqlite3

from mitra.security.audit import AuditLog


def test_log_and_list_recent(tmp_path):
    audit = AuditLog(tmp_path / "a.db")
    try:
        first = audit.log("user", "P", "read_file", "/x", "allowed", "ok", "1")
        second = audit.log("user", "P", "deny", "/y", "denied", "no", "")
        assert first.id < second.id
        entries = audit.list_recent("P")
        assert [e.id for e in entries] == [second.id, first.id]
        assert entries[0].decision == "denied"
        assert entries[1].result == "1"
    finally:
        audit.close()


def test_project_filter(tmp_path):
    audit = AuditLog(tmp_path / "a.db")
    try:
        audit.log("user", "A", "read_file", "/x", "allowed")
        audit.log("user", "B", "read_file", "/y", "allowed")
        assert [e.project for e in audit.list_recent("A")] == ["A"]
        assert len(audit.list_recent()) == 2
    finally:
        audit.close()


def test_no_update_or_delete_api(tmp_path):
    audit = AuditLog(tmp_path / "a.db")
    try:
        assert not hasattr(audit, "update")
        assert not hasattr(audit, "delete")
        assert not hasattr(audit, "clear")
    finally:
        audit.close()


def test_chain_verifies(tmp_path):
    audit = AuditLog(tmp_path / "a.db")
    try:
        audit.log("user", "P", "a", "/x", "allowed")
        audit.log("user", "P", "b", "/y", "denied")
        assert audit.verify_chain() == (True, 2)
    finally:
        audit.close()


def test_tamper_breaks_chain(tmp_path):
    db = tmp_path / "a.db"
    audit = AuditLog(db)
    try:
        audit.log("user", "P", "a", "/x", "allowed")
        audit.log("user", "P", "b", "/y", "denied")
        raw = sqlite3.connect(str(db))
        raw.execute("UPDATE audit SET result = 'forged' WHERE id = 1")
        raw.commit()
        raw.close()
        assert audit.verify_chain() == (False, 1)
    finally:
        audit.close()


def test_migration_backfills_hashes(tmp_path):
    db = tmp_path / "old.db"
    raw = sqlite3.connect(str(db))
    raw.execute(
        "CREATE TABLE audit (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " created_at TEXT, actor TEXT, project TEXT, action TEXT, target TEXT,"
        " decision TEXT, rationale TEXT, result TEXT)"
    )
    raw.execute(
        "INSERT INTO audit (created_at, actor, project, action, target,"
        " decision, rationale, result) VALUES ('t', 'u', 'P', 'a', '/x', 'ok', '', '')"
    )
    raw.commit()
    raw.close()
    audit = AuditLog(db)
    try:
        assert audit.verify_chain() == (True, 1)
    finally:
        audit.close()
