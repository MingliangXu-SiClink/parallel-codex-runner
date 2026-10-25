# macOS 0.1.449: Single-Line, Complete Execution Summary

## User Contract

The collapsed execution entry stays on one line outside the message input. Its
order is candidate count, candidate model, synthesis count, synthesis model.
Both models remain present when identical or synthesis is disabled. It never
uses omission marks, replaces a full name with an abbreviation, shrinks fonts
to fit the viewport, or stacks the two roles into separate rows.

The full role-label variant is preferred when it fits. The compact variant uses
the established role icons while retaining both complete model names. When even
that variant exceeds the available width, a native horizontal scroll view keeps
the complete entry reachable on the same line. Wide-window hit-testing still
follows the content width, without a full-row hover background.

The expanded editor retains build 448's 368-point preferred width, 16-point
stage gap, disabled gray locks, direct count editing, and upward-only placement
inside the application window. Expanded long labels wrap rather than truncate.
The bounded native surface scrolls its complete option list; there is no nested
option-list scroller and no count-confirmation checkmark. Count range labels
use a hyphen, not an omission mark.

## Verification

- 51 relevant native tests in six suites pass: execution configuration, native
  presentation, model options, Agent questions, lifecycle, and signed updates.
- Protocol and desktop-state contract executables, branding, signing-store, and
  explicitly disabled-signing gates pass against the same frozen source hashes.
- Offscreen native renders cover Chinese and English, light and dark appearance,
  all display zoom levels, narrow and wide windows, and editable/locked stages.
  Every collapsed summary remains exactly one 28-point scaled row. A native
  scroll interaction reaches the end of long names without mutating settings or
  sending a message. Expanded long-name and option-row renders remain complete.
- The existing tests also cover immediate valid count edits, invalid input,
  delayed model callbacks, Enter deduplication, stale workspace/run contexts,
  native draft/IME preservation on resize, upward growth, window bounds, native
  hit-testing, and bounded scrolling. No QA window was shown or activated.
- The full unrestricted suite is not claimed as passing. The prior bounded
  all-suite attempts and their existing loopback fixture stalls are documented
  in `2026-10-18-client448-execution-popover.md`; 449 uses the scoped affected
  suites and contract gates rather than repeating those unrelated stalls.

The early wrapping prototype was rejected immediately after the user's explicit
single-line correction. Its signed archive and metadata remain private and
unpublished. The catalog was checked to still be at 448 before that rejected
archive was moved out of the release path. Only the corrected single-line
artifact below was published as 449. The immutable published 448 was not changed.

## Publication and Delivery

- Published at `2026-10-18T02:53:05Z` to the real authenticated catalog as
  `macos-release-8db6278dbe3644ba88e21c917bbb5a6d`, version 0.1.449/build 449.
- Archive: `Parallel-Codex-macos-0.1.449.zip`, 14,784,689 bytes; SHA-256
  `a6111d0f665ad77f025b2c83dbae3021af2b9dc8cad531041f4b668939697f7e`.
- Signed executable SHA-256:
  `0c3052bfceb2acccd513127be84ef8b62c9ab536707c1c6a9b643386db7ab2bd`.
- Same existing offline encrypted `Parallel Codex Release Signing` publisher.
  Final archive unpack, version, exact bytes, rendering resources, and strict
  deep native signature pass. No Keychain, trust, security, or updater change.
- Independent catalog readback agrees on publisher, version, size, and hash.
  The actual installed 447 controller reports 449 available, and its supported
  authenticated one-use-ticket download matches the published archive. The
  ticket remained in memory only. Other component manifests/artifacts are
  unchanged.
- The actual unchanged signed 447 program accepted and staged the download via
  `--pcr-prepare-update`; every staged file matches the published signed bundle.
  This is not a synthetic lower-version fixture or an unsigned local package.

## Activation Boundary

This turn did not request application exit or installation. At the final native
observation the installed and loaded client is still 0.1.447/PID 57024, with its
original binary hash and loaded inode. The single real main window was inspected;
no stale QA windows substituted for it. pcrd remains 0.1.98/PID 48685 with the
same LaunchAgent and receipt-store identity before and after preparation.

The actual authenticated update request/download succeeded; local health and
the existing connected Relay state are recorded separately. The retained older
502 timestamp is not represented as a new failure. This UI-release check is not
a replacement for per-Agent native/tool/remote-computation maintenance evidence.

The 449 update is verified and prepared, not locally activated. No prompt,
adoption, synchronization, retry, task replay, job stop, daemon restart, queue
policy change, research cleanup, or draft/message rollback was issued.

Private hashes, native renders, test logs, publication/readback, authenticated
download, and preparation evidence are under
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/ExecutionSingleLine-20261018/`.
Only four frozen macOS inputs differ from 448: `Info.plist`,
`ComposerExecutionConfiguration.swift`, `ExecutionConfigurationTests.swift`, and
`scripts/test-client.sh`. Lifecycle, native updater, and task execution sources
are unchanged.
