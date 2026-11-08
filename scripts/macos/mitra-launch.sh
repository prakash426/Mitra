#!/usr/bin/env bash
# Launch Mitra with one command: ensure the local API runs, then open the
# desktop shell when it is built, else the HUD in the default browser.
# Usage: bash scripts/macos/mitra-launch.sh
# Test hooks: MITRA_REPO_DIR, MITRA_SKIP_OPEN=1, MITRA_HEALTH_TRIES=N
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="${MITRA_REPO_DIR:-$(cd "$HERE/../.." && pwd)}"
PYTHON="$REPO_DIR/.venv/bin/python"
LOGS_DIR="$HOME/Library/Logs/mitra"
TRIES="${MITRA_HEALTH_TRIES:-12}"

PORT="8931"
if [ -f "$REPO_DIR/config.toml" ]; then
  PORT="$(python3 -c 'import tomllib,sys; print(tomllib.load(open(sys.argv[1],"rb")).get("api",{}).get("port",8931))' "$REPO_DIR/config.toml" 2>/dev/null || echo 8931)"
fi
URL="http://127.0.0.1:$PORT"
DESKTOP_BIN="$REPO_DIR/desktop/src-tauri/target/debug/mitra-desktop"

mkdir -p "$LOGS_DIR"

api_up() {
  curl -sf "$URL/health" >/dev/null 2>&1
}

if ! api_up; then
  if [ ! -x "$PYTHON" ]; then
    echo "Mitra launch failed: no venv python at $PYTHON"
    exit 1
  fi
  nohup "$PYTHON" -m mitra.api >>"$LOGS_DIR/server.log" 2>&1 &
  echo "Mitra API starting, log at $LOGS_DIR/server.log"
fi

i=0
while [ "$i" -lt "$TRIES" ]; do
  if api_up; then
    break
  fi
  i=$((i + 1))
  sleep 2
done

if ! api_up; then
  echo "Mitra API did not answer at $URL/health, opening anyway."
fi

if [ "${MITRA_SKIP_OPEN:-0}" = "1" ]; then
  echo "Mitra ready at $URL (open skipped)."
  exit 0
fi

if [ -x "$DESKTOP_BIN" ]; then
  echo "Opening Mitra desktop shell."
  "$DESKTOP_BIN" >>"$LOGS_DIR/desktop.log" 2>&1 &
  exit 0
fi

APP="/Applications/Mitra.app"
if [ -d "$APP" ]; then
  echo "Opening Mitra desktop app."
  open "$APP"
  exit 0
fi

echo "Opening Mitra HUD at $URL."
open "$URL"
