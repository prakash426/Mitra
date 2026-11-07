"""macOS Calendar and Mail readers. Read only, AppleScript based.

The first run asks the user for Automation permission through the normal
macOS prompt. Until granted, these tools fail gracefully with guidance.
"""

from __future__ import annotations

import shutil
import subprocess

from .base import Tool, ToolContext, ToolResult

MAX_EVENTS = 50


def _quote(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _calendar_script(days: int, calendar: str) -> str:
    header = (
        'tell application "Calendar"\n'
        '  set out to ""\n'
        "  set startWindow to (current date)\n"
        "  set endWindow to (current date) + (%d * days)\n" % days
    )
    def collect(indent: str) -> str:
        return (
            "%sif start date of ev > startWindow"
            " and start date of ev < endWindow then\n" % indent
            + '%s  set out to out & summary of ev & " | "'
            ' & (start date of ev as string) & "\\n"\n' % indent
            + "%send if\n" % indent
        )

    if calendar:
        loop = (
            '  repeat with ev in (events of calendar "%s")\n' % _quote(calendar)
            + collect("    ")
            + "  end repeat\n"
        )
    else:
        loop = (
            "  repeat with c in calendars\n"
            "    repeat with ev in (events of c)\n"
            + collect("      ")
            + "    end repeat\n"
            + "  end repeat\n"
        )
    return header + loop + "  return out\n" + "end tell\n"


def _mail_script(limit: int) -> str:
    return (
        'tell application "Mail"\n'
        '  set out to ""\n'
        "  set n to 0\n"
        "  repeat with m in (reverse of (get messages of inbox))\n"
        "    set n to n + 1\n"
        "    if n > %d then exit repeat\n" % limit
        + '    set out to out & subject of m & " | " & sender of m & "\\n"\n'
        + "  end repeat\n"
        + "  return out\n"
        + "end tell\n"
    )


def _run_osascript(script: str, app: str) -> tuple[bool, str]:
    if shutil.which("osascript") is None:
        return False, "osascript is not available on this machine."
    try:
        completed = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return False, "The %s request timed out." % app
    except OSError as exc:
        return False, "Cannot run osascript: %s." % exc
    if completed.returncode != 0:
        err = (completed.stderr or "").strip() or "unknown error"
        lowered = err.lower()
        if "not allowed" in lowered or "permission" in lowered or "-1743" in err:
            return (
                False,
                "macOS blocked %s access. Grant Automation permission in "
                "System Settings under Privacy, Automation, then retry." % app,
            )
        return False, "Script failed: %s." % err[:200]
    return True, completed.stdout


def _lines(output: str, cap: int) -> list[str]:
    return [line for line in (l.strip() for l in output.splitlines()) if line][:cap]


class CalendarNextTool(Tool):
    """List upcoming macOS Calendar events. Read only."""

    name = "calendar_next"
    description = "Upcoming calendar events. Args: {days, calendar}."
    read_only = True

    def invoke(self, args: dict, ctx: ToolContext) -> ToolResult:
        try:
            days = int(args.get("days", 7))
        except (TypeError, ValueError):
            return ToolResult(ok=False, error="days must be a whole number.")
        if not 1 <= days <= 31:
            return ToolResult(ok=False, error="days must sit between 1 and 31.")
        calendar = str(args.get("calendar", "")).strip()
        ok, output = _run_osascript(_calendar_script(days, calendar), "Calendar")
        if not ok:
            return ToolResult(ok=False, error=output)
        events = _lines(output, MAX_EVENTS)
        ctx.audit.log(
            actor="user",
            project=ctx.project,
            action="calendar_next",
            target=calendar or "all calendars",
            decision="allowed",
            rationale="read only calendar lookup",
            result="%d events" % len(events),
        )
        body = "\n".join(events) if events else "(no upcoming events)"
        return ToolResult(ok=True, output="Upcoming events:\n%s" % body, target="calendar")


class MailRecentTool(Tool):
    """List recent inbox subjects from macOS Mail. Read only."""

    name = "mail_recent"
    description = "Recent inbox subjects. Args: {limit}."
    read_only = True

    def invoke(self, args: dict, ctx: ToolContext) -> ToolResult:
        try:
            limit = int(args.get("limit", 10))
        except (TypeError, ValueError):
            return ToolResult(ok=False, error="limit must be a whole number.")
        if not 1 <= limit <= 20:
            return ToolResult(ok=False, error="limit must sit between 1 and 20.")
        ok, output = _run_osascript(_mail_script(limit), "Mail")
        if not ok:
            return ToolResult(ok=False, error=output)
        messages = _lines(output, limit)
        ctx.audit.log(
            actor="user",
            project=ctx.project,
            action="mail_recent",
            target="inbox",
            decision="allowed",
            rationale="read only inbox lookup",
            result="%d messages" % len(messages),
        )
        body = "\n".join(messages) if messages else "(inbox empty)"
        return ToolResult(ok=True, output="Recent inbox:\n%s" % body, target="inbox")
