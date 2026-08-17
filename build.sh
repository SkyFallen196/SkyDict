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

# py2app 0.28 cannot sign the finished bundle itself, and macOS refuses to load an
# unsigned bundle's libraries at all, so this step is not optional.
#
# Note that an ad-hoc signature does NOT survive a rebuild: macOS tracks the app by the
# hash of its contents, which changes with every build, so the Accessibility grant has to
# be renewed each time (toggle SkyDict off and on in System Settings). Only a real
# signing identity gives a stable one — see README.
SIGN_IDENTITY="${SIGN_IDENTITY:--}"
if [[ "$SIGN_IDENTITY" == "-" ]]; then
    echo "==> Signing (ad-hoc; permissions must be re-granted after each build)"
else
    echo "==> Signing as '$SIGN_IDENTITY'"
fi
codesign --force --deep --sign "$SIGN_IDENTITY" "$APP"
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
