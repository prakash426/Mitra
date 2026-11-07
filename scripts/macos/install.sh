#!/usr/bin/env bash
# Install Mitra as a macOS login service.
# At every login the API server starts and the HUD opens in your browser.
# Sleep needs no action: the server and your tabs survive it, so Mitra is
# already there when the Mac wakes.
#
# Usage: bash scripts/macos/install.sh
# Test hooks: MITRA_REPO_DIR, MITRA_AGENTS_DIR, MITRA_SKIP_BOOTSTRAP=1
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="${MITRA_REPO_DIR:-$(cd "$HERE/../.." && pwd)}"
AGENTS_DIR="${MITRA_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
LOGS_DIR="$HOME/Library/Logs/mitra"
PYTHON="$REPO_DIR/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
  echo "Mitra install failed: no venv python at $PYTHON"
  echo "Create it first: python3 -m venv .venv && .venv/bin/pip install -e \".[dev]\""
  exit 1
fi

PORT="8931"
if [ -f "$REPO_DIR/config.toml" ]; then
  PORT="$(python3 -c 'import tomllib,sys; print(tomllib.load(open(sys.argv[1],"rb")).get("api",{}).get("port",8931))' "$REPO_DIR/config.toml" 2>/dev/null || echo 8931)"
else
  echo "Note: no config.toml yet. Copy config.example.toml to config.toml first."
fi
URL="http://127.0.0.1:$PORT"

mkdir -p "$AGENTS_DIR" "$LOGS_DIR"

cat > "$AGENTS_DIR/com.mitra.server.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.mitra.server</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>-m</string>
    <string>mitra.api</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$REPO_DIR</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$LOGS_DIR/server.log</string>
  <key>StandardErrorPath</key>
  <string>$LOGS_DIR/server.log</string>
</dict>
</plist>
PLIST

cat > "$AGENTS_DIR/com.mitra.opener.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.mitra.opener</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$HERE/mitra-open.sh</string>
    <string>$URL</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <false/>
</dict>
</plist>
PLIST

if command -v plutil >/dev/null 2>&1; then
  plutil -lint "$AGENTS_DIR/com.mitra.server.plist" >/dev/null
  plutil -lint "$AGENTS_DIR/com.mitra.opener.plist" >/dev/null
fi

if [ "${MITRA_SKIP_BOOTSTRAP:-0}" = "1" ]; then
  echo "Plists written to $AGENTS_DIR (bootstrap skipped)."
  exit 0
fi

UID_NUM="$(id -u)"
launchctl bootout "gui/$UID_NUM/com.mitra.server" 2>/dev/null || true
launchctl bootout "gui/$UID_NUM/com.mitra.opener" 2>/dev/null || true
launchctl bootstrap "gui/$UID_NUM" "$AGENTS_DIR/com.mitra.server.plist"
launchctl bootstrap "gui/$UID_NUM" "$AGENTS_DIR/com.mitra.opener.plist"

echo "Mitra installed. The HUD opens at $URL shortly, and at every login."
echo "Server log: $LOGS_DIR/server.log"
