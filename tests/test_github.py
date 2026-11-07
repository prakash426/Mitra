"""GitHub connector: validation, reads, staged PRs."""

from __future__ import annotations

import base64

import httpx

from mitra.security.audit import AuditLog
from mitra.security.policy import ApprovalGate, Policy
from mitra.tools.base import ToolContext
from mitra.tools.github_tools import (
    GithubListPrsTool,
    GithubOpenPrTool,
    GithubReadFileTool,
    execute_approved_pr,
)


def _ctx(tmp_path):
    gate = ApprovalGate()
    audit = AuditLog(tmp_path / "audit.db")
    ctx = ToolContext(project="P", policy=Policy([tmp_path]), gate=gate, audit=audit)
    return ctx, gate, audit


class _FakeResp:
    def __init__(self, payload=None, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "http",
                request=httpx.Request("GET", "http://x"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self._payload


def test_read_file_success(monkeypatch, tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    seen = {}
    payload = {
        "content": base64.b64encode(b"hello readme").decode(),
        "encoding": "base64",
    }

    def fake_get(url, **kwargs):
        seen["url"] = url
        return _FakeResp(payload)

    monkeypatch.setattr(httpx, "get", fake_get)
    try:
        result = GithubReadFileTool().invoke(
            {"repo": "octo/demo", "path": "README.md"}, ctx
        )
        assert result.ok and "hello readme" in result.output
        assert "repos/octo/demo/contents/README.md" in seen["url"]
    finally:
        gate.close()
        audit.close()


def test_read_validation_blocks_http(monkeypatch, tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    calls = []

    def boom(url, **kwargs):
        calls.append(url)
        raise AssertionError("must not call")

    monkeypatch.setattr(httpx, "get", boom)
    try:
        assert not GithubReadFileTool().invoke({"repo": "bad", "path": "x"}, ctx).ok
        assert not GithubReadFileTool().invoke(
            {"repo": "o/r", "path": "../x"}, ctx
        ).ok
        assert calls == []
    finally:
        gate.close()
        audit.close()


def test_read_404_message(monkeypatch, tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResp({}, status=404))
    try:
        result = GithubReadFileTool().invoke({"repo": "o/r", "path": "x"}, ctx)
        assert not result.ok and "Not found" in result.error
    finally:
        gate.close()
        audit.close()


def test_read_directory_lists(monkeypatch, tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    payload = [{"name": "a.py"}, {"name": "b.py"}]
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResp(payload))
    try:
        result = GithubReadFileTool().invoke({"repo": "o/r", "path": "src"}, ctx)
        assert result.ok and "a.py" in result.output
    finally:
        gate.close()
        audit.close()


def test_list_prs(monkeypatch, tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    payload = [{"number": 3, "title": "Fix it", "user": {"login": "octo"}}]
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResp(payload))
    try:
        result = GithubListPrsTool().invoke({"repo": "o/r"}, ctx)
        assert result.ok and "#3 Fix it (octo)" in result.output
        bad = GithubListPrsTool().invoke({"repo": "o/r", "state": "bogus"}, ctx)
        assert not bad.ok
    finally:
        gate.close()
        audit.close()


def test_open_pr_stages_without_posting(monkeypatch, tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    calls = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: calls.append(1))
    try:
        result = GithubOpenPrTool().invoke(
            {"repo": "o/r", "title": "T", "head": "feat", "base": "main"}, ctx
        )
        assert result.ok and result.proposal_id.startswith("prop-")
        assert calls == []
        assert len(gate.pending("P")) == 1
    finally:
        gate.close()
        audit.close()


def test_open_pr_validation(tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    try:
        base = {"repo": "o/r", "title": "T", "head": "feat", "base": "main"}
        assert not GithubOpenPrTool().invoke({**base, "repo": "bad"}, ctx).ok
        assert not GithubOpenPrTool().invoke({**base, "head": "a b"}, ctx).ok
        assert not GithubOpenPrTool().invoke({**base, "title": " "}, ctx).ok
    finally:
        gate.close()
        audit.close()


def test_execute_posts_with_token(monkeypatch, tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    seen = {}
    monkeypatch.setenv("MITRA_GITHUB_TOKEN", "test-token-123")

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["json"] = kwargs.get("json")
        seen["auth"] = kwargs.get("headers", {}).get("Authorization", "")
        return _FakeResp({"number": 7, "html_url": "http://x/pr/7"}, status=201)

    monkeypatch.setattr(httpx, "post", fake_post)
    try:
        staged = GithubOpenPrTool().invoke(
            {"repo": "o/r", "title": "T", "head": "feat", "base": "main"}, ctx
        )
        token = gate.approve(staged.proposal_id)
        done = execute_approved_pr(staged.proposal_id, token, ctx)
        assert done.ok and "Opened PR #7" in done.output
        assert seen["url"].endswith("/repos/o/r/pulls")
        assert seen["json"]["head"] == "feat"
        assert seen["auth"] == "Bearer test-token-123"
    finally:
        gate.close()
        audit.close()


def test_execute_needs_token(monkeypatch, tmp_path):
    ctx, gate, audit = _ctx(tmp_path)
    monkeypatch.delenv("MITRA_GITHUB_TOKEN", raising=False)
    try:
        staged = GithubOpenPrTool().invoke(
            {"repo": "o/r", "title": "T", "head": "feat", "base": "main"}, ctx
        )
        token = gate.approve(staged.proposal_id)
        done = execute_approved_pr(staged.proposal_id, token, ctx)
        assert not done.ok and "MITRA_GITHUB_TOKEN" in done.error
    finally:
        gate.close()
        audit.close()
