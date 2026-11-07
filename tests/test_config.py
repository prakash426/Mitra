"""Config loading, validation, and environment overrides."""

from __future__ import annotations

import pytest

from mitra.config import ConfigError, default_config, load_config, resolve_profile

VALID_TOML = """
[provider]
name = "ollama"
model = "llama3.1"
base_url = "http://localhost:11434"
timeout_seconds = 15

[memory]
db_path = "data/mitra.db"

[tools]
allowed_dirs = ["projects"]

[api]
host = "127.0.0.1"
port = 9000
"""


def _write(tmp_path, text: str = VALID_TOML):
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_valid_config(tmp_path):
    config = load_config(_write(tmp_path))
    assert config.provider.name == "ollama"
    assert config.provider.model == "llama3.1"
    assert config.provider.timeout_seconds == 15
    assert config.api.port == 9000
    assert config.resolve_db_path() == tmp_path / "data" / "mitra.db"
    assert config.resolve_allowed_dirs() == [tmp_path / "projects"]


def test_missing_config_helpful_error(tmp_path):
    with pytest.raises(ConfigError, match="config.example.toml"):
        load_config(tmp_path / "nope.toml")


def test_unknown_provider_rejected(tmp_path):
    path = _write(tmp_path, VALID_TOML.replace('"ollama"', '"hal9000"'))
    with pytest.raises(ConfigError, match="Unknown provider"):
        load_config(path)


def test_bad_port_and_timeout_rejected(tmp_path):
    bad_port = _write(tmp_path, VALID_TOML.replace("port = 9000", "port = 99999"))
    with pytest.raises(ConfigError, match="port"):
        load_config(bad_port)
    bad_timeout = _write(
        tmp_path, VALID_TOML.replace("timeout_seconds = 15", "timeout_seconds = 0")
    )
    with pytest.raises(ConfigError, match="timeout"):
        load_config(bad_timeout)


def test_empty_allowed_dirs_rejected(tmp_path):
    path = _write(tmp_path, VALID_TOML.replace('["projects"]', "[]"))
    with pytest.raises(ConfigError, match="allowed_dirs"):
        load_config(path)


def test_env_overrides_provider_and_key(tmp_path, monkeypatch):
    monkeypatch.setenv("MITRA_PROVIDER", "mock")
    monkeypatch.setenv("MITRA_MODEL", "env-model")
    monkeypatch.setenv("MITRA_MODEL_KEY", "secret-key")
    config = load_config(_write(tmp_path))
    assert config.provider.name == "mock"
    assert config.provider.model == "env-model"
    assert config.provider.api_key == "secret-key"


def test_defaults_are_safe(tmp_path):
    config = default_config(tmp_path)
    assert config.provider.name == "mock"
    assert config.resolve_allowed_dirs() == [tmp_path]


PROFILE_TOML = VALID_TOML + """
[profiles.Alpha]
provider = "mock"
model = "mock-echo"
allowed_dirs = ["alpha"]
shell_enabled = true

[profiles.Beta]
model = "beta-model"
"""


def test_profiles_resolve(tmp_path):
    config = load_config(_write(tmp_path, PROFILE_TOML))
    alpha = resolve_profile(config, "Alpha")
    assert alpha.provider.name == "mock"
    assert alpha.tools.allowed_dirs == ("alpha",)
    assert alpha.shell.enabled is True
    beta = resolve_profile(config, "Beta")
    assert beta.provider.name == "ollama"
    assert beta.provider.model == "beta-model"
    assert resolve_profile(config, "Unknown") is config


def test_profile_validation(tmp_path):
    bad = PROFILE_TOML + "\n[profiles.Evil]\nstarship = true\n"
    with pytest.raises(ConfigError, match="unknown key"):
        load_config(_write(tmp_path, bad))


def test_shell_and_speech_sections(tmp_path):
    config = load_config(_write(tmp_path))
    assert config.shell.enabled is False
    assert config.speech.backend == "silent"
    custom = (
        VALID_TOML
        + '\n[shell]\nenabled = true\nallowlist = ["echo"]\n'
        + '\n[speech]\nbackend = "say"\nvoice = "Alex"\n'
    )
    config2 = load_config(_write(tmp_path, custom))
    assert config2.shell.enabled is True
    assert config2.shell.allowlist == ("echo",)
    assert config2.speech.backend == "say"
    assert config2.speech.voice == "Alex"
    bad_shell = VALID_TOML + '\n[shell]\nallowlist = ["../bin/x"]\n'
    with pytest.raises(ConfigError, match="bare command"):
        load_config(_write(tmp_path, bad_shell))


def test_hardening_sections(tmp_path):
    config = load_config(_write(tmp_path))
    assert config.tools.fetch_allowed_hosts == ()
    assert config.approvals.retain_days == 30
    assert len(config.shell.deny_regexes) >= 5
    custom = (
        VALID_TOML.replace(
            'allowed_dirs = ["projects"]',
            'allowed_dirs = ["."]\nfetch_allowed_hosts = ["Example.com"]',
        )
        + "\n[approvals]\nretain_days = 7\n"
        + '\n[shell]\ndeny_regexes = ["danger-zone"]\n'
    )
    config2 = load_config(_write(tmp_path, custom))
    assert config2.tools.fetch_allowed_hosts == ("example.com",)
    assert config2.approvals.retain_days == 7
    assert config2.shell.deny_regexes == ("danger-zone",)
    bad_deny = VALID_TOML + '\n[shell]\ndeny_regexes = ["(["]\n'
    with pytest.raises(ConfigError, match="bad pattern"):
        load_config(_write(tmp_path, bad_deny))
    bad_retain = VALID_TOML + "\n[approvals]\nretain_days = 0\n"
    with pytest.raises(ConfigError, match="retain_days"):
        load_config(_write(tmp_path, bad_retain))


def test_flag_rules_and_private_hosts(tmp_path):
    custom = (
        VALID_TOML.replace(
            'allowed_dirs = ["projects"]',
            'allowed_dirs = ["."]\nfetch_private_hosts = ["Printer.Local"]',
        )
        + '\n[shell]\nallowlist = ["git"]\nflag_rules = { git = ["status"] }\n'
    )
    config = load_config(_write(tmp_path, custom))
    assert config.tools.fetch_private_hosts == ("printer.local",)
    assert config.shell.flag_rules == {"git": ("status",)}
    bad = VALID_TOML + '\n[shell]\nflag_rules = { git = "status" }\n'
    with pytest.raises(ConfigError, match="list of flags"):
        load_config(_write(tmp_path, bad))


def test_embeddings_section(tmp_path):
    config = load_config(_write(tmp_path))
    assert config.memory.embeddings == "off"
    custom = VALID_TOML.replace(
        'db_path = "data/mitra.db"',
        'db_path = "m.db"\nembeddings = "ollama"\nembed_model = "m"',
    )
    config2 = load_config(_write(tmp_path, custom))
    assert config2.memory.embeddings == "ollama"
    assert config2.memory.embed_model == "m"
    bad = VALID_TOML.replace(
        'db_path = "data/mitra.db"', 'db_path = "m.db"\nembeddings = "bogus"'
    )
    with pytest.raises(ConfigError, match="Unknown embeddings"):
        load_config(_write(tmp_path, bad))
