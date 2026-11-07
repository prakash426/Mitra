"""Configuration loading for Mitra.

Settings live in config.toml. Secrets live in the environment only.
Per project profiles override a small safe subset of settings.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

VALID_PROVIDERS = ("mock", "ollama", "openai_compat")
VALID_EMBEDDINGS = ("off", "ollama")
VALID_SPEECH_BACKENDS = ("silent", "say")
VALID_STT_ENGINES = ("stub", "faster_whisper")
VALID_MCP_TRUST = ("read", "staged")
PROFILE_KEYS = ("provider", "model", "allowed_dirs", "shell_enabled")


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


@dataclass(frozen=True)
class ProviderConfig:
    name: str = "mock"
    model: str = "mock-echo"
    base_url: str = "http://localhost:11434"
    timeout_seconds: int = 30
    api_key: str = ""


@dataclass(frozen=True)
class MemoryConfig:
    db_path: str = "mitra.db"
    embeddings: str = "off"
    embed_model: str = "nomic-embed-text"
    embed_base_url: str = "http://localhost:11434"


@dataclass(frozen=True)
class ToolsConfig:
    allowed_dirs: tuple[str, ...] = (".",)
    fetch_allowed_hosts: tuple[str, ...] = ()
    fetch_private_hosts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ApiConfig:
    host: str = "127.0.0.1"
    port: int = 8931


@dataclass(frozen=True)
class ApprovalsConfig:
    ttl_seconds: int = 3600
    retain_days: int = 30


DEFAULT_SHELL_DENY_REGEXES = (
    r"\brm\b[^;|&]*\s-[a-zA-Z]*r",
    r"\bgit\s+clean\b[^;|&]*\s-[a-zA-Z]*d",
    r"\bdd\b",
    r"\bmkfs\b",
    r"chmod\s+(-R\s+)?777",
    r":\(\)",
)


@dataclass(frozen=True)
class ShellConfig:
    enabled: bool = False
    allowlist: tuple[str, ...] = ("ls", "pwd", "echo")
    timeout_seconds: int = 30
    max_output_chars: int = 20000
    deny_regexes: tuple[str, ...] = DEFAULT_SHELL_DENY_REGEXES
    flag_rules: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class SpeechConfig:
    backend: str = "silent"
    voice: str = "Samantha"
    rate: int = 190


@dataclass(frozen=True)
class OrchestratorConfig:
    planning: bool = True


@dataclass(frozen=True)
class SpeechToTextConfig:
    engine: str = "stub"
    model: str = "base"
    max_mb: int = 10


@dataclass(frozen=True)
class McpServerConfig:
    command: str = ""
    args: tuple[str, ...] = ()
    trust: str = "staged"


@dataclass(frozen=True)
class MitraConfig:
    provider: ProviderConfig = field(default_factory=ProviderConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    approvals: ApprovalsConfig = field(default_factory=ApprovalsConfig)
    shell: ShellConfig = field(default_factory=ShellConfig)
    speech: SpeechConfig = field(default_factory=SpeechConfig)
    orchestrator: OrchestratorConfig = field(default_factory=OrchestratorConfig)
    speech_to_text: SpeechToTextConfig = field(default_factory=SpeechToTextConfig)
    mcp_servers: dict[str, McpServerConfig] = field(default_factory=dict)
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    project_dir: Path = field(default_factory=Path.cwd)

    def resolve_db_path(self) -> Path:
        """Absolute database path, resolved against the project directory."""
        db = Path(self.memory.db_path)
        if db.is_absolute():
            return db
        return self.project_dir / db

    def resolve_allowed_dirs(self) -> list[Path]:
        """Absolute allowed directories, resolved against project dir."""
        resolved = []
        for entry in self.tools.allowed_dirs:
            path = Path(entry)
            if not path.is_absolute():
                path = self.project_dir / path
            resolved.append(path)
        return resolved


def default_config(project_dir: str | Path | None = None) -> MitraConfig:
    """Safe defaults for tests and first runs. Mock provider, local paths."""
    base = Path(project_dir) if project_dir else Path.cwd()
    return MitraConfig(project_dir=base)


def resolve_profile(config: MitraConfig, project: str) -> MitraConfig:
    """Apply the named project profile over base settings, if one exists."""
    overrides = config.profiles.get(project.strip(), {})
    if not overrides:
        return config
    provider = config.provider
    if "provider" in overrides or "model" in overrides:
        provider = replace(
            provider,
            name=overrides.get("provider", provider.name),
            model=overrides.get("model", provider.model),
        )
    tools = config.tools
    if "allowed_dirs" in overrides:
        tools = replace(tools, allowed_dirs=tuple(overrides["allowed_dirs"]))
    shell = config.shell
    if "shell_enabled" in overrides:
        shell = replace(shell, enabled=bool(overrides["shell_enabled"]))
    return replace(config, provider=provider, tools=tools, shell=shell)


def _profile_overrides(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        raise ConfigError("[profiles] sections must map names to settings.")
    cleaned: dict[str, dict[str, Any]] = {}
    for name, values in raw.items():
        if not isinstance(values, dict):
            raise ConfigError("Profile '%s' must be a settings table." % name)
        for key in values:
            if key not in PROFILE_KEYS:
                raise ConfigError(
                    "Profile '%s' has unknown key '%s'." % (name, key)
                )
        if "provider" in values and values["provider"] not in VALID_PROVIDERS:
            raise ConfigError("Profile '%s' has an unknown provider." % name)
        if "model" in values and (
            not isinstance(values["model"], str) or not values["model"].strip()
        ):
            raise ConfigError("Profile '%s' model must be a non empty string." % name)
        if "allowed_dirs" in values:
            dirs = values["allowed_dirs"]
            if not isinstance(dirs, list) or not dirs:
                raise ConfigError("Profile '%s' allowed_dirs must be non empty." % name)
            for entry in dirs:
                if not isinstance(entry, str) or not entry.strip():
                    raise ConfigError(
                        "Profile '%s' allowed_dirs entries must be strings." % name
                    )
        if "shell_enabled" in values and not isinstance(values["shell_enabled"], bool):
            raise ConfigError("Profile '%s' shell_enabled must be true or false." % name)
        cleaned[name] = dict(values)
    return cleaned


def _mcp_servers(raw: Any) -> dict[str, McpServerConfig]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError("[mcp] must be a settings table.")
    servers_raw = raw.get("servers", {})
    if not isinstance(servers_raw, dict):
        raise ConfigError("[mcp.servers] must map names to settings.")
    cleaned: dict[str, McpServerConfig] = {}
    for name, values in servers_raw.items():
        if not isinstance(values, dict):
            raise ConfigError("MCP server '%s' must be a settings table." % name)
        command = values.get("command", "")
        if not isinstance(command, str) or not command.strip():
            raise ConfigError("MCP server '%s' needs a command." % name)
        args = values.get("args", [])
        if not isinstance(args, list) or not all(
            isinstance(a, str) for a in args
        ):
            raise ConfigError("MCP server '%s' args must be a list." % name)
        trust = values.get("trust", "staged")
        if trust not in VALID_MCP_TRUST:
            raise ConfigError(
                "MCP server '%s' trust must be one of: %s."
                % (name, ", ".join(VALID_MCP_TRUST))
            )
        cleaned[name] = McpServerConfig(
            command=command.strip(), args=tuple(args), trust=trust
        )
    return cleaned


def _shell_config(raw: Any) -> ShellConfig:
    if raw is None:
        return ShellConfig()
    if not isinstance(raw, dict):
        raise ConfigError("[shell] must be a settings table.")
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError("shell.enabled must be true or false.")
    allowlist = raw.get("allowlist", ["ls", "pwd", "echo"])
    if not isinstance(allowlist, list) or not allowlist:
        raise ConfigError("shell.allowlist must be a non empty list.")
    for entry in allowlist:
        if not isinstance(entry, str) or not entry.strip():
            raise ConfigError("shell.allowlist entries must be non empty strings.")
        if "/" in entry or any(ch.isspace() for ch in entry):
            raise ConfigError(
                "shell.allowlist entries must be bare command names."
            )
    deny = raw.get("deny_regexes", list(DEFAULT_SHELL_DENY_REGEXES))
    if not isinstance(deny, list):
        raise ConfigError("shell.deny_regexes must be a list.")
    for entry in deny:
        if not isinstance(entry, str) or not entry.strip():
            raise ConfigError("shell.deny_regexes entries must be non empty strings.")
        try:
            re.compile(entry)
        except re.error as exc:
            raise ConfigError("shell.deny_regexes has a bad pattern: %s." % exc)
    flag_rules_raw = raw.get("flag_rules", {})
    if not isinstance(flag_rules_raw, dict):
        raise ConfigError("shell.flag_rules must map command names to flag lists.")
    flag_rules: dict[str, tuple[str, ...]] = {}
    for command, flags in flag_rules_raw.items():
        if not isinstance(flags, list) or not all(
            isinstance(f, str) and f.strip() for f in flags
        ):
            raise ConfigError(
                "shell.flag_rules for '%s' must be a list of flags." % command
            )
        flag_rules[command] = tuple(flags)
    try:
        timeout = int(raw.get("timeout_seconds", 30))
    except (TypeError, ValueError):
        raise ConfigError("shell.timeout_seconds must be a whole number.")
    if not 1 <= timeout <= 300:
        raise ConfigError("shell.timeout_seconds must sit between 1 and 300.")
    try:
        max_chars = int(raw.get("max_output_chars", 20000))
    except (TypeError, ValueError):
        raise ConfigError("shell.max_output_chars must be a whole number.")
    if not 100 <= max_chars <= 500000:
        raise ConfigError("shell.max_output_chars must sit between 100 and 500000.")
    return ShellConfig(
        enabled=enabled,
        allowlist=tuple(allowlist),
        timeout_seconds=timeout,
        max_output_chars=max_chars,
        deny_regexes=tuple(deny),
        flag_rules=flag_rules,
    )


def load_config(path: str | Path | None = None) -> MitraConfig:
    """Load config.toml with environment overrides. Raises ConfigError."""
    cfg_path = Path(path) if path else Path.cwd() / "config.toml"
    if not cfg_path.is_file():
        raise ConfigError(
            "Config file not found: %s. Copy config.example.toml to "
            "config.toml to get started." % cfg_path
        )
    try:
        with open(cfg_path, "rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError("Cannot read config file %s: %s" % (cfg_path, exc))

    provider_raw = raw.get("provider", {})
    memory_raw = raw.get("memory", {})
    tools_raw = raw.get("tools", {})
    api_raw = raw.get("api", {})
    approvals_raw = raw.get("approvals", {})
    speech_raw = raw.get("speech", {})

    name = os.environ.get("MITRA_PROVIDER", provider_raw.get("name", "mock"))
    if name not in VALID_PROVIDERS:
        raise ConfigError(
            "Unknown provider '%s'. Use one of: %s." % (name, ", ".join(VALID_PROVIDERS))
        )
    model = os.environ.get("MITRA_MODEL", provider_raw.get("model", "mock-echo"))
    base_url = provider_raw.get("base_url", "http://localhost:11434")
    try:
        timeout = int(provider_raw.get("timeout_seconds", 30))
    except (TypeError, ValueError):
        raise ConfigError("provider.timeout_seconds must be a whole number.")
    if timeout <= 0:
        raise ConfigError("provider.timeout_seconds must be positive.")
    api_key = os.environ.get("MITRA_MODEL_KEY", "")

    db_path = memory_raw.get("db_path", "mitra.db")
    if not isinstance(db_path, str) or not db_path.strip():
        raise ConfigError("memory.db_path must be a non empty string.")
    embeddings = memory_raw.get("embeddings", "off")
    if embeddings not in VALID_EMBEDDINGS:
        raise ConfigError(
            "Unknown embeddings '%s'. Use one of: %s."
            % (embeddings, ", ".join(VALID_EMBEDDINGS))
        )
    embed_model = memory_raw.get("embed_model", "nomic-embed-text")
    if not isinstance(embed_model, str) or not embed_model.strip():
        raise ConfigError("memory.embed_model must be a non empty string.")
    embed_base_url = memory_raw.get("embed_base_url", "http://localhost:11434")
    if not isinstance(embed_base_url, str) or not embed_base_url.strip():
        raise ConfigError("memory.embed_base_url must be a non empty string.")

    allowed = tools_raw.get("allowed_dirs", ["."])
    if not isinstance(allowed, list) or not allowed:
        raise ConfigError("tools.allowed_dirs must be a non empty list.")
    for entry in allowed:
        if not isinstance(entry, str) or not entry.strip():
            raise ConfigError("tools.allowed_dirs entries must be non empty strings.")
    hosts = tools_raw.get("fetch_allowed_hosts", [])
    if not isinstance(hosts, list):
        raise ConfigError("tools.fetch_allowed_hosts must be a list.")
    for entry in hosts:
        if not isinstance(entry, str) or not entry.strip():
            raise ConfigError(
                "tools.fetch_allowed_hosts entries must be non empty strings."
            )
    private_hosts = tools_raw.get("fetch_private_hosts", [])
    if not isinstance(private_hosts, list):
        raise ConfigError("tools.fetch_private_hosts must be a list.")
    for entry in private_hosts:
        if not isinstance(entry, str) or not entry.strip():
            raise ConfigError(
                "tools.fetch_private_hosts entries must be non empty strings."
            )

    host = api_raw.get("host", "127.0.0.1")
    try:
        port = int(api_raw.get("port", 8931))
    except (TypeError, ValueError):
        raise ConfigError("api.port must be a whole number.")
    if not 1 <= port <= 65535:
        raise ConfigError("api.port must sit between 1 and 65535.")

    try:
        ttl = int(approvals_raw.get("ttl_seconds", 3600))
    except (TypeError, ValueError):
        raise ConfigError("approvals.ttl_seconds must be a whole number.")
    if ttl <= 0:
        raise ConfigError("approvals.ttl_seconds must be positive.")
    try:
        retain = int(approvals_raw.get("retain_days", 30))
    except (TypeError, ValueError):
        raise ConfigError("approvals.retain_days must be a whole number.")
    if not 1 <= retain <= 3650:
        raise ConfigError("approvals.retain_days must sit between 1 and 3650.")

    speech_backend = speech_raw.get("backend", "silent")
    if speech_backend not in VALID_SPEECH_BACKENDS:
        raise ConfigError(
            "Unknown speech backend '%s'. Use one of: %s."
            % (speech_backend, ", ".join(VALID_SPEECH_BACKENDS))
        )
    voice = speech_raw.get("voice", "Samantha")
    if not isinstance(voice, str) or not voice.strip():
        raise ConfigError("speech.voice must be a non empty string.")
    try:
        speech_rate = int(speech_raw.get("rate", 190))
    except (TypeError, ValueError):
        raise ConfigError("speech.rate must be a whole number.")
    if not 50 <= speech_rate <= 500:
        raise ConfigError("speech.rate must sit between 50 and 500.")

    orch_raw = raw.get("orchestrator", {})
    if not isinstance(orch_raw, dict):
        raise ConfigError("[orchestrator] must be a settings table.")
    planning = orch_raw.get("planning", True)
    if not isinstance(planning, bool):
        raise ConfigError("orchestrator.planning must be true or false.")

    stt_raw = raw.get("speech_to_text", {})
    if not isinstance(stt_raw, dict):
        raise ConfigError("[speech_to_text] must be a settings table.")
    stt_engine = stt_raw.get("engine", "stub")
    if stt_engine not in VALID_STT_ENGINES:
        raise ConfigError(
            "Unknown speech_to_text engine '%s'. Use one of: %s."
            % (stt_engine, ", ".join(VALID_STT_ENGINES))
        )
    stt_model = stt_raw.get("model", "base")
    if not isinstance(stt_model, str) or not stt_model.strip():
        raise ConfigError("speech_to_text.model must be a non empty string.")
    try:
        stt_max = int(stt_raw.get("max_mb", 10))
    except (TypeError, ValueError):
        raise ConfigError("speech_to_text.max_mb must be a whole number.")
    if not 1 <= stt_max <= 100:
        raise ConfigError("speech_to_text.max_mb must sit between 1 and 100.")

    mcp_servers = _mcp_servers(raw.get("mcp", {}))

    return MitraConfig(
        provider=ProviderConfig(
            name=name,
            model=model,
            base_url=base_url,
            timeout_seconds=timeout,
            api_key=api_key,
        ),
        memory=MemoryConfig(
            db_path=db_path,
            embeddings=embeddings,
            embed_model=embed_model,
            embed_base_url=embed_base_url,
        ),
        tools=ToolsConfig(
            allowed_dirs=tuple(allowed),
            fetch_allowed_hosts=tuple(h.lower() for h in hosts),
            fetch_private_hosts=tuple(h.lower() for h in private_hosts),
        ),
        api=ApiConfig(host=host, port=port),
        approvals=ApprovalsConfig(ttl_seconds=ttl, retain_days=retain),
        shell=_shell_config(raw.get("shell")),
        speech=SpeechConfig(backend=speech_backend, voice=voice, rate=speech_rate),
        orchestrator=OrchestratorConfig(planning=planning),
        speech_to_text=SpeechToTextConfig(
            engine=stt_engine, model=stt_model.strip(), max_mb=stt_max
        ),
        mcp_servers=mcp_servers,
        profiles=_profile_overrides(raw.get("profiles", {})),
        project_dir=cfg_path.parent,
    )
