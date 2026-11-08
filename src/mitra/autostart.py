"""macOS autostart status. Thin wrapper over the login plists.

No policy decisions here. Enabling and disabling shell out to the
repo scripts so there is exactly one installer implementation.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def agents_dir() -> Path:
    import os

    override = os.environ.get("MITRA_AGENTS_DIR", "")
    if override.strip():
        return Path(override)
    return Path.home() / "Library" / "LaunchAgents"


def status() -> dict[str, object]:
    """Report whether the login plists are installed."""
    base = agents_dir()
    server = base / "com.mitra.server.plist"
    opener = base / "com.mitra.opener.plist"
    installed = server.is_file() and opener.is_file()
    return {
        "supported": True,
        "enabled": installed,
        "server_plist": str(server),
        "opener_plist": str(opener),
    }


def set_enabled(want: bool) -> dict[str, object]:
    """Install or remove the login items. Returns status plus a message."""
    script = "install.sh" if want else "uninstall.sh"
    path = repo_root() / "scripts" / "macos" / script
    try:
        done = subprocess.run(
            ["bash", str(path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "message": "Autostart script failed: %s." % exc}
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip()[:300] or "script failed"
        return {"ok": False, "message": "Autostart script failed: %s." % detail}
    current = status()
    current["ok"] = True
    current["message"] = (
        "Mitra will start at login." if want else "Mitra autostart removed."
    )
    return current
