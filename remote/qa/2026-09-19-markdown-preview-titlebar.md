# Markdown Preview Titlebar Repair

Date: 2026-09-19

## Report and Findings

- The user's screenshot shows a persistent white rectangle in the top-left titlebar while a Markdown document is open in the right inspector. The user confirmed that closing the preview removes the rectangle.
- Markdown files use MarkdownUI inside the native SwiftUI inspector, not a web view or Quick Look. The reported rectangle is outside the document surface.
- `WindowConfigurationView.updateNSView` reconfigured native titlebar properties on every model refresh. A recording-window regression test demonstrated 84 property assignments after the initial configuration and 20 unchanged refreshes, rather than retaining the initial 4 assignments.
- The window backing used an unresolved dynamic color. Drawing it under a different appearance resolved a light backing for a dark window, or vice versa. Both appearance-isolation test cases failed before the change.
- The native inspector also controls toolbar presentation. The automatic toolbar backdrop is now explicitly hidden, consistent with this app's transparent titlebar, rather than leaving two independently managed backgrounds.

The exact screenshot rectangle was not reproduced consistently in the isolated 0.1.370 client. The tests above establish actual defects in the relevant titlebar path, not proof that every possible cause of a white rectangle has been eliminated.

## Changes

- Resolve the window backing color in its hosting view's effective appearance before assigning it to AppKit.
- Reapply the backing when that appearance changes, including an app-specific light/dark preference.
- Set titlebar properties only when their values actually change. Ordinary polling and preview refreshes no longer repeatedly invalidate the native chrome.
- Explicitly hide the automatic window-toolbar background while preserving native title, controls, resizing, and the resizable file inspector.
- Add regression coverage for redundant property assignments, cross-window appearance isolation, and changing the app appearance without resetting other titlebar properties.

No user Markdown documents, user workspace files, pcrd implementation, Android implementation, or Agent state was modified by this fix.

## Verification

- Before the fix: the new idempotency test failed with 84 versus 4 assignments; the two cross-appearance cases failed with a roughly 0.90 RGB difference.
- After the fix: 125 macOS tests in 18 suites passed, together with the protocol and desktop-state contract checks. The 11 release-bundle tests also passed. Native title/control/frame preservation remains covered.
- Signed release build succeeded with the macOS 26.5 SDK. Installed and archived bundle signatures passed strict verification.
- Used a separate native QA bundle and pcrd state. Opened a copy of the actual `pi0-exercises.md` through a real conversation file link, preserving the original document.
- Verified Markdown headings, Chinese text, and tables in dark and light appearances. Verified the light-mode preview in fullscreen and resized its native divider from position 1616 to 1423. The observed titlebar had no white rectangle.
- Some UI automation calls timed out or returned ScreenCaptureKit error -3811 after closing the separate Settings window. A process sample showed the main run loop mostly idle; a QA-only relaunch restored UI access. This is not claimed as a verified fix for that separate automation/appearance-transition issue.
- Restored the disposable QA Markdown file and stopped only the isolated QA client and daemon.

## Release and Installation

- Version: **macOS 0.1.371 (371)**.
- Private Relay release: `macos-release-80639f7c03c84403a9d32d1bebe96147`.
- Archive: `release/clients/macos/Parallel-Codex-macos-0.1.371.zip`.
- SHA-256: `ac5e08f818f04a22831dc86b6f0aa7687965633a317fc79dcad88846278404cf`; size: 8,502,677 bytes.
- Verified the paired controller's update check from build 370 and a private one-use-ticket download with the exact expected hash and size. Removed the temporary remote upload after publication.
- After explicit user approval, backed up 0.1.370, installed 0.1.371 into `/Applications/Parallel Codex.app`, and restarted only the client. Used exact-PID termination rather than ordinary Quit, which could otherwise auto-adopt a selected completed candidate.
- The installed client restored the siclab-next conversation, Agent 4 selection, result inspector, and two queued prompts. Synthesis candidates remained running with increasing usage.
- Production pcrd PID 69326 and Relay connection time `2026-09-19T03:33:08.470055+00:00` remained unchanged. Run `pcr-20260919T033400Z-fd40a814` stayed active. No Agent, web service, or production daemon was stopped.
- Android, pcrd, and Relay release-manifest entries are unchanged.

Build/test logs, signed application, and previous application backups are under `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/MarkdownPreview-20260919-PovavV`.
