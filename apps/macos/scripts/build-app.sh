#!/bin/zsh

set -euo pipefail

SCRIPT_DIR=${0:A:h}
PROJECT_DIR=${SCRIPT_DIR:h}
CONFIGURATION=${CONFIGURATION:-release}
typeset -a SWIFT_BUILD_ARGS
SWIFT_BUILD_ARGS=()
if [[ -n ${PCR_MAC_BUILD_JOBS:-} ]]; then
    SWIFT_BUILD_ARGS+=(--jobs "$PCR_MAC_BUILD_JOBS")
fi
if [[ -n ${PCR_MAC_SDK:-} ]]; then
    SWIFT_BUILD_ARGS+=(--sdk "$PCR_MAC_SDK" --build-system native)
fi
OUTPUT_DIR=${PCR_APP_OUTPUT_DIR:-"$PROJECT_DIR/dist"}
APP_DIR="$OUTPUT_DIR/Parallel Codex.app"
BRAND_SOURCE="$PROJECT_DIR/../branding/parallel-codex-icon-1024.png"
LOCAL_SIGNING_DIR=${PCR_MAC_SIGNING_DIR:-"$HOME/Library/Application Support/Parallel Codex/Signing/Release"}
RCODESIGN=${PCR_MAC_RCODESIGN:-"$LOCAL_SIGNING_DIR/tools/rcodesign"}

[[ -f "$BRAND_SOURCE" ]] || {
    print -u2 -- "Missing canonical branding asset: $BRAND_SOURCE"
    exit 1
}

if [[ ${PCR_MAC_DISABLE_LOCAL_SIGNING:-0} != 1 ]]; then
    "$SCRIPT_DIR/ensure-local-signing.sh"
fi

if [[ ${CLEAN:-0} == 1 ]]; then
    swift package --package-path "$PROJECT_DIR" clean
fi
swift build --package-path "$PROJECT_DIR" -c "$CONFIGURATION" "${SWIFT_BUILD_ARGS[@]}"
BIN_DIR=$(swift build --package-path "$PROJECT_DIR" -c "$CONFIGURATION" "${SWIFT_BUILD_ARGS[@]}" --show-bin-path)

ICON_DIR="$PROJECT_DIR/.build/pcr-brand/AppIcon.iconset"
ICON_PATH="$PROJECT_DIR/.build/pcr-brand/AppIcon.icns"
rm -rf "$ICON_DIR"
mkdir -p "$ICON_DIR"

typeset -a ICON_SWIFT_ARGS
ICON_SWIFT_ARGS=()
if [[ -n ${PCR_MAC_SDK:-} ]]; then
    ICON_SWIFT_ARGS+=(-sdk "$PCR_MAC_SDK")
fi
swift "${ICON_SWIFT_ARGS[@]}" "$SCRIPT_DIR/build-app-icon.swift" "$BRAND_SOURCE" "$ICON_DIR"
iconutil --convert icns --output "$ICON_PATH" "$ICON_DIR"

mkdir -p "$OUTPUT_DIR"
find "$OUTPUT_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"
install -m 755 "$BIN_DIR/ParallelCodex" "$APP_DIR/Contents/MacOS/Parallel Codex"
if nm -u "$APP_DIR/Contents/MacOS/Parallel Codex" | \
    rg -q '_SecItem(CopyMatching|Add|Update|Delete)'; then
    print -u2 -- "The macOS client binary contains forbidden Keychain calls."
    exit 1
fi
install -m 644 "$PROJECT_DIR/Info.plist" "$APP_DIR/Contents/Info.plist"
if [[ -n ${PCR_MAC_VERSION_OVERRIDE:-} ]]; then
    /usr/libexec/PlistBuddy -c \
        "Set :CFBundleShortVersionString $PCR_MAC_VERSION_OVERRIDE" \
        "$APP_DIR/Contents/Info.plist"
fi
if [[ -n ${PCR_MAC_BUILD_OVERRIDE:-} ]]; then
    /usr/libexec/PlistBuddy -c \
        "Set :CFBundleVersion $PCR_MAC_BUILD_OVERRIDE" \
        "$APP_DIR/Contents/Info.plist"
fi
# A new resource name also invalidates AppKit's cached pre-normalization icon.
install -m 644 "$ICON_PATH" "$APP_DIR/Contents/Resources/ParallelCodex.icns"
install -m 644 "$BRAND_SOURCE" \
    "$APP_DIR/Contents/Resources/parallel-codex-icon-1024.png"
for resource_bundle in "$BIN_DIR"/*.bundle(N); do
    cp -R "$resource_bundle" "$APP_DIR/Contents/Resources/"
done
for localization in "$PROJECT_DIR/Sources/PCRRemoteMac/Resources"/*.lproj; do
    [[ -d "$localization" ]] || continue
    cp -R "$localization" "$APP_DIR/Contents/Resources/"
done
if [[ ${PCR_MAC_DISABLE_LOCAL_SIGNING:-0} == 1 ]]; then
    print -r -- "$APP_DIR"
    exit 0
fi
"$RCODESIGN" sign --config-file /dev/null --timestamp-url none --code-signature-flags runtime \
    --code-requirements-file "$LOCAL_SIGNING_DIR/requirements.csreq" \
    --pem-file <(openssl pkey -in "$LOCAL_SIGNING_DIR/private-key.pem" \
        -passin "file:$LOCAL_SIGNING_DIR/signing-password") \
    --pem-file "$LOCAL_SIGNING_DIR/certificate.pem" "$APP_DIR"
codesign --verify --deep --strict "$APP_DIR"

print -r -- "$APP_DIR"
