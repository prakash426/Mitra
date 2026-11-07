"""GitHub connector. Reads run freely; opening a PR needs approval.

Authentication comes from MITRA_GITHUB_TOKEN only. Without a token, public
repositories stay readable and anything else fails with a clear message.
Tokens never touch proposals, logs, or stored details.
"""

from __future__ import annotations

import base64
import json
import os
import re

import httpx

from .base import Tool, ToolContext, ToolResult

API = "https://api.github.com"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
MAX_FILE_BYTES = 100_000


def _token() -> str:
    return os.environ.get("MITRA_GITHUB_TOKEN", "").strip()


def _headers() -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    token = _token()
    if token:
        headers["Authorization"] = "Bearer " + token
    return headers


def _check_repo(repo: str) -> str:
    if not REPO_RE.match(repo.strip()) or ".." in repo:
        return "Repository must look like 'owner/name'."
    return ""


def _check_path(path: str) -> str:
    cleaned = path.strip()
    if not cleaned or cleaned.startswith("/") or ".." in cleaned.split("/"):
        return "Path must stay relative without parent segments."
    return ""


def _check_branch(branch: str) -> str:
    cleaned = branch.strip()
    if not BRANCH_RE.match(cleaned) or ".." in cleaned:
        return "Branch '%s' looks invalid." % branch.strip()
    return ""


class GithubReadFileTool(Tool):
    """Read a file or list a directory inside a GitHub repository."""

    name = "github_read_file"
    description = "Read a repo file. Args: {repo, path, ref}."
    read_only = True

    def invoke(self, args: dict, ctx: ToolContext) -> ToolResult:
        repo = str(args.get("repo", ""))
        path = str(args.get("path", ""))
        ref = str(args.get("ref", "")).strip()
        problem = _check_repo(repo) or _check_path(path)
        if problem:
            return ToolResult(ok=False, error=problem)
        url = API + "/repos/%s/contents/%s" % (
            repo.strip(),
            path.strip().lstrip("/"),
        )
        try:
            resp = httpx.get(
                url, headers=_headers(), params={"ref": ref} if ref else None, timeout=20
            )
            if resp.status_code == 404:
                return ToolResult(
                    ok=False,
                    error="Not found. Private repos need MITRA_GITHUB_TOKEN.",
                    target=repo.strip(),
                )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            return ToolResult(
                ok=False, error="GitHub request failed: %s." % exc, target=repo.strip()
            )
        except ValueError:
            return ToolResult(
                ok=False,
                error="GitHub returned an unreadable reply.",
                target=repo.strip(),
            )
        if isinstance(data, list):
            names = [str(e.get("name", "")) for e in data[:50]]
            ctx.audit.log(
                actor="user",
                project=ctx.project,
                action="github_read_file",
                target="%s:%s" % (repo.strip(), path.strip()),
                decision="allowed",
                rationale="directory listing",
                result="%d entries" % len(names),
            )
            return ToolResult(
                ok=True,
                output="%s:%s:\n%s" % (repo.strip(), path.strip(), "\n".join(names)),
                target=repo.strip(),
            )
        if data.get("encoding") != "base64" or not isinstance(data.get("content"), str):
            return ToolResult(ok=False, error="Unsupported GitHub content encoding.")
        try:
            raw = base64.b64decode(data["content"])
        except ValueError:
            return ToolResult(ok=False, error="GitHub content is not valid base64.")
        if len(raw) > MAX_FILE_BYTES:
            return ToolResult(
                ok=False,
                error="File is %d bytes, over the %d byte limit."
                % (len(raw), MAX_FILE_BYTES),
            )
        text = raw.decode("utf-8", errors="replace")
        ctx.audit.log(
            actor="user",
            project=ctx.project,
            action="github_read_file",
            target="%s:%s" % (repo.strip(), path.strip()),
            decision="allowed",
            rationale="repo read",
            result="%d chars read" % len(text),
        )
        return ToolResult(
            ok=True,
            output="%s:%s:\n%s" % (repo.strip(), path.strip(), text),
            target=repo.strip(),
        )


