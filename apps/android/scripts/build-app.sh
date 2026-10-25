#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)
BUILD_TYPE=${BUILD_TYPE:-debug}

case "$BUILD_TYPE" in
  debug)
    GRADLE_TASK=:app:assembleDebug
    ;;
  release)
    GRADLE_TASK=:app:assembleRelease
    ;;
  *)
    printf 'BUILD_TYPE must be debug or release, got: %s\n' "$BUILD_TYPE" >&2
    exit 2
    ;;
esac

if [[ -z ${JAVA_HOME:-} ]]; then
  candidates=(
    /opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
    /usr/local/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
    "/Applications/Android Studio.app/Contents/jbr/Contents/Home"
  )
  if command -v brew >/dev/null 2>&1; then
    brew_prefix=$(brew --prefix openjdk@17 2>/dev/null || true)
    [[ -z "$brew_prefix" ]] || candidates+=(
      "$brew_prefix/libexec/openjdk.jdk/Contents/Home"
    )
  fi
  java_home=$(/usr/libexec/java_home -v 17 2>/dev/null || true)
  [[ -z "$java_home" ]] || candidates+=("$java_home")
  for candidate in "${candidates[@]}"; do
    if [[ -x "$candidate/bin/java" ]]; then
      export JAVA_HOME=$candidate
      break
    fi
  done
fi

if [[ ! -x ${JAVA_HOME:-}/bin/java ]]; then
  printf 'Java 17 is required to build the Android app.\n' >&2
  exit 1
fi

cd "$PROJECT_DIR"
./gradlew "$GRADLE_TASK"

APK_DIR="$PROJECT_DIR/app/build/outputs/apk/$BUILD_TYPE"
APK_PATH=$(find "$APK_DIR" -maxdepth 1 -type f -name '*.apk' -print -quit)
if [[ -z "$APK_PATH" ]]; then
  printf 'No APK was produced in %s\n' "$APK_DIR" >&2
  exit 1
fi

OUTPUT_DIR=${PCR_APP_OUTPUT_DIR:-"$PROJECT_DIR/dist"}
OUTPUT_PATH="$OUTPUT_DIR/Parallel Codex.apk"
STAGED_PATH="$OUTPUT_DIR/.Parallel Codex.apk.tmp"
mkdir -p "$OUTPUT_DIR"

property_value() {
  local key=$1
  local file=$2
  awk -v key="$key" '
    index($0, key "=") == 1 {
      print substr($0, length(key) + 2)
      exit
    }
  ' "$file"
}

KEYSTORE_PROPERTIES="$PROJECT_DIR/keystore.properties"
SIGNING_LINEAGE=""
if [[ "$BUILD_TYPE" == release && -f "$KEYSTORE_PROPERTIES" ]]; then
  SIGNING_LINEAGE=$(property_value signingLineageFile "$KEYSTORE_PROPERTIES")
fi

if [[ -n "$SIGNING_LINEAGE" ]]; then
  [[ -f "$SIGNING_LINEAGE" ]] || {
    printf 'Android signing lineage does not exist: %s\n' "$SIGNING_LINEAGE" >&2
    exit 1
  }
  SDK_DIR=$(property_value sdk.dir "$PROJECT_DIR/local.properties")
  APKSIGNER=$(find "$SDK_DIR/build-tools" -maxdepth 2 -type f -name apksigner \
    -print | sort | tail -1)
  [[ -x "$APKSIGNER" ]] || {
    printf 'Could not find apksigner under %s/build-tools\n' "$SDK_DIR" >&2
    exit 1
  }

  RELEASE_STORE_FILE=$(property_value storeFile "$KEYSTORE_PROPERTIES")
  RELEASE_KEY_ALIAS=$(property_value keyAlias "$KEYSTORE_PROPERTIES")
  export PCR_ANDROID_STORE_PASSWORD
  export PCR_ANDROID_KEY_PASSWORD
  PCR_ANDROID_STORE_PASSWORD=$(property_value storePassword "$KEYSTORE_PROPERTIES")
  PCR_ANDROID_KEY_PASSWORD=$(property_value keyPassword "$KEYSTORE_PROPERTIES")
  rm -f -- "$STAGED_PATH"
  "$APKSIGNER" sign \
    --out "$STAGED_PATH" \
    --ks "$RELEASE_STORE_FILE" \
    --ks-key-alias "$RELEASE_KEY_ALIAS" \
    --ks-pass env:PCR_ANDROID_STORE_PASSWORD \
    --key-pass env:PCR_ANDROID_KEY_PASSWORD \
    --lineage "$SIGNING_LINEAGE" \
    --min-sdk-version 28 \
    --rotation-min-sdk-version 28 \
    --v1-signing-enabled false \
    --v2-signing-enabled false \
    --v3-signing-enabled true \
    --v4-signing-enabled false \
    "$APK_PATH"
  "$APKSIGNER" verify --min-sdk-version 28 "$STAGED_PATH"
  unset PCR_ANDROID_STORE_PASSWORD PCR_ANDROID_KEY_PASSWORD
else
  install -m 644 "$APK_PATH" "$STAGED_PATH"
fi
chmod 644 "$STAGED_PATH"
find "$OUTPUT_DIR" -mindepth 1 -maxdepth 1 ! -name "$(basename "$STAGED_PATH")" \
  -exec rm -rf -- {} +
mv -f "$STAGED_PATH" "$OUTPUT_PATH"
printf '%s\n' "$OUTPUT_PATH"
