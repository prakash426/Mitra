"""Minimal MCP client over stdio.

Speaks newline delimited JSON-RPC to a model context protocol server:
initialize, then tools/list or tools/call. Each call spawns the server
fresh, so no session state persists between calls.
"""

from __future__ import annotations

import json
import selectors
import subprocess
import time

PROTOCOL_VERSION = "2024-11-05"
DEFAULT_TIMEOUT = 20
MAX_LINE_BYTES = 1_000_000


class McpError(Exception):
    """The MCP server failed or misbehaved. Safe to show the user."""


def _send(proc: subprocess.Popen, payload: dict | None, notif: dict | None = None) -> None:
    assert proc.stdin is not None
    if notif is not None:
        proc.stdin.write(json.dumps(notif) + "\n")
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()


def _read_until(
    proc: subprocess.Popen, want: set[int], deadline: float
) -> dict[int, dict]:
    assert proc.stdout is not None
    found: dict[int, dict] = {}
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    try:
        while want - found.keys():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if proc.poll() is not None:
                break
            for key, _mask in sel.select(timeout=min(remaining, 5)):
                line = key.fileobj.readline(  # type: ignore[union-attr]
                    MAX_LINE_BYTES
                )
                if not line:
                    break
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                mid = message.get("id")
                if isinstance(mid, int) and mid in want:
                    found[mid] = message
    finally:
        sel.unregister(proc.stdout)
    return found


