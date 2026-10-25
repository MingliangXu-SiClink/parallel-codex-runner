# macOS 0.1.448: Compact, Window-Bounded Execution Editor

## Scope and Source Changes

This is a macOS-only UI change. It does not change pcrd, Relay, Android, Agent
execution, research files, task admission, adoption, or computation concurrency.

- The collapsed summary pairs each role's count with its own model, in candidate
  then synthesis order. Identical models are still shown twice; disabling
  synthesis does not hide its configured model. The entry remains content-width,
  without a full-row hover fill, and its chevron points upward.
- The normal editor is 368 logical points wide instead of 480, before display
  zoom. Stage spacing is 16 instead of 32 points. Locked synthesis shows its
  actual run configuration with a small lock and disabled gray controls. Scope
  explanations remain help/accessibility hints, not permanent labels.
- Count presets, steps, and valid numeric input apply immediately. There is no
  confirmation/checkmark button. Invalid or out-of-range input cannot change
  settings; reflow and Enter do not duplicate an edit. Returning to the initial
  value still applies correctly when the model callback is delayed.
- The editor and its count/model/reasoning/Fast controls share one upward-growing
  native surface. Unlike a native popover, it cannot flip below the summary or
  use screen bounds in place of window bounds. Placement is constrained to the
  window's content layout rectangle, with bounded scrolling for long content.
  It resizes without replacing the original message editor. The surface is a
  sibling of the SwiftUI hosting view so native hit-testing reaches it.
- Existing edit-context checks (machine, workspace, run, navigation generation),
  synthesis locking, persistence, and live synthesis-update semantics remain in
  place. Editing configuration does not send a prompt or adopt a result.

Relevant sources are `ComposerExecutionConfiguration.swift`, the new
`ExecutionConfigurationPresentation.swift`, the focused native tests, the
execution-editor contract guard, and the macOS bundle version.

## Verification

- Both existing protocol/desktop-state contract executables pass, as do the
  branding checks, signing-store regression, and explicitly unsigned-build gate.
- **49 relevant native tests in six suites pass**: execution configuration,
  native placement, model options, Agent questions, client lifecycle, and signed
  update verification/installation behavior.
- Offscreen native render evidence covers Chinese/English, dark/light, all
  display zoom levels, narrow/wide summaries, and editable/locked synthesis.
  Window-bounded placement, native hit-testing, child-content growth without a
  parent refresh, long-editor scrolling, resize, and removal are exercised
  without ordering or activating a QA window. Numeric editing is exercised
  through the native text field. These are not production-message tests.
- The unrestricted all-suite command is **not claimed as passing**. Two bounded
  attempts stopped advancing in existing loopback fixture polling paths
  (`PromptDeliveryTests.Fixture` and `AutomaticPaginationTests.Fixture`). Their
  owned QA helper processes were sampled and normally terminated; no production
  client, owner, Agent, or computation process was stopped. The passing scoped
  gate runs the unchanged contract prefix plus the six relevant suites rather
  than concealing these incomplete broad attempts.

Private frozen-source hashes, test logs, samples, screenshots, signed artifact
metadata, publication/readback, and installation evidence are recorded under
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/ExecutionPopover-20261018/`.
No credential or one-use update ticket is stored in this evidence.

## Release and Activation

- **Built and signed:** 0.1.448/build 448 uses the same existing offline encrypted
  `Parallel Codex Release Signing` identity as installed 447. Final archive
  unpack, strict/deep signature, publisher, version, and packaged rendering
  checks pass. No Keychain, trust, or updater-verification change was made.
- **Published:** at `2026-10-18T02:27:22Z`, the private catalog accepted
  `macos-release-74733086aeea4c0985be84d684432490`. Independent readback agrees on
  version, publisher, size **14,787,172**, and SHA-256
  `dfe642f7e439a384c7eeb9843206f0f486e020de877c4d744077845e9b40dd55`.
  Other component metadata and artifacts are unchanged.
- **Authenticated delivery and preparation:** the real installed 447 controller
  reports 448 available. Its supported one-use-ticket download matches the
  published bytes; the ticket was kept in memory only. The actual unchanged
  signed 447 client successfully verified and staged that download through
  `--pcr-prepare-update`. No synthetic old-build fixture substitutes for this
  real channel check.
- **Not activated locally:** a new accepted-message handoff first caused a
  read-only preflight to stop. It cleared through the normal app workflow.
  Later, a real nested dialog was observed in the current main window at a fresh
  pre-exit check. After it closed, a new durable message handoff appeared at the
  last preflight, so activation was deferred rather than continuing to chase a
  moving user-operation boundary. Native helpers were canceled before any quit
  request. The actual installed/loaded client remains **447/PID 57024**, and
  pcrd remains **0.1.98/PID 48685**. No installation-success claim is made.
  Draft, message, receipt, queue, and task data were not edited or rolled back.
- The audit observer was corrected to exclude only the current, byte-verified
  native installer helper path, which legitimately shares the app bundle ID.
  It still enumerates every real client window; it does not hide other copies,
  scan stale QA windows as user windows, or ignore the actual blocking dialog.
- No adoption, retry, task replay, job stop, daemon restart, or concurrency-policy
  command was issued. The final preflight had nine persistent owners, including
  newer user-started work; these were inspected for update safety, not enrolled
  as new maintenance/recovery targets.

Only four existing frozen macOS inputs differ from the audited 447 source:
`Info.plist`, `ComposerExecutionConfiguration.swift`,
`ExecutionConfigurationTests.swift`, and `scripts/test-client.sh`, plus the two
new presentation source/test files. Publisher, client lifecycle, native update
implementation, and all other common frozen inputs are unchanged.
