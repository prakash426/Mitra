"""Tool registry. Registration never touches the orchestrator."""

from __future__ import annotations

from .base import Tool, ToolSpec


class ToolRegistry:
    """Name to tool map with discovery support."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not tool.name or not tool.name.strip():
            raise ValueError("tool name must be a non empty string.")
        key = tool.name.strip()
        if key in self._tools:
            raise ValueError("tool '%s' is already registered." % key)
        self._tools[key] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name.strip())

    def describe(self) -> list[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]


def default_registry() -> ToolRegistry:
    """Registry with the milestone four tool set."""
    from .file_tools import ProposeWriteFileTool, ReadFileTool
    from .github_tools import GithubListPrsTool, GithubOpenPrTool, GithubReadFileTool
    from .macos_tools import CalendarAddTool, CalendarNextTool, MailRecentTool
    from .safe_tools import FetchUrlTool, ListDirTool, SearchFilesTool
    from .shell_tool import RunShellTool

    registry = ToolRegistry()
    registry.register(ReadFileTool())
    registry.register(ProposeWriteFileTool())
    registry.register(ListDirTool())
    registry.register(SearchFilesTool())
    registry.register(FetchUrlTool())
    registry.register(RunShellTool())
    registry.register(GithubReadFileTool())
    registry.register(GithubListPrsTool())
    registry.register(GithubOpenPrTool())
    registry.register(CalendarNextTool())
    registry.register(MailRecentTool())
    registry.register(CalendarAddTool())
    return registry
