#!/bin/zsh

set -euo pipefail

SCRIPT_DIR=${0:A:h}
PROJECT_DIR=${SCRIPT_DIR:h}
APP_SOURCE="$PROJECT_DIR/dist/Parallel Codex.app"
SKIP_LAUNCH=0
[[ ${1:-} == --skip-launch ]] && SKIP_LAUNCH=1

temporary=$(mktemp -d "${TMPDIR:-/tmp}/parallel-codex-upgrade.XXXXXX")
installed="$temporary/Applications/Parallel Codex.app"
backup="$temporary/original/Parallel Codex.app"
had_original=0

if [[ -d "$APP_SOURCE" ]]; then
    mkdir -p "$backup:h"
    ditto "$APP_SOURCE" "$backup"
    had_original=1
fi

cleanup() {
    rm -rf "$APP_SOURCE"
    if (( had_original )); then
        mkdir -p "$APP_SOURCE:h"
        ditto "$backup" "$APP_SOURCE"
    fi
    rm -rf "$temporary"
}
trap cleanup EXIT INT TERM

signature_requirement() {
    codesign -d -r- "$1" 2>&1 | sed -n 's/^designated => //p'
}

signature_authority() {
    codesign -dv --verbose=4 "$1" 2>&1 | sed -n 's/^Authority=//p' | head -1
}

verify_bundle() {
    local app=$1
    codesign --verify --deep --strict --verbose=2 "$app"
    [[ $(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' \
        "$app/Contents/Info.plist") == com.parallelcodexrunner.mac ]]
    [[ -z $(xattr -p com.apple.quarantine "$app" 2>/dev/null || true) ]]
}

launch_once() {
    local app=$1
    (( SKIP_LAUNCH )) && return 0
    "$app/Contents/MacOS/Parallel Codex" --pcr-resource-smoke-test &
    local opener=$!
    local ticks=0
    while kill -0 "$opener" 2>/dev/null; do
        if (( ticks >= 120 )); then
            kill "$opener" 2>/dev/null || true
            wait "$opener" 2>/dev/null || true
            print -u2 -- "Installed app did not finish its smoke launch."
            return 1
        fi
        sleep 0.1
        (( ticks += 1 ))
    done
    wait "$opener"
}

launch_ui_once() {
    local app=$1
    (( SKIP_LAUNCH )) && return 0
    local marker="$temporary/ui-ready-$(uuidgen)"
    PCR_MAC_UI_SMOKE_MARKER="$marker" \
        "$app/Contents/MacOS/Parallel Codex" --pcr-ui-smoke-test &
    local opener=$!
    local ticks=0
    while kill -0 "$opener" 2>/dev/null; do
        if (( ticks >= 120 )); then
            kill "$opener" 2>/dev/null || true
            wait "$opener" 2>/dev/null || true
            print -u2 -- "Installed app did not render a responsive window."
            return 1
        fi
        sleep 0.1
        (( ticks += 1 ))
    done
    wait "$opener"
    [[ -f "$marker" ]] || {
        print -u2 -- "Installed app exited before its first window became ready."
        return 1
    }
}

base_build=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' \
    "$PROJECT_DIR/Info.plist")
first_build=$(( base_build + 100000 ))
second_build=$(( first_build + 1 ))

PCR_MAC_BUILD_OVERRIDE=$first_build "$SCRIPT_DIR/build-app.sh" >/dev/null
verify_bundle "$APP_SOURCE"
first_requirement=$(signature_requirement "$APP_SOURCE")
first_authority=$(signature_authority "$APP_SOURCE")
[[ -n "$first_requirement" && -n "$first_authority" ]]

mkdir -p "$installed:h"
ditto "$APP_SOURCE" "$installed"
verify_bundle "$installed"
launch_once "$installed"
launch_once "$installed"
launch_ui_once "$installed"
launch_ui_once "$installed"

PCR_MAC_BUILD_OVERRIDE=$second_build "$SCRIPT_DIR/build-app.sh" >/dev/null
verify_bundle "$APP_SOURCE"
second_requirement=$(signature_requirement "$APP_SOURCE")
second_authority=$(signature_authority "$APP_SOURCE")

[[ "$first_requirement" == "$second_requirement" ]] || {
    print -u2 -- "The designated requirement changed between app builds."
    exit 1
}
[[ "$first_authority" == "$second_authority" ]] || {
    print -u2 -- "The signing authority changed between app builds."
    exit 1
}

rm -rf "$installed"
ditto "$APP_SOURCE" "$installed"
verify_bundle "$installed"
[[ $(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' \
    "$installed/Contents/Info.plist") == "$second_build" ]]
launch_once "$installed"
launch_once "$installed"
launch_ui_once "$installed"
launch_ui_once "$installed"

print -r -- "Installed launch and upgrade verification passed ($first_authority)."