def _session(command: str, args: tuple[str, ...], timeout: int) -> subprocess.Popen:
    try:
        proc = subprocess.Popen(
            [command, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        raise McpError("cannot start MCP server '%s': %s." % (command, exc))
    deadline = time.monotonic() + timeout
    _send(
        proc,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mitra", "version": "0.1.0"},
            },
        },
        notif={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    found = _read_until(proc, {1}, deadline)
    if 1 not in found or "error" in found[1]:
        _stop(proc)
        detail = ""
        if 1 in found and isinstance(found[1].get("error"), dict):
            detail = " %s" % found[1]["error"].get("message", "")
        raise McpError("MCP server '%s' failed to initialize.%s" % (command, detail))
    return proc


def _stop(proc: subprocess.Popen) -> None:
    try:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            proc.kill()
        except OSError:
            pass


def _exchange(
    command: str, args: tuple[str, ...], method: str, params: dict, timeout: int
) -> dict:
    proc = _session(command, args, timeout)
    try:
        deadline = time.monotonic() + timeout
        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": method, "params": params})
        found = _read_until(proc, {2}, deadline)
        if 2 not in found:
            raise McpError("MCP server '%s' timed out on %s." % (command, method))
        if "error" in found[2]:
            err = found[2]["error"]
            detail = err.get("message", "unknown error") if isinstance(err, dict) else err
            raise McpError("MCP %s failed: %s." % (method, detail))
        result = found[2].get("result", {})
        if not isinstance(result, dict):
            raise McpError("MCP %s returned an unreadable reply." % method)
        return result
    finally:
        _stop(proc)


def list_tools(
    command: str, args: tuple[str, ...] = (), timeout: int = DEFAULT_TIMEOUT
) -> list[dict[str, str]]:
    """Return [{name, description}] for a server. Raises McpError."""
    result = _exchange(command, args, "tools/list", {}, timeout)
    tools = result.get("tools", [])
    if not isinstance(tools, list):
        raise McpError("MCP tools/list returned an unreadable reply.")
    cleaned = []
    for entry in tools:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        cleaned.append(
            {
                "name": str(entry["name"]),
                "description": str(entry.get("description", ""))[:500],
            }
        )
    return cleaned


def _text_blocks(result: dict, key: str) -> list[str]:
    parts = []
    content = result.get(key, [])
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, dict) and "text" in block and key == "contents":
                parts.append(str(block.get("text", "")))
    return parts


def call_tool(
    command: str,
    args: tuple[str, ...],
    tool: str,
    arguments: dict,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Call a server tool. Returns joined text content. Raises McpError."""
    result = _exchange(
        command, args, "tools/call", {"name": tool, "arguments": arguments}, timeout
    )
    return result_text(result, tool)


def result_text(result: dict, tool: str) -> str:
    """Joined text content of a tools/call result. Raises McpError."""
    parts = _text_blocks(result, "content")
    if result.get("isError") and not parts:
        raise McpError("MCP tool '%s' reported an error." % tool)
    return "\n".join(parts) if parts else "(no text content)"


def list_resources(
    command: str, args: tuple[str, ...] = (), timeout: int = DEFAULT_TIMEOUT
) -> list[dict[str, str]]:
    """Return [{uri, name}] for a server. Raises McpError."""
    result = _exchange(command, args, "resources/list", {}, timeout)
    items = result.get("resources", [])
    if not isinstance(items, list):
        raise McpError("MCP resources/list returned an unreadable reply.")
    cleaned = []
    for entry in items:
        if not isinstance(entry, dict) or not entry.get("uri"):
            continue
        cleaned.append(
            {
                "uri": str(entry["uri"]),
                "name": str(entry.get("name", entry["uri"]))[:200],
            }
        )
    return cleaned


def read_resource(
    command: str,
    args: tuple[str, ...],
    uri: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Read one resource. Returns joined text. Raises McpError."""
    result = _exchange(command, args, "resources/read", {"uri": uri}, timeout)
    parts = _text_blocks(result, "contents")
    return "\n".join(parts) if parts else "(no text content)"


class McpSession:
    """One persistent server process. Respawns once when it dies mid call."""

    def __init__(self, command: str, args: tuple[str, ...], timeout: int) -> None:
        self._command = command
        self._args = args
        self._timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._next_id = 2

    def _ensure(self) -> subprocess.Popen:
        if self._proc is None or self._proc.poll() is not None:
            if self._proc is not None:
                _stop(self._proc)
            self._proc = _session(self._command, self._args, self._timeout)
            self._next_id = 2
        return self._proc

    def call(self, method: str, params: dict) -> dict:
        last_error = ""
        for _attempt in range(2):
            proc = self._ensure()
            deadline = time.monotonic() + self._timeout
            call_id = self._next_id
            self._next_id += 1
            try:
                _send(
                    proc,
                    {"jsonrpc": "2.0", "id": call_id, "method": method, "params": params},
                )
            except (OSError, ValueError) as exc:
                last_error = str(exc)
                self._proc = None
                continue
            found = _read_until(proc, {call_id}, deadline)
            if call_id in found:
                if "error" in found[call_id]:
                    err = found[call_id]["error"]
                    detail = (
                        err.get("message", "unknown error")
                        if isinstance(err, dict)
                        else err
                    )
                    raise McpError("MCP %s failed: %s." % (method, detail))
                result = found[call_id].get("result", {})
                if not isinstance(result, dict):
                    raise McpError("MCP %s returned an unreadable reply." % method)
                return result
            last_error = "no reply"
            self._proc = None
        raise McpError(
            "MCP server '%s' failed on %s (%s)." % (self._command, method, last_error)
        )

    def close(self) -> None:
        if self._proc is not None:
            _stop(self._proc)
            self._proc = None


class McpPool:
    """Persistent sessions keyed by server. Owned by the orchestrator."""

    def __init__(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout
        self._sessions: dict[tuple[str, tuple[str, ...]], McpSession] = {}

    def call(
        self, command: str, args: tuple[str, ...], method: str, params: dict
    ) -> dict:
        key = (command, tuple(args))
        session = self._sessions.get(key)
        if session is None:
            session = McpSession(command, tuple(args), self._timeout)
            self._sessions[key] = session
        return session.call(method, params)

    def close(self) -> None:
        for session in self._sessions.values():
            session.close()
        self._sessions.clear()
