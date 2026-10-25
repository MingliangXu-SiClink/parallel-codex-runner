#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TEMP=$(mktemp -d "${TMPDIR:-/tmp}/pcr-app-icon.XXXXXX")
trap 'rm -rf -- "$TEMP"' EXIT HUP INT TERM

if [ -n "${PCR_MAC_SDK:-}" ]; then
    export SDKROOT="$PCR_MAC_SDK"
fi
swift "$ROOT/scripts/build-app-icon.swift" \
    "$ROOT/../branding/parallel-codex-icon-1024.png" "$TEMP/AppIcon.iconset"
iconutil --convert icns --output "$TEMP/AppIcon.icns" "$TEMP/AppIcon.iconset"
swift "$ROOT/Tests/AppIcon/main.swift" "$TEMP" "$ROOT/Info.plist"
