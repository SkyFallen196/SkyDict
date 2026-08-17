#!/usr/bin/env bash
#
# Build SkyDict.app.
#
#   ./build.sh              build and sign
#   ./build.sh --install    also copy to /Applications
#
set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${PYTHON:-$(command -v python3)}"
APP="dist/SkyDict.app"

echo "==> Cleaning"
rm -rf build dist

echo "==> Building with py2app"
"$PYTHON" setup.py py2app

# py2app 0.28 cannot sign the finished bundle itself. The signature is not about trust
# here — an ad-hoc one is enough — but about identity: macOS keys the Accessibility and
# Microphone grants to it, and an unsigned bundle gets a new identity on every build,
# so the user would have to re-grant permission after each one.
echo "==> Signing (ad-hoc)"
codesign --force --deep --sign - "$APP"
codesign --verify --verbose=1 "$APP"

echo "==> Built $APP ($(du -sh "$APP" | cut -f1))"

if [[ "${1:-}" == "--install" ]]; then
    echo "==> Installing to /Applications"
    # Replacing a running app leaves macOS confused about which copy owns the
    # permissions, so stop it first.
    pkill -f "SkyDict.app/Contents/MacOS" 2>/dev/null || true
    rm -rf /Applications/SkyDict.app
    cp -R "$APP" /Applications/
    echo "==> Installed. Open it from Applications."
fi
