"""macOS Calendar and Mail connector. Reads run freely, event creation
stages a proposal and waits for approval, like every other mutation.

The first run asks the user for Automation permission through the normal
macOS prompt. Until granted, these tools fail gracefully with guidance.
"""

from __future__ import annotations

import json
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


def _add_event_script(
    title: str, year: int, month: int, day: int, hour: int, minute: int,
    duration: int, calendar: str,
) -> str:
    target = 'first calendar' if not calendar else 'calendar "%s"' % _quote(calendar)
    return (
        'tell application "Calendar"\n'
        "  set startDate to current date\n"
        "  set year of startDate to %d\n" % year
        + "  set month of startDate to %d\n" % month
        + "  set day of startDate to %d\n" % day
        + "  set time of startDate to (%d * hours + %d * minutes)\n" % (hour, minute)
        + "  set endDate to startDate + (%d * minutes)\n" % duration
        + "  tell (%s)\n" % target
        + '    make new event with properties {summary:"%s",'
        ' start date:startDate, end date:endDate}\n' % _quote(title)
        + "  end tell\n"
        + "end tell\n"
    )


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


class CalendarAddTool(Tool):
    """Stage a calendar event for approval. Nothing books until approval."""

    name = "calendar_add"
    description = "Stage a calendar event. Args: {title, date, time, duration}."
    read_only = False

    def invoke(self, args: dict, ctx: ToolContext) -> ToolResult:
        title = str(args.get("title", "")).strip()
        date = str(args.get("date", "")).strip()
        time = str(args.get("time", "")).strip()
        calendar = str(args.get("calendar", "")).strip()
        try:
            duration = int(args.get("duration", 30))
        except (TypeError, ValueError):
            return ToolResult(ok=False, error="duration must be a whole number.")
        if not 5 <= duration <= 480:
            return ToolResult(ok=False, error="duration must sit between 5 and 480.")
        decision = ctx.policy.check_calendar_add(title, date, time)
        if not decision.needs_approval:
            ctx.audit.log(
                actor="user",
                project=ctx.project,
                action="calendar_add",
                target=title[:120] or "event",
                decision="denied",
                rationale=decision.reason,
            )
            return ToolResult(ok=False, error=decision.reason, target=title[:120])
        details = json.dumps(
            {"title": title, "date": date, "time": time,
             "duration": duration, "calendar": calendar}
        )
        proposal = ctx.gate.stage(
            action="calendar_add",
            target="%s on %s at %s" % (title[:80], date, time),
            details=details,
            project=ctx.project,
        )
        ctx.audit.log(
            actor="user",
            project=ctx.project,
            action="calendar_add",
            target=proposal.target,
            decision="approval_requested",
            rationale="Proposal %s staged." % proposal.id,
        )
        return ToolResult(
            ok=True,
            output="Proposal %s staged: %s\nReply 'approve %s' to book, or 'deny %s'."
            % (proposal.id, proposal.target, proposal.id, proposal.id),
            target=proposal.target,
            proposal_id=proposal.id,
        )


def execute_approved_event(
    proposal_id: str, token: str, ctx: ToolContext
) -> ToolResult:
    """Book a staged event after approval. Burns the token on success."""
    proposal = ctx.gate.get(proposal_id)
    if proposal is None:
        return ToolResult(ok=False, error="Unknown proposal '%s'." % proposal_id)
    if not ctx.gate.consume(proposal_id, token):
        ctx.audit.log(
            actor="user",
            project=ctx.project,
            action="calendar_add",
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
        year_s, month_s, day_s = str(spec["date"]).split("-")
        hour_s, minute_s = str(spec["time"]).split(":")
        when = (int(year_s), int(month_s), int(day_s), int(hour_s), int(minute_s))
        duration = int(spec.get("duration", 30))
        title = str(spec["title"])
    except (ValueError, KeyError, TypeError, AttributeError):
        return ToolResult(ok=False, error="Event data is unreadable.")
    script = _add_event_script(
        title, *when, duration, str(spec.get("calendar", ""))
    )
    ok, output = _run_osascript(script, "Calendar")
    if not ok:
        return ToolResult(ok=False, error=output, target=proposal.target)
    ctx.audit.log(
        actor="user",
        project=ctx.project,
        action="calendar_add",
        target=proposal.target,
        decision="allowed",
        rationale="Proposal %s approved and executed." % proposal_id,
    )
    return ToolResult(ok=True, output="Booked: %s." % proposal.target, target=proposal.target)
