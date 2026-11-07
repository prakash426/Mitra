#!/usr/bin/env bash
# Wait for the Mitra API, then open the HUD in the default browser.
# Usage: mitra-open.sh [url] [tries]
set -u

URL="${1:-http://127.0.0.1:8931}"
TRIES="${2:-60}"

i=0
while [ "$i" -lt "$TRIES" ]; do
  if curl -sf "$URL/health" >/dev/null 2>&1; then
    break
  fi
  i=$((i + 1))
  sleep 2
done
open "$URL"
