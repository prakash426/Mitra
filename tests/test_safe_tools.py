"""Read only discovery tools: list, search, fetch."""

from __future__ import annotations

import httpx

from mitra.security.audit import AuditLog
from mitra.security.policy import ApprovalGate, Policy
from mitra.tools.base import ToolContext
from mitra.tools.safe_tools import FetchUrlTool, ListDirTool, SearchFilesTool


def _ctx(tmp_path):
    gate = ApprovalGate()
    audit = AuditLog(tmp_path / "audit.db")
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "docs" / "guide.md").write_text("guide", encoding="utf-8")
    (tmp_path / "top.txt").write_text("top", encoding="utf-8")
    ctx = ToolContext(project="P", policy=Policy([tmp_path]), gate=gate, audit=audit)
    return ctx, gate, audit


def test_list_dir(tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    try:
        result = ListDirTool().invoke({"path": "."}, ctx)
        assert result.ok
        assert "top.txt" in result.output and "docs/" in result.output
        assert not ListDirTool().invoke({"path": "nope"}, ctx).ok
        assert not ListDirTool().invoke({"path": ".."}, ctx).ok
    finally:
        gate.close()
        audit.close()


def test_search_files(tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    (tmp_path / ".venv").mkdir(exist_ok=True)
    (tmp_path / ".venv" / "skip.md").write_text("x", encoding="utf-8")
    try:
        result = SearchFilesTool().invoke({"pattern": "*.md"}, ctx)
        assert result.ok
        assert "guide.md" in result.output
        assert "skip.md" not in result.output
        assert not SearchFilesTool().invoke({"pattern": ""}, ctx).ok
        assert not SearchFilesTool().invoke({"pattern": "../x"}, ctx).ok
    finally:
        gate.close()
        audit.close()


class _FakeStream:
    def __init__(self, body: bytes, content_type: str = "text/html"):
        self._body = body
        self.headers = {"content-type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        pass

    def iter_bytes(self, size):
        yield self._body


def test_fetch_url_success(monkeypatch, tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: _FakeStream(b"<h1>hi</h1>"))
    try:
        result = FetchUrlTool().invoke({"url": "https://example.com"}, ctx)
        assert result.ok and "hi" in result.output
    finally:
        gate.close()
        audit.close()


def test_fetch_url_rejects_scheme(tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    try:
        assert not FetchUrlTool().invoke({"url": "file:///etc/passwd"}, ctx).ok
        assert not FetchUrlTool().invoke({"url": "ftp://x/y"}, ctx).ok
        assert not FetchUrlTool().invoke({"url": "javascript:alert(1)"}, ctx).ok
    finally:
        gate.close()
        audit.close()


def test_fetch_url_skips_binary(monkeypatch, tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    monkeypatch.setattr(
        httpx, "stream", lambda *a, **k: _FakeStream(b"\x89PNG", "image/png")
    )
    try:
        result = FetchUrlTool().invoke({"url": "https://example.com/i.png"}, ctx)
        assert result.ok and "skipped" in result.output
    finally:
        gate.close()
        audit.close()


def test_fetch_url_truncates(monkeypatch, tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: _FakeStream(b"y" * 300_000))
    try:
        result = FetchUrlTool().invoke({"url": "https://example.com/big"}, ctx)
        assert result.ok and "truncated" in result.output
    finally:
        gate.close()
        audit.close()


def test_fetch_url_failure(monkeypatch, tmp_path):
    ctx, gate, audit = _ctx(tmp_path)

    def boom(*args, **kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "stream", boom)
    try:
        result = FetchUrlTool().invoke({"url": "https://example.com"}, ctx)
        assert not result.ok
    finally:
        gate.close()
        audit.close()