class GithubListPrsTool(Tool):
    """List pull requests for a repository."""

    name = "github_list_prs"
    description = "List pull requests. Args: {repo, state}."
    read_only = True

    def invoke(self, args: dict, ctx: ToolContext) -> ToolResult:
        repo = str(args.get("repo", ""))
        state = str(args.get("state", "open")).strip() or "open"
        problem = _check_repo(repo)
        if problem:
            return ToolResult(ok=False, error=problem)
        if state not in ("open", "closed", "all"):
            return ToolResult(ok=False, error="State must be open, closed, or all.")
        try:
            resp = httpx.get(
                API + "/repos/%s/pulls" % repo.strip(),
                headers=_headers(),
                params={"state": state, "per_page": 20},
                timeout=20,
            )
            if resp.status_code == 404:
                return ToolResult(
                    ok=False,
                    error="Not found. Private repos need MITRA_GITHUB_TOKEN.",
                    target=repo.strip(),
                )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            return ToolResult(
                ok=False, error="GitHub request failed: %s." % exc, target=repo.strip()
            )
        except ValueError:
            return ToolResult(
                ok=False,
                error="GitHub returned an unreadable reply.",
                target=repo.strip(),
            )
        if not isinstance(data, list):
            return ToolResult(ok=False, error="GitHub returned an unreadable reply.")
        lines = []
        for pr in data[:20]:
            user = pr.get("user", {}) or {}
            lines.append(
                "#%s %s (%s)" % (pr.get("number", "?"), pr.get("title", ""), user.get("login", "?"))
            )
        ctx.audit.log(
            actor="user",
            project=ctx.project,
            action="github_list_prs",
            target=repo.strip(),
            decision="allowed",
            rationale="state %s" % state,
            result="%d pull requests" % len(lines),
        )
        body = "\n".join(lines) if lines else "(no pull requests)"
        return ToolResult(
            ok=True,
            output="Pull requests for %s (%s):\n%s" % (repo.strip(), state, body),
            target=repo.strip(),
        )


class GithubOpenPrTool(Tool):
    """Stage a pull request for approval. Nothing posts until approval."""

    name = "github_open_pr"
    description = "Stage a pull request. Args: {repo, title, head, base, body}."
    read_only = False

    def invoke(self, args: dict, ctx: ToolContext) -> ToolResult:
        repo = str(args.get("repo", ""))
        title = str(args.get("title", "")).strip()
        head = str(args.get("head", ""))
        base = str(args.get("base", ""))
        body = str(args.get("body", ""))
        problem = (
            _check_repo(repo)
            or _check_branch(head)
            or _check_branch(base)
            or ("" if title else "Missing 'title' argument.")
        )
        if problem:
            return ToolResult(ok=False, error=problem)
        spec = {
            "repo": repo.strip(),
            "title": title,
            "head": head.strip(),
            "base": base.strip(),
            "body": body[:2000],
        }
        proposal = ctx.gate.stage(
            action="github_open_pr",
            target=repo.strip(),
            details=json.dumps(spec),
            project=ctx.project,
        )
        ctx.audit.log(
            actor="user",
            project=ctx.project,
            action="github_open_pr",
            target=repo.strip(),
            decision="approval_requested",
            rationale="Proposal %s staged: %s" % (proposal.id, title[:80]),
        )
        return ToolResult(
            ok=True,
            output="Proposal %s staged: PR '%s' in %s (%s into %s)."
            % (proposal.id, title, repo.strip(), head.strip(), base.strip()),
            target=repo.strip(),
            proposal_id=proposal.id,
        )


def execute_approved_pr(proposal_id: str, token: str, ctx: ToolContext) -> ToolResult:
    """Open a staged pull request after approval. Burns the token."""
    proposal = ctx.gate.get(proposal_id)
    if proposal is None:
        return ToolResult(ok=False, error="Unknown proposal '%s'." % proposal_id)
    if not ctx.gate.consume(proposal_id, token):
        ctx.audit.log(
            actor="user",
            project=ctx.project,
            action="github_open_pr",
            target=proposal.target,
            decision="denied",
            rationale="Approval token invalid, already used, or not approved.",
        )
        return ToolResult(
            ok=False,
            error="Approval missing or already used for '%s'." % proposal_id,
            target=proposal.target,
        )
    try:
        spec = json.loads(proposal.details)
    except ValueError:
        return ToolResult(ok=False, error="Proposal details are unreadable.")
    if not _token():
        return ToolResult(
            ok=False, error="Missing MITRA_GITHUB_TOKEN. Opening a PR needs a token."
        )
    try:
        resp = httpx.post(
            API + "/repos/%s/pulls" % spec["repo"],
            headers=_headers(),
            json={
                "title": spec["title"],
                "head": spec["head"],
                "base": spec["base"],
                "body": spec.get("body", ""),
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        return ToolResult(
            ok=False, error="GitHub rejected the PR: %s." % exc, target=spec["repo"]
        )
    except ValueError:
        return ToolResult(ok=False, error="GitHub returned an unreadable reply.")
    number = data.get("number", "?")
    url = data.get("html_url", "")
    ctx.audit.log(
        actor="user",
        project=ctx.project,
        action="github_open_pr",
        target=spec["repo"],
        decision="allowed",
        rationale="Proposal %s approved and executed." % proposal_id,
        result="PR #%s opened" % number,
    )
    message = "Opened PR #%s in %s." % (number, spec["repo"])
    if url:
        message += "\n%s" % url
    return ToolResult(ok=True, output=message, target=spec["repo"])
