#!/usr/bin/env bash
# Build the macOS app + .dmg (UNSIGNED). Runs on a macOS machine / GitHub Actions
# macos-latest runner. Produces dist/Burnmeter.dmg (stable name) + a versioned copy.
#
# pywebview on macOS uses the Cocoa/WebKit backend (pyobjc), not WebView2 — so the
# build deps differ from Windows. Unsigned → Gatekeeper "can't verify developer"
# (right-click → Open) until Apple Developer signing/notarization.
set -euo pipefail
cd "$(dirname "$0")/.."

VER=$(python3 -c "import re;print(re.search(r'__version__\s*=\s*\"([^\"]+)\"',open('burnmeter/__init__.py').read()).group(1))")
echo "Building Burnmeter $VER for macOS"

python3 -m pip install --quiet --upgrade pip pyinstaller pyinstaller-hooks-contrib
python3 -m pip install --quiet ".[app,sync]"

# .icns from the shipped PNG (sips + iconutil are built into macOS).
rm -rf build/icon.iconset; mkdir -p build/icon.iconset
SRC="burnmeter/assets/burnmeter.png"
for s in 16 32 64 128 256 512; do
  sips -z "$s" "$s"     "$SRC" --out "build/icon.iconset/icon_${s}x${s}.png"   >/dev/null
  d=$((s*2)); sips -z "$d" "$d" "$SRC" --out "build/icon.iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns build/icon.iconset -o build/burnmeter.icns

python3 -m PyInstaller --noconfirm --clean --name Burnmeter --windowed \
  --icon build/burnmeter.icns \
  --osx-bundle-identifier dev.burnmeter.app \
  --paths . \
  --collect-all webview \
  --collect-all cryptography \
  --collect-all pystray \
  --collect-all PIL \
  --collect-submodules burnmeter \
  --hidden-import webview.platforms.cocoa \
  --hidden-import pystray._darwin \
  --add-data "burnmeter/static:burnmeter/static" \
  --add-data "burnmeter/assets:burnmeter/assets" \
  packaging/burnmeter_app.py

# Package a drag-to-Applications .dmg.
rm -rf dist/dmg; mkdir -p dist/dmg
cp -R "dist/Burnmeter.app" "dist/dmg/Burnmeter.app"
ln -s /Applications "dist/dmg/Applications"
hdiutil create -volname "Burnmeter" -srcfolder dist/dmg -ov -format UDZO "dist/Burnmeter.dmg"
cp "dist/Burnmeter.dmg" "dist/Burnmeter-${VER}.dmg"
echo "Built dist/Burnmeter.dmg  +  dist/Burnmeter-${VER}.dmg"
