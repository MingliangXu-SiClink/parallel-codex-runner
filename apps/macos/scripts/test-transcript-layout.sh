#!/bin/bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
DEVELOPER_DIR=$(xcode-select -p)
# AppKit suites share NSApplication and its main run loop. Focus-changing
# interaction suites are opt-in so ordinary checks never interrupt desktop work.
ARGS=(--package-path "$ROOT" --disable-xctest --enable-swift-testing --no-parallel --filter 'TranscriptLayoutTests|ClientTransportTests|EditorInteractionTests|ReviewWorkspaceTests|SidebarLayoutTests')
if [[ "${PCR_INTERACTIVE_QA_AUTHORIZED:-0}" != "1" ]]; then
  ARGS+=(--skip 'EditorInteractionTests|InteractionContractTests|TranscriptScrollPublicationTests|NativeMotionTests')
  while IFS= read -r interactive_file; do
    case "$interactive_file" in
      */EditorInteractionTests.swift|*/InteractionContractTests.swift|*/TranscriptScrollPublicationTests.swift|*/NativeMotionTests.swift) ;;
      *) printf '%s\n' "Unclassified foreground test: $interactive_file" >&2; exit 1 ;;
    esac
  done < <(rg -l '\.activate\(|\.orderFront|\.makeKeyAndOrderFront|CGEventPost|\.post\(tap:|NSWorkspace\.shared\.open|\.setActivationPolicy' "$ROOT/Tests" || true)
fi
if [[ -n "${PCR_MAC_BUILD_JOBS:-}" ]]; then
  ARGS+=(--jobs "$PCR_MAC_BUILD_JOBS")
fi
if [[ -n "${PCR_MAC_SDK:-}" ]]; then
  ARGS+=(--sdk "$PCR_MAC_SDK" --build-system native)
fi

# Command Line Tools ships Swift Testing outside the default framework search
# path. Full Xcode supplies these paths through SwiftPM automatically.
if [[ "$DEVELOPER_DIR" == */CommandLineTools ]]; then
  FRAMEWORKS="$DEVELOPER_DIR/Library/Developer/Frameworks"
  ARGS+=(
    -Xswiftc -F -Xswiftc "$FRAMEWORKS"
    -Xlinker -rpath -Xlinker "$FRAMEWORKS"
    -Xlinker -rpath -Xlinker "$DEVELOPER_DIR/Library/Developer/usr/lib"
  )
fi

swift test "${ARGS[@]}"
