#!/usr/bin/env bash
# Remove the Mitra macOS login service. Server logs are kept.
# Usage: bash scripts/macos/uninstall.sh
set -u

AGENTS_DIR="${MITRA_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
UID_NUM="$(id -u)"

launchctl bootout "gui/$UID_NUM/com.mitra.server" 2>/dev/null || true
launchctl bootout "gui/$UID_NUM/com.mitra.opener" 2>/dev/null || true
rm -f "$AGENTS_DIR/com.mitra.server.plist" "$AGENTS_DIR/com.mitra.opener.plist"

echo "Mitra login service removed. Logs kept under $HOME/Library/Logs/mitra."
