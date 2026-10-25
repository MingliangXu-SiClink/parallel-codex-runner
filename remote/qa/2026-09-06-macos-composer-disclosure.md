# macOS 0.1.326: Composer and Work Disclosure

## Problems and Changes

The work disclosure's transition snapshot was an ordinary transparent
`NSImageView`. The collection view and its scroll view also have transparent
backgrounds. During expansion, old glyphs in the snapshot were therefore drawn
over the newly inserted reasoning and command rows. Disabling animation alone
does not prevent this compositing error.

`TranscriptTransitionOverlay` now paints the resolved, opaque conversation
canvas before drawing the snapshot. It remains until the existing AppKit layout
and semantic scroll-anchor restoration finish. An attempted immediate removal
passed a top-of-document test but caused a real-client jump during repeated
toggles; that attempt was removed before publication. The regression now uses
history before the disclosure and starts near the document bottom.

The composer now separates writing from controls:

- Full-width native multiline editor above the toolbar, instead of squeezing
  text between two buttons.
- 16-point base text, 52-point minimum editor height, 200-point maximum height.
  Existing app text scaling and native marked-text handling remain in place.
- Bottom toolbar with input options on the left and the current model/effort,
  run settings entry, and a 36-point send control on the right.
- Plain arrow inside one circular send surface, rather than a circle symbol
  inside another circle; quieter focus outline and shadow.
- No microphone or permission controls were added without working features.
- Submission, queue, selected-branch adoption, and confirmation gates are reused
  unchanged. The settings entry exposes the model and effort to accessibility.

The supplied user screenshot is the visual reference. The implementation keeps
an AppKit text view for multiline input, consistent with Apple's
[text input guidance](https://developer.apple.com/design/human-interface-guidelines/text-fields).
The transition fix follows the actual drawing contract of
[NSView.isOpaque](https://developer.apple.com/documentation/appkit/nsview/isopaque).

## Verification

- The new native transition regression failed before the fix: the cover was
  non-opaque and its bitmap contained transparent pixels. After the fix, sampled
  alpha is greater than 0.999 in both appearances, the temporary cover is removed,
  the disclosure retains its viewport position, and inserted rows do not overlap.
- Protocol contract tests, desktop state tests, and all 18 native tests pass.
  The new editor test checks font, native placeholder/caret origin, selection,
  and preservation of marked text across a SwiftUI refresh.
- Final production build completed in 43.57 seconds. The installed application
  passes `codesign --verify --deep --strict`.
- Actual installed-client checks used the existing read-only
  `BackendRestartQA-20260906-A.` conversation. No messages were submitted.
- Repeated expansion/collapse, including four consecutive toggles, retained the
  control on screen. Thinking, commands, and the answer remained aligned.
- Chinese/English multiline draft entry, Shift-Return, selection replacement,
  undo, narrow-window wrapping, run-settings open/close, and quit/relaunch draft
  restoration were verified. Empty input disables send.
- Light/dark appearance and standard/extra-large text were inspected. Test draft
  was cleared; original workspace/conversation, dark appearance, and standard
  text size restored. Window changes made by the user were not reverted.
- In-app update check after publication reports 0.1.326 as current/latest.

Screenshots are under
`~/Library/Caches/ParallelCodexMaintenance/pcrd-20260906-dQFqCM/`:
`macos-326-composer-dark.jpg`, `macos-326-composer-light.jpg`,
`macos-326-composer-narrow.jpg`, and `macos-326-composer-zoom.jpg`.

The UI tool captures settled screenshots, not high-frame-rate video. The
transient compositing evidence comes from the native bitmap regression; this
report does not claim a frame-by-frame recording of every real-client scenario.
The pre-existing short AppKit settling interval is retained, not advertised as
a zero-latency disclosure implementation.

## Delivery

- Installed application: `/Applications/Parallel Codex.app`.
- Version/build: `0.1.326` / `326`.
- Private release: `macos-release-87c40792c76c41359c1ee147bb218f0a`.
- Published: `2026-09-06T05:38:03.301131Z`.
- Archive: `release/clients/macos/Parallel-Codex-macos-0.1.326.zip`.
- Archive SHA-256: `bcf43c7512d5a5ae6ad8d1a7aacc688406595b211c944cee3af8b8632dde53eb`.
- Archive bytes: `8066243`.
- Installed/archive executable SHA-256 (identical):
  `5cb198730fd018153cdca1c8287f052e8d3e49434ed95ff29afa7c2154512d8a`.
- Signing remains `Parallel Codex Local Signing`; this is not a notarized release.
- Android remains 0.1.259 (260), with its existing APK hash unchanged. No running
  backend service was changed or restarted. Unified bundle packaging regenerated
  wheel archives only; it did not deploy those archives.
- No Git commit, reset, or cleanup of the user's migration work was performed.
