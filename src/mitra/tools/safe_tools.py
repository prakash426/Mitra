"""Read only discovery tools. List, search, and fetch. Never mutate."""

from __future__ import annotations

import ipaddress
from socket import getaddrinfo
from urllib.parse import urljoin, urlsplit

import httpx

from .base import Tool, ToolContext, ToolResult

SKIP_DIRS = frozenset(
    {".venv", ".git", "__pycache__", "node_modules", ".pytest_cache", ".hg", ".svn"}
)
MAX_LIST_ENTRIES = 200
MAX_SEARCH_RESULTS = 50
MAX_FETCH_BYTES = 200_000
MAX_FETCH_CHARS = 8000
MAX_REDIRECTS = 3
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class ListDirTool(Tool):
    """List one directory inside the allowed scope."""

    name = "list_dir"
    description = "List a directory. Args: {path}."
    read_only = True

    def invoke(self, args: dict, ctx: ToolContext) -> ToolResult:
        path = str(args.get("path", ".")).strip() or "."
        decision = ctx.policy.check_dir(path)
        if not decision.allowed:
            ctx.audit.log(
                actor="user",
                project=ctx.project,
                action="list_dir",
                target=path,
                decision="denied",
                rationale=decision.reason,
            )
            return ToolResult(ok=False, error=decision.reason, target=path)
        resolved = ctx.policy.resolve_inside(path)
        assert resolved is not None
        try:
            entries = sorted(
                resolved.iterdir(), key=lambda p: (p.is_file(), p.name.lower())
            )
        except OSError as exc:
            return ToolResult(
                ok=False, error="Cannot list directory: %s." % exc, target=str(resolved)
            )
        lines = []
        for entry in entries[:MAX_LIST_ENTRIES]:
            if entry.is_dir():
                lines.append(entry.name + "/")
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = -1
                lines.append("%s (%d bytes)" % (entry.name, size))
        if len(entries) > MAX_LIST_ENTRIES:
            lines.append("... truncated at %d entries" % MAX_LIST_ENTRIES)
        ctx.audit.log(
            actor="user",
            project=ctx.project,
            action="list_dir",
            target=str(resolved),
            decision="allowed",
            rationale=decision.reason,
            result="%d entries" % len(entries),
        )
        body = "\n".join(lines) if lines else "(empty directory)"
        return ToolResult(
            ok=True, output="%s:\n%s" % (resolved, body), target=str(resolved)
        )


class SearchFilesTool(Tool):
    """Find files by name pattern inside the allowed scope."""

    name = "search_files"
    description = "Search file names. Args: {pattern, path}."
    read_only = True

    def invoke(self, args: dict, ctx: ToolContext) -> ToolResult:
        pattern = str(args.get("pattern", "")).strip()
        path = str(args.get("path", ".")).strip() or "."
        if not pattern:
            return ToolResult(ok=False, error="Missing 'pattern' argument.")
        if ".." in pattern or pattern.startswith("/"):
            return ToolResult(ok=False, error="Pattern must stay relative.")
        decision = ctx.policy.check_dir(path)
        if not decision.allowed:
            ctx.audit.log(
                actor="user",
                project=ctx.project,
                action="search_files",
                target=path,
                decision="denied",
                rationale=decision.reason,
            )
            return ToolResult(ok=False, error=decision.reason, target=path)
        resolved = ctx.policy.resolve_inside(path)
        assert resolved is not None
        found: list[str] = []
        try:
            for candidate in resolved.rglob(pattern):
                if any(part in SKIP_DIRS for part in candidate.parts):
                    continue
                found.append(str(candidate.relative_to(resolved)))
                if len(found) >= MAX_SEARCH_RESULTS:
                    break
        except OSError as exc:
            return ToolResult(
                ok=False, error="Search failed: %s." % exc, target=str(resolved)
            )
        ctx.audit.log(
            actor="user",
            project=ctx.project,
            action="search_files",
            target=str(resolved),
            decision="allowed",
            rationale="pattern '%s'" % pattern,
            result="%d matches" % len(found),
        )
        if not found:
            return ToolResult(
                ok=True, output="No matches for '%s'." % pattern, target=str(resolved)
            )
        return ToolResult(
            ok=True,
            output="Matches for '%s':\n%s" % (pattern, "\n".join(sorted(found))),
            target=str(resolved),
        )


