"""Audit log: append only, ordered, filterable."""

from __future__ import annotations

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
