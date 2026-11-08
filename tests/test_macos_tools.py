"""macOS Calendar and Mail readers: parsing plus graceful failure."""

from __future__ import annotations

import subprocess

from mitra.security.audit import AuditLog
from mitra.security.policy import ApprovalGate, Policy
from mitra.tools.base import ToolContext
from mitra.tools.macos_tools import (
    CalendarAddTool,
    CalendarNextTool,
    MailRecentTool,
    execute_approved_event,
)


def _ctx(tmp_path):
    gate = ApprovalGate()
    audit = AuditLog(tmp_path / "audit.db")
    return (
        ToolContext(project="P", policy=Policy([tmp_path]), gate=gate, audit=audit),
        gate,
        audit,
    )


class _Done:
    def __init__(self, stdout="", stderr="", code=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = code


def test_calendar_success(monkeypatch, tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    monkeypatch.setattr(
        "mitra.tools.macos_tools.shutil.which", lambda n: "/usr/bin/osascript"
    )
    monkeypatch.setattr(
        "mitra.tools.macos_tools.subprocess.run",
        lambda *a, **k: _Done("Standup | Monday\n\nRetro | Tuesday\n"),
    )
    try:
        result = CalendarNextTool().invoke({"days": 7}, ctx)
        assert result.ok
        assert "Standup" in result.output and "Retro" in result.output
    finally:
        gate.close()
        audit.close()


def test_calendar_validation(tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    try:
        assert not CalendarNextTool().invoke({"days": 99}, ctx).ok
        assert not CalendarNextTool().invoke({"days": "x"}, ctx).ok
    finally:
        gate.close()
        audit.close()


def test_calendar_permission_guidance(monkeypatch, tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    monkeypatch.setattr(
        "mitra.tools.macos_tools.shutil.which", lambda n: "/usr/bin/osascript"
    )
    monkeypatch.setattr(
        "mitra.tools.macos_tools.subprocess.run",
        lambda *a, **k: _Done("", "execution error: Not allowed (-1743)", 1),
    )
    try:
        result = CalendarNextTool().invoke({}, ctx)
        assert not result.ok and "Automation permission" in result.error
    finally:
        gate.close()
        audit.close()


def test_osascript_missing(monkeypatch, tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    monkeypatch.setattr("mitra.tools.macos_tools.shutil.which", lambda n: None)
    try:
        assert not CalendarNextTool().invoke({}, ctx).ok
        assert not MailRecentTool().invoke({}, ctx).ok
    finally:
        gate.close()
        audit.close()


def test_mail_timeout(monkeypatch, tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    monkeypatch.setattr(
        "mitra.tools.macos_tools.shutil.which", lambda n: "/usr/bin/osascript"
    )

    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired("osascript", 30)

    monkeypatch.setattr("mitra.tools.macos_tools.subprocess.run", boom)
    try:
        result = MailRecentTool().invoke({}, ctx)
        assert not result.ok and "timed out" in result.error
    finally:
        gate.close()
        audit.close()


def test_mail_success(monkeypatch, tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    monkeypatch.setattr(
        "mitra.tools.macos_tools.shutil.which", lambda n: "/usr/bin/osascript"
    )
    monkeypatch.setattr(
        "mitra.tools.macos_tools.subprocess.run",
        lambda *a, **k: _Done("Ship it | boss@x.io\n"),
    )
    try:
        result = MailRecentTool().invoke({"limit": 5}, ctx)
        assert result.ok and "Ship it" in result.output
        assert not MailRecentTool().invoke({"limit": 99}, ctx).ok
    finally:
        gate.close()
        audit.close()


def test_calendar_add_stages_and_books(monkeypatch, tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    monkeypatch.setattr(
        "mitra.tools.macos_tools.shutil.which", lambda n: "/usr/bin/osascript"
    )
    seen = []
    monkeypatch.setattr(
        "mitra.tools.macos_tools.subprocess.run",
        lambda *a, **k: (seen.append(a), _Done(""))[1],
    )
    try:
        staged = CalendarAddTool().invoke(
            {"title": "Dentist", "date": "2026-11-11", "time": "09:00"}, ctx
        )
        assert staged.ok and staged.proposal_id
        assert seen == []
        token = gate.approve(staged.proposal_id)
        done = execute_approved_event(staged.proposal_id, token, ctx)
        assert done.ok
        assert "Booked" in done.output
        assert "Dentist" in seen[0][0][2]
        actions = {e.action for e in audit.list_recent("P")}
        assert "calendar_add" in actions
    finally:
        gate.close()
        audit.close()


def test_calendar_add_denials(tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    try:
        assert not CalendarAddTool().invoke({"title": "", "date": "2026-11-11", "time": "09:00"}, ctx).ok
        assert not CalendarAddTool().invoke({"title": "x", "date": "11-11", "time": "09:00"}, ctx).ok
        assert not CalendarAddTool().invoke({"title": "x", "date": "2026-11-11", "time": "9am"}, ctx).ok
        assert not CalendarAddTool().invoke(
            {"title": "x", "date": "2026-11-11", "time": "09:00", "duration": 9999}, ctx
        ).ok
        assert gate.pending("P") == []
    finally:
        gate.close()
        audit.close()