def host_problem(
    url: str,
    allowed_hosts: tuple[str, ...],
    private_hosts: tuple[str, ...] = (),
) -> str:
    """Validate a fetch target. Returns an error message or empty string."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return "URL has an unreadable host."
    if not host:
        return "URL has no host."
    if allowed_hosts and not any(
        host == entry or host.endswith("." + entry) for entry in allowed_hosts
    ):
        return "Host '%s' is not in the fetch allowlist." % host
    private_ok = any(
        host == entry or host.endswith("." + entry) for entry in private_hosts
    )
    try:
        infos = getaddrinfo(host, None)
    except OSError:
        return "Cannot resolve host '%s'." % host
    if private_ok:
        return ""
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return "Host '%s' resolves to a private or local address." % host
    return ""


class FetchUrlTool(Tool):
    """Fetch a URL over http or https with strict size and time caps.

    Every hop is revalidated: public hosts only, optional allowlist,
    manual redirect loop so a redirect can never smuggle a private target.
    """

    name = "fetch_url"
    description = "Fetch a URL as text. Args: {url}."
    read_only = True

    def invoke(self, args: dict, ctx: ToolContext) -> ToolResult:
        url = str(args.get("url", "")).strip()
        if not url.lower().startswith(("http://", "https://")):
            return ToolResult(ok=False, error="Only http and https URLs are allowed.")
        current = url
        try:
            for _hop in range(MAX_REDIRECTS + 1):
                problem = host_problem(
                    current, ctx.fetch_allowed_hosts, ctx.fetch_private_hosts
                )
                if problem:
                    return ToolResult(ok=False, error=problem, target=url)
                with httpx.stream(
                    "GET", current, timeout=15, follow_redirects=False
                ) as response:
                    location = response.headers.get("location", "")
                    if response.status_code in REDIRECT_STATUSES and location:
                        current = urljoin(current, location)
                        if not current.lower().startswith(("http://", "https://")):
                            return ToolResult(
                                ok=False,
                                error="Redirect target is not http(s).",
                                target=url,
                            )
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "")
                    data = bytearray()
                    for chunk in response.iter_bytes(8192):
                        room = MAX_FETCH_BYTES - len(data)
                        if room <= 0:
                            break
                        data += chunk[:room]
                    return self._finish(ctx, url, content_type, bytes(data))
        except httpx.HTTPError as exc:
            return ToolResult(ok=False, error="Fetch failed: %s." % exc, target=url)
        return ToolResult(ok=False, error="Too many redirects.", target=url)

    def _finish(
        self, ctx: ToolContext, url: str, content_type: str, data: bytes
    ) -> ToolResult:
        truncated = len(data) >= MAX_FETCH_BYTES
        lowered_type = content_type.lower()
        text_types = ("text/", "json", "xml", "javascript")
        if lowered_type and not any(t in lowered_type for t in text_types):
            ctx.audit.log(
                actor="user",
                project=ctx.project,
                action="fetch_url",
                target=url,
                decision="allowed",
                rationale="non text content skipped",
                result="%d bytes, %s" % (len(data), content_type),
            )
            return ToolResult(
                ok=True,
                output="Fetched %s (%d bytes, %s). Non text content skipped."
                % (url, len(data), content_type or "unknown type"),
                target=url,
            )
        text = data.decode("utf-8", errors="replace")
        if len(text) > MAX_FETCH_CHARS:
            text = text[:MAX_FETCH_CHARS] + "\n... truncated"
        elif truncated:
            text += "\n... truncated at %d bytes" % MAX_FETCH_BYTES
        ctx.audit.log(
            actor="user",
            project=ctx.project,
            action="fetch_url",
            target=url,
            decision="allowed",
            rationale="http fetch inside caps",
            result="%d bytes" % len(data),
        )
        return ToolResult(ok=True, output="Fetched %s:\n%s" % (url, text), target=url)
