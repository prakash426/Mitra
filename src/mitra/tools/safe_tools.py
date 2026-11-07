"""Read only discovery tools. List, search, and fetch. Never mutate."""

from __future__ import annotations

import httpx

from .base import Tool, ToolContext, ToolResult

SKIP_DIRS = frozenset(
    {".venv", ".git", "__pycache__", "node_modules", ".pytest_cache", ".hg", ".svn"}
)
MAX_LIST_ENTRIES = 200
MAX_SEARCH_RESULTS = 50
MAX_FETCH_BYTES = 200_000
MAX_FETCH_CHARS = 8000


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


class FetchUrlTool(Tool):
    """Fetch a URL over http or https with strict size and time caps."""

    name = "fetch_url"
    description = "Fetch a URL as text. Args: {url}."
    read_only = True

    def invoke(self, args: dict, ctx: ToolContext) -> ToolResult:
        url = str(args.get("url", "")).strip()
        if not url.lower().startswith(("http://", "https://")):
            return ToolResult(ok=False, error="Only http and https URLs are allowed.")
        try:
            with httpx.stream(
                "GET", url, timeout=15, follow_redirects=True
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                data = bytearray()
                for chunk in response.iter_bytes(8192):
                    room = MAX_FETCH_BYTES - len(data)
                    if room <= 0:
                        break
                    data += chunk[:room]
        except httpx.HTTPError as exc:
            return ToolResult(ok=False, error="Fetch failed: %s." % exc, target=url)
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
        text = bytes(data).decode("utf-8", errors="replace")
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
