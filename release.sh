#!/usr/bin/env bash
#
# Build SkyDict.dmg and publish it to GitHub Releases.
#
#   ./release.sh                    build the DMG into dist/
#   ./release.sh --publish          also tag the commit and upload the DMG
#   ./release.sh --publish --draft  upload as a draft, to edit the notes before it is public
#   ./release.sh --skip-build       package the dist/SkyDict.app already built
#
set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${PYTHON:-$(command -v python3)}"
APP="dist/SkyDict.app"
VOLUME="SkyDict"

publish=false
draft=false
skip_build=false
for arg in "$@"; do
    case "$arg" in
        --publish) publish=true ;;
        --draft) draft=true ;;
        --skip-build) skip_build=true ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

# The version lives in three files that must agree: a tag pointing at a bundle whose
# Info.plist says something else is not worth debugging after the fact.
echo "==> Reading the version"
VERSION="$("$PYTHON" - <<'PY'
import pathlib
import re
import sys

SOURCES = {
    "skydict/__init__.py": r'^__version__ = "([^"]+)"',
    "pyproject.toml": r'^version = "([^"]+)"',
    "setup.py": r'^VERSION = "([^"]+)"',
}

found = {}
for path, pattern in SOURCES.items():
    match = re.search(pattern, pathlib.Path(path).read_text(encoding="utf-8"), re.M)
    found[path] = match.group(1) if match else None

if len(set(found.values())) != 1 or None in found.values():
    for path, version in found.items():
        print(f"  {path}: {version}", file=sys.stderr)
    sys.exit("Version strings disagree; make them match before releasing.")

print(next(iter(set(found.values()))))
PY
)"
DMG="dist/SkyDict-$VERSION-arm64.dmg"
echo "    $VERSION"

if $publish; then
    # Checked before the build so a dirty tree costs seconds rather than minutes.
    if ! git diff --quiet || ! git diff --cached --quiet; then
        echo "Working tree has uncommitted changes; commit them before releasing." >&2
        exit 1
    fi
fi

if $skip_build; then
    [[ -d "$APP" ]] || { echo "No $APP to package. Drop --skip-build." >&2; exit 1; }
    echo "==> Reusing $APP"
else
    ./build.sh
fi

# A bundle that fails this would install and then refuse to launch, and the DMG would
# look perfectly fine while doing it.
echo "==> Verifying the signature"
codesign --verify --deep --strict "$APP"

echo "==> Building $DMG"
# A leftover mount from an interrupted run makes hdiutil fail on the volume name.
[[ -d "/Volumes/$VOLUME" ]] && hdiutil detach "/Volumes/$VOLUME" -quiet || true

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
# ditto rather than cp: it is the copy that preserves everything codesign checks.
ditto "$APP" "$STAGE/SkyDict.app"
ln -s /Applications "$STAGE/Applications"

rm -f "$DMG"
hdiutil create \
    -volname "$VOLUME" \
    -srcfolder "$STAGE" \
    -ov -format UDZO -imagekey zlib-level=9 \
    "$DMG" >/dev/null

echo "==> Built $DMG ($(du -sh "$DMG" | cut -f1))"
shasum -a 256 "$DMG"

if ! $publish; then
    echo "==> Not publishing. Re-run with --publish to tag and upload."
    exit 0
fi

command -v gh >/dev/null || {
    echo "gh is not installed. Run: brew install gh && gh auth login" >&2
    exit 1
}

TAG="v$VERSION"
NOTES="$STAGE/notes.md"
cat > "$NOTES" <<NOTES_END
Requires macOS 11 or later on Apple Silicon.

### Install

1. Open the DMG and drag **SkyDict** into Applications.
2. SkyDict is signed ad-hoc rather than with an Apple Developer ID, so macOS quarantines
   it on download and reports it as *damaged*. Clear the quarantine flag once:

   \`\`\`bash
   xattr -dr com.apple.quarantine /Applications/SkyDict.app
   \`\`\`

3. Launch it — a 🎙 appears in the menubar.
4. Grant Accessibility in System Settings › Privacy & Security › Accessibility, then
   **restart SkyDict**. Without it the hotkey does nothing and dictations only reach the
   clipboard. The ad-hoc signature changes with every release, so this has to be redone
   after each update: toggle SkyDict off and on in that list.

Hold **right Option**, speak, release. The first run of the local backend downloads about
230 MB of model weights into \`~/SkyDict_models\`.

\`\`\`
$(shasum -a 256 "$DMG" | sed "s| dist/| |")
\`\`\`
NOTES_END

if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
    echo "==> Tag $TAG already exists"
else
    echo "==> Tagging $TAG"
    git tag -a "$TAG" -m "SkyDict $VERSION"
fi
git push origin "$TAG"

echo "==> Creating the release"
gh_args=(--title "SkyDict $VERSION" --notes-file "$NOTES")
$draft && gh_args+=(--draft)
gh release create "$TAG" "$DMG" "${gh_args[@]}"

echo "==> Published $TAG"
