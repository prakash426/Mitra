"""Configuration loading for Mitra.

Settings live in config.toml. Secrets live in the environment only.
Per project profiles override a small safe subset of settings.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

VALID_PROVIDERS = ("mock", "ollama", "openai_compat")
VALID_SPEECH_BACKENDS = ("silent", "say")
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


@dataclass(frozen=True)
class ToolsConfig:
    allowed_dirs: tuple[str, ...] = (".",)


@dataclass(frozen=True)
class ApiConfig:
    host: str = "127.0.0.1"
    port: int = 8931


@dataclass(frozen=True)
class ApprovalsConfig:
    ttl_seconds: int = 3600


@dataclass(frozen=True)
class ShellConfig:
    enabled: bool = False
    allowlist: tuple[str, ...] = ("ls", "pwd", "echo")
    timeout_seconds: int = 30
    max_output_chars: int = 20000


@dataclass(frozen=True)
class SpeechConfig:
    backend: str = "silent"
    voice: str = "Samantha"


@dataclass(frozen=True)
class MitraConfig:
    provider: ProviderConfig = field(default_factory=ProviderConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    approvals: ApprovalsConfig = field(default_factory=ApprovalsConfig)
    shell: ShellConfig = field(default_factory=ShellConfig)
    speech: SpeechConfig = field(default_factory=SpeechConfig)
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

    allowed = tools_raw.get("allowed_dirs", ["."])
    if not isinstance(allowed, list) or not allowed:
        raise ConfigError("tools.allowed_dirs must be a non empty list.")
    for entry in allowed:
        if not isinstance(entry, str) or not entry.strip():
            raise ConfigError("tools.allowed_dirs entries must be non empty strings.")

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

    speech_backend = speech_raw.get("backend", "silent")
    if speech_backend not in VALID_SPEECH_BACKENDS:
        raise ConfigError(
            "Unknown speech backend '%s'. Use one of: %s."
            % (speech_backend, ", ".join(VALID_SPEECH_BACKENDS))
        )
    voice = speech_raw.get("voice", "Samantha")
    if not isinstance(voice, str) or not voice.strip():
        raise ConfigError("speech.voice must be a non empty string.")

    return MitraConfig(
        provider=ProviderConfig(
            name=name,
            model=model,
            base_url=base_url,
            timeout_seconds=timeout,
            api_key=api_key,
        ),
        memory=MemoryConfig(db_path=db_path),
        tools=ToolsConfig(allowed_dirs=tuple(allowed)),
        api=ApiConfig(host=host, port=port),
        approvals=ApprovalsConfig(ttl_seconds=ttl),
        shell=_shell_config(raw.get("shell")),
        speech=SpeechConfig(backend=speech_backend, voice=voice),
        profiles=_profile_overrides(raw.get("profiles", {})),
        project_dir=cfg_path.parent,
    )
