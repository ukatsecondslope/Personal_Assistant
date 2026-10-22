#!/usr/bin/env bash
# Build "Personal Assistant.app" in /Applications and put a shortcut to it on the Desktop.
# Re-run after moving the repo: the launcher has the repo path baked in.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP="${APP_DIR:-/Applications}/Personal Assistant.app"
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Personal Assistant</string>
  <key>CFBundleDisplayName</key><string>Personal Assistant</string>
  <key>CFBundleIdentifier</key><string>local.personal-assistant</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

# GUI apps don't get the shell PATH, so paths are absolute.
cat > "$APP/Contents/MacOS/launcher" <<LAUNCHER
#!/usr/bin/env bash
cd "$REPO" || exit 1
mkdir -p data
if ! .venv/bin/python -c "import webview" 2>/dev/null; then
  {
    [ -d .venv ] || "$UV" venv --python 3.12 .venv
    "$UV" pip install --python .venv/bin/python -r requirements.txt
  } >> data/desktop.log 2>&1 || {
    osascript -e 'display alert "Personal Assistant" message "Setup failed. See $REPO/data/desktop.log"'
    exit 1
  }
fi
exec .venv/bin/python -m app.desktop 2>> data/desktop.log
LAUNCHER
chmod +x "$APP/Contents/MacOS/launcher"

ICONSET="$(mktemp -d)/AppIcon.iconset"
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  sips -z $size $size "$REPO/assets/icon.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  sips -z $((size * 2)) $((size * 2)) "$REPO/assets/icon.png" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
rm -rf "$(dirname "$ICONSET")"

touch "$APP"  # make Finder pick up the new icon
ln -sfn "$APP" "$HOME/Desktop/Personal Assistant"
echo "Built $APP (shortcut on Desktop)"
