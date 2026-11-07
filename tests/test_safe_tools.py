"""Read only discovery tools: list, search, fetch with SSRF guards."""

from __future__ import annotations

import httpx
import pytest

from mitra.security.audit import AuditLog
from mitra.security.policy import ApprovalGate, Policy
from mitra.tools.base import ToolContext
from mitra.tools.safe_tools import FetchUrlTool, ListDirTool, SearchFilesTool

PUBLIC_IP = "93.184.216.34"


def _ctx(tmp_path, hosts=(), private_hosts=()):
    gate = ApprovalGate()
    audit = AuditLog(tmp_path / "audit.db")
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "docs" / "guide.md").write_text("guide", encoding="utf-8")
    (tmp_path / "top.txt").write_text("top", encoding="utf-8")
    ctx = ToolContext(
        project="P",
        policy=Policy([tmp_path]),
        gate=gate,
        audit=audit,
        fetch_allowed_hosts=tuple(hosts),
        fetch_private_hosts=tuple(private_hosts),
    )
    return ctx, gate, audit


def _public_resolve(host, port, *args, **kwargs):
    return [(None, None, None, None, (PUBLIC_IP, 0))]


def _smart_resolve(host, port, *args, **kwargs):
    ip = "127.0.0.1" if host in ("127.0.0.1", "localhost") else PUBLIC_IP
    return [(None, None, None, None, (ip, 0))]


@pytest.fixture()
def public_dns(monkeypatch):
    monkeypatch.setattr("mitra.tools.safe_tools.getaddrinfo", _public_resolve)


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
    def __init__(self, body=b"", content_type="text/html", status=200, location=""):
        self._body = body
        self.status_code = status
        self.headers = {"content-type": content_type}
        if location:
            self.headers["location"] = location

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "http error",
                request=httpx.Request("GET", "http://x"),
                response=httpx.Response(self.status_code),
            )

    def iter_bytes(self, size):
        yield self._body


def test_fetch_url_success(public_dns, monkeypatch, tmp_path):
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


def test_fetch_url_skips_binary(public_dns, monkeypatch, tmp_path):
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


def test_fetch_url_truncates(public_dns, monkeypatch, tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: _FakeStream(b"y" * 300_000))
    try:
        result = FetchUrlTool().invoke({"url": "https://example.com/big"}, ctx)
        assert result.ok and "truncated" in result.output
    finally:
        gate.close()
        audit.close()


def test_fetch_url_failure(public_dns, monkeypatch, tmp_path):
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


def test_fetch_private_hosts_blocked(tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    try:
        urls = (
            "http://127.0.0.1:9/x",
            "http://localhost:9/x",
            "http://10.0.0.1/x",
            "http://192.168.1.1/x",
            "http://169.254.169.254/x",
        )
        for url in urls:
            result = FetchUrlTool().invoke({"url": url}, ctx)
            assert not result.ok and "private or local" in result.error, url
    finally:
        gate.close()
        audit.close()


def test_fetch_unresolvable_host(monkeypatch, tmp_path):
    ctx, gate, audit = _ctx(tmp_path)

    def boom(host, port, *args, **kwargs):
        raise OSError("no such host")

    monkeypatch.setattr("mitra.tools.safe_tools.getaddrinfo", boom)
    try:
        result = FetchUrlTool().invoke({"url": "https://no-such-host-xyz.test/"}, ctx)
        assert not result.ok and "Cannot resolve" in result.error
    finally:
        gate.close()
        audit.close()


def test_fetch_allowlist_enforced(public_dns, monkeypatch, tmp_path):
    ctx, gate, audit = _ctx(tmp_path, hosts=("example.com",))
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: _FakeStream(b"ok"))
    try:
        good = FetchUrlTool().invoke({"url": "https://example.com/"}, ctx)
        assert good.ok
        bad = FetchUrlTool().invoke({"url": "https://other.org/"}, ctx)
        assert not bad.ok and "allowlist" in bad.error
    finally:
        gate.close()
        audit.close()


def test_fetch_redirect_to_private_blocked(monkeypatch, tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    monkeypatch.setattr("mitra.tools.safe_tools.getaddrinfo", _smart_resolve)
    calls = {"n": 0}

    def fake_stream(*args, **kwargs):
        calls["n"] += 1
        return _FakeStream(b"", status=302, location="http://127.0.0.1/secret")

    monkeypatch.setattr(httpx, "stream", fake_stream)
    try:
        result = FetchUrlTool().invoke({"url": "https://example.com/go"}, ctx)
        assert not result.ok and "private or local" in result.error
        assert calls["n"] == 1
    finally:
        gate.close()
        audit.close()


def test_fetch_non_http_redirect_blocked(public_dns, monkeypatch, tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    monkeypatch.setattr(
        httpx, "stream", lambda *a, **k: _FakeStream(b"", status=302, location="ftp://x/y")
    )
    try:
        result = FetchUrlTool().invoke({"url": "https://example.com/go"}, ctx)
        assert not result.ok and "not http(s)" in result.error
    finally:
        gate.close()
        audit.close()


def test_fetch_private_host_exception(monkeypatch, tmp_path):
    ctx, gate, audit = _ctx(tmp_path, private_hosts=("127.0.0.1",))
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: _FakeStream(b"lan page"))
    try:
        result = FetchUrlTool().invoke({"url": "http://127.0.0.1:9/lan"}, ctx)
        assert result.ok and "lan page" in result.output
    finally:
        gate.close()
        audit.close()


def test_fetch_too_many_redirects(public_dns, monkeypatch, tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    calls = {"n": 0}

    def fake_stream(*args, **kwargs):
        calls["n"] += 1
        return _FakeStream(b"", status=302, location="https://example.com/loop")

    monkeypatch.setattr(httpx, "stream", fake_stream)
    try:
        result = FetchUrlTool().invoke({"url": "https://example.com/loop"}, ctx)
        assert not result.ok and "Too many redirects" in result.error
        assert calls["n"] == 4
    finally:
        gate.close()
        audit.close()
