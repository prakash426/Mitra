"""macOS autostart scripts: syntax, plist rendering, opener logic."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).parent.parent / "scripts" / "macos"


def _run(cmd, env=None):
    merged = dict(os.environ)
    if env:
        merged.update(env)
    return subprocess.run(cmd, capture_output=True, text=True, env=merged, timeout=60)


def test_scripts_pass_shell_syntax():
    for name in ("install.sh", "uninstall.sh", "mitra-open.sh", "mitra-launch.sh", "build-app.sh"):
        result = _run(["bash", "-n", str(SCRIPTS / name)])
        assert result.returncode == 0, name


def test_installer_renders_plists(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".venv" / "bin").mkdir(parents=True)
    fake_python = repo / ".venv" / "bin" / "python"
    fake_python.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)
    (repo / "config.toml").write_text("[api]\nport = 9999\n", encoding="utf-8")
    agents = tmp_path / "agents"
    result = _run(
        ["bash", str(SCRIPTS / "install.sh")],
        env={
            "MITRA_REPO_DIR": str(repo),
            "MITRA_AGENTS_DIR": str(agents),
            "MITRA_SKIP_BOOTSTRAP": "1",
        },
    )
    assert result.returncode == 0, result.stderr
    server = (agents / "com.mitra.server.plist").read_text(encoding="utf-8")
    opener = (agents / "com.mitra.opener.plist").read_text(encoding="utf-8")
    assert str(fake_python) in server
    assert "mitra.api" in server
    assert "http://127.0.0.1:9999" in opener
    if sys.platform == "darwin":
        lint1 = _run(["plutil", "-lint", str(agents / "com.mitra.server.plist")])
        lint2 = _run(["plutil", "-lint", str(agents / "com.mitra.opener.plist")])
        assert lint1.returncode == 0 and lint2.returncode == 0


def test_opener_waits_then_opens(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    opened = tmp_path / "opened.txt"
    (bin_dir / "curl").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (bin_dir / "open").write_text(
        "#!/bin/sh\necho \"$1\" >> \"%s\"\n" % opened, encoding="utf-8"
    )
    for name in ("curl", "open"):
        path = bin_dir / name
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    env = {"PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", "")}
    result = _run(
        ["bash", str(SCRIPTS / "mitra-open.sh"), "http://127.0.0.1:9999", "2"], env=env
    )
    assert result.returncode == 0, result.stderr
    assert opened.read_text(encoding="utf-8").strip() == "http://127.0.0.1:9999"


def test_launcher_starts_api_then_skips_open(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".venv" / "bin").mkdir(parents=True)
    fake_python = repo / ".venv" / "bin" / "python"
    fake_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)
    (repo / "config.toml").write_text("[api]\nport = 9999\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "curl").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    (bin_dir / "curl").chmod(0o755)
    env = {
        "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
        "MITRA_REPO_DIR": str(repo),
        "MITRA_SKIP_OPEN": "1",
        "MITRA_HEALTH_TRIES": "1",
        "HOME": str(tmp_path / "home"),
    }
    result = _run(["bash", str(SCRIPTS / "mitra-launch.sh")], env=env)
    assert result.returncode == 0, result.stderr
    assert "Mitra ready at http://127.0.0.1:9999" in result.stdout


def test_launcher_fails_without_venv(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "curl").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    (bin_dir / "curl").chmod(0o755)
    env = {
        "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
        "MITRA_REPO_DIR": str(repo),
        "MITRA_SKIP_OPEN": "1",
        "MITRA_HEALTH_TRIES": "1",
        "HOME": str(tmp_path / "home"),
    }
    result = _run(["bash", str(SCRIPTS / "mitra-launch.sh")], env=env)
    assert result.returncode == 1
    assert "no venv python" in result.stdout


def test_build_app_renders_bundle(tmp_path):
    out = tmp_path / "dist"
    result = _run(
        ["bash", str(SCRIPTS / "build-app.sh"), str(out)],
        env={"MITRA_REPO_DIR": str(SCRIPTS.parent.parent)},
    )
    assert result.returncode == 0, result.stderr
    app = out / "Mitra.app"
    assert (app / "Contents" / "Info.plist").is_file()
    assert (app / "Contents" / "MacOS" / "Mitra").is_file()
    plist = (app / "Contents" / "Info.plist").read_text(encoding="utf-8")
    assert "com.mitra.app" in plist
    launcher = (app / "Contents" / "MacOS" / "Mitra").read_text(encoding="utf-8")
    assert "mitra-launch.sh" in launcher
