# macOS 0.1.368: sidebar header

## Scope

Match the supplied reference's quiet, integrated upper-left navigation without
changing workspace selection, conversation routing, or the resizable sidebar
sections.

- Remove the circular shared background from the sidebar toggle on macOS 26.
- Retain the native toolbar and window controls; remove the toolbar separator and
  let the sidebar surface continue into the titlebar.
- Remove the duplicate app icon from the sidebar header, increase the product
  menu's prominence, and align its chevron with the search affordance.
- Darken the sidebar surface; keep the existing light-mode palette.
- Keep the native conversation-title implementation unchanged. Do not add
  decorative back/forward or notification buttons without corresponding features.

The shared-background API is availability-guarded; earlier macOS versions retain
their native toolbar behavior. Reference: [Apple ToolbarContent API](https://developer.apple.com/documentation/swiftui/toolbarcontent/sharedbackgroundvisibility%28_%3A%29).

## Verification

- Built and signed the production app with the repository build script and the
  existing `Parallel Codex Local Signing` identity, using macOS SDK 26.5.
- Final `test-client.sh`: 118 tests in 18 suites passed, 51.368 seconds.
- Added native-window regression coverage for transparent titlebar configuration:
  repeated configuration preserves frame, content view, style mask, semantic
  title, window controls, live-resize behavior, and visibility.
- `codesign --verify --deep --strict` and executable smoke test passed.
- Launched the compiled client in an isolated preview bundle and home directory,
  on macOS 26.6.2. Inspected dark and light modes, a 228-point sidebar, the minimum
  window width, enlarged text, the product-menu popover, sidebar toggling, and
  divider/window resizing. The sidebar toggle is no longer a circular button;
  the header no longer has a separate colored app icon or horizontal toolbar seam.
- Enlarged text at minimum sidebar width keeps Search visible; the product name
  can still truncate rather than overlap controls. Standard-size text fits.

The UI preview uses `--pcr-ui-smoke-test`, which intentionally skips real account
hydration. Its connecting placeholder is not evidence of a Relay failure. No live
messages were sent, no workspace was changed, and no backend was restarted.

## Release And Delivery

- Version/build: `0.1.368 / 368`.
- Private Relay release: `macos-release-ddce7e28fff3435eb117be666ca15e0f`.
- Published: `2026-09-18T18:09:47.023044+00:00`.
- ZIP size: `8487965` bytes.
- SHA-256: `2485fb3030fab5e3cdfebc33323985a138b435681e52bb8938ed21ab593e1c8f`.
- Authenticated update check from the paired desktop, reporting current build
  367, returned build 368 as available. Requested a device-bound download ticket,
  downloaded through pcrd, and verified the downloaded size, SHA-256, extracted
  bundle version, complete signature, signing authority, and designated
  requirement against the catalog and locally built archive.
- No public download route was introduced. The running `/Applications` client
  remains 0.1.367; restart/install approval has not been received for this turn.

## Remaining Observation

Repeated sidebar toggles in the isolated loading-state window can intermittently
leave the conversation toolbar title undrawn until another repaint. The same
behavior was reproduced using a separately signed copy of the installed 0.1.367
binary. Experiments involving toolbar-title relocation and forced repaint were
discarded; the release retains the original title implementation rather than
claiming those experiments fixed the problem. This still needs investigation in
a hydrated conversation window and is not counted as fixed by this release.

Build, test logs, source-before backups, and the release archive are kept outside
the repository at:

`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/SidebarChrome-20260919-IEf6oe`
