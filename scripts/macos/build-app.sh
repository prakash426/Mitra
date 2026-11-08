#!/usr/bin/env bash
# Build a Mitra.app bundle that launches the API plus HUD with one click.
# The app runs scripts/macos/mitra-launch.sh, so add Mitra.app to
# System Settings, General, Login Items to greet you at every login.
# Unsigned: first launch needs a right click, Open approval.
# Usage: bash scripts/macos/build-app.sh [output_dir]
# Test hooks: MITRA_REPO_DIR
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="${MITRA_REPO_DIR:-$(cd "$HERE/../.." && pwd)}"
OUT_DIR="${1:-$REPO_DIR/dist}"
APP="$OUT_DIR/Mitra.app"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

ICON_SRC="$REPO_DIR/desktop/src-tauri/icon.png"
if [ -f "$ICON_SRC" ]; then
  cp "$ICON_SRC" "$APP/Contents/Resources/icon.png"
fi

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>Mitra</string>
  <key>CFBundleDisplayName</key>
  <string>Mitra</string>
  <key>CFBundleIdentifier</key>
  <string>com.mitra.app</string>
  <key>CFBundleVersion</key>
  <string>0.2.0</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleExecutable</key>
  <string>Mitra</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/Mitra" <<LAUNCH
#!/usr/bin/env bash
exec /bin/bash "$REPO_DIR/scripts/macos/mitra-launch.sh"
LAUNCH
chmod +x "$APP/Contents/MacOS/Mitra"

if command -v plutil >/dev/null 2>&1; then
  plutil -lint "$APP/Contents/Info.plist" >/dev/null
fi

echo "Mitra.app built at $APP"
echo "Drag it to /Applications, then add it under Login Items to autostart."
