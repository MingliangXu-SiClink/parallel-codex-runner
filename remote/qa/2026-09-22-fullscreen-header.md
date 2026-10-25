# macOS fullscreen header acceptance

Date: 2026-09-22 (Asia/Shanghai)
Delivered: macOS 0.1.381 (381), privately published, installed, and restarted.
Scope: macOS window chrome only. No pcrd, Relay, Android, Agent, or web-service
restart was performed.

## Cause and repair

The conversation already drew its own header, while a transparent SwiftUI
NSToolbar supplied only the sidebar toggle. In fullscreen, AppKit moves the
native toolbar into a separate 52-point window. The existing appearance repair
updated the main window backing, not this additional surface.

The original failure was intermittent in the formal client. An isolated native
reproduction using the same split/header/toolbar arrangement produced a
persistent, full-width white strip when changing toolbar visibility in
fullscreen. Making the main window non-opaque did not fix it. Painting the
toolbar explicitly covered the custom conversation title instead. Neither
workaround was shipped.

The repair removes the redundant NSToolbar from conversation windows and uses
one content header. It retains native window controls and the semantic window
title. The sidebar toggle now belongs to that header, with normal-window space
reserved for the traffic lights and a smaller inset in fullscreen. Native
window notifications update this presentation state without involving Agent
polling or the conversation model. Both split panes extend behind the native
title area; the existing sidebar widths and split persistence are unchanged.

The header uses public AppKit dragging, zoom, and minimize operations. No
private titlebar hierarchy is traversed, and no timer repaints the window.

## Verification

- Production build succeeded in 43.03 seconds using the installed macOS 26.5
  SDK and the repository build script. Existing local signing identity reused;
  strict/deep code-signature checks passed before and after installation.
- `test-transcript-layout.sh`: 175 tests in 25 suites passed in 99.912 seconds.
  New regressions cover window-scoped fullscreen observation, attaching and
  detaching windows, stable header size, absence of a duplicate toolbar, and
  preservation of native controls. Existing window-color, sidebar, editor,
  transcript, inspector, and transport tests also passed.
- Native walkthrough used a separate signed test bundle, isolated state
  directory, and IPC fixture that cannot start real Agents. Verified normal
  and fullscreen windows in light/dark appearance, repeated enter/exit,
  sidebar hide/show, candidate switching, result details, and adjacent file
  review. Conversation title and controls remained visible; no white overlay
  remained in these checks.
- The initial candidate retained an extra normal-window top inset. The native
  walkthrough caught it; both split-pane safe areas were corrected, then the
  final binary was rebuilt and the full layout suite rerun.
- Installed formal client restored the existing SiClab conversation. Entered
  dark fullscreen and inspected a real screenshot: the header was dark and
  unobstructed; the original Agent remained running. No production prompt or
  stop/adopt operation was submitted during this check.

## Honest test limits

The broader `test-client.sh` command did not pass its separate protocol-contract
binary: its existing persistent-cache test encountered Cocoa error 513 / EPERM
when requesting `.atomic` plus `.completeFileProtection`. A standalone Foundation
probe confirmed that plain and atomic writes succeed on this host, while the
file-protected write fails. Changing TMPDIR did not resolve it. Cache code and
its protection policy were not changed to force a green result. This failure
is not counted as a passing test above.

An initial preload timing test failed under overlapping build activity; the
two subsequent complete layout-suite runs both passed. Drag/double-click manual
acceptance could not be completed after fullscreen Space switching because the
computer-use tool returned `noWindowsAvailable` for coordinate actions. Native
fullscreen buttons and the View-menu exit action were exercised successfully;
manual drag smoothness is not claimed as verified.

## Deployment and continuity

- Private release: `macos-release-74a41cf27ea64df3b3c84e1081f3243f`, active.
- Archive: `release/clients/macos/Parallel-Codex-macos-0.1.381.zip`
- Archive SHA-256:
  `0209c9cf9e05a357a35c334702de368978f49047fd3497547417e40ca3cc8865`
- Installed app: `/Applications/Parallel Codex.app`, version 0.1.381 (381).
- Installed executable matches the built executable:
  `a009d1a97da744367316c6a495902f6e93e97663c00275fa4c3d25d252178fe1`.
- The old app and source snapshots are retained in
  `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/FullscreenChrome-20260922-dFT92V`.
- Only the macOS entry and archive record changed in the release catalog.
- pcrd PID 45254, persistent worker PID 45755, and web watcher PID 21279 kept
  their original start times. Run `pcr-20260922T001727Z-efd8be30` remains active;
  pcrd health is OK and `http://127.0.0.1:8811` returns HTTP 200.
- The isolated QA client and fixture server were shut down. Private remote
  upload staging was removed after release verification.

## References

- [Apple: titlebarAppearsTransparent](https://developer.apple.com/documentation/appkit/nswindow/titlebarappearstransparent)
- [Apple: SwiftUI toolbars](https://developer.apple.com/documentation/swiftui/toolbars)
