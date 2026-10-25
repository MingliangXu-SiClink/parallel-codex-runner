# macOS File Review: Automatic Reading

Follow-up: [macOS 0.1.390 acceptance](2026-09-30-automatic-pagination.md) resolves
the manual command/directory pagination and residual three-line reopen drift
documented below, and records publication, installation, and client restart.

## Scope

- macOS 0.1.389 only. No pcrd, Android, Relay, Agent-selection, or execution-policy changes.
- Keep full-context file diffs, syntax highlighting, independent result details,
  the short composer, and existing adjustable columns.
- Existing unrelated working-tree changes were retained.

## Implemented

- File review advances the existing immutable diff cursor until the selected
  file is complete. There is no manual pagination or refresh control.
- The menu reads the complete status/path directory at the start of the same
  snapshot, including when that directory spans pages. It does not require
  downloading every file body.
- AppModel owns a bounded in-memory cache (six results, 24 MiB). Switching files
  and reopening the preview reuse complete files and the same-snapshot cursor.
- Refresh retains both the visible file and its reopenable cached copy until
  replacement content is complete. Partial first reads remain resumable.
- Expired snapshots and changing answer versions restart once automatically;
  incompatible pages never concatenate. Real failures remain local to review.
- Background refresh cannot interrupt an in-progress read every eight seconds.
- Reading position is keyed by result/file and anchored to text, not just a
  pixel offset. Async highlighting preserves selection and the bottom position.
- Copy exports only the file being viewed. Compared answers also read all pages
  automatically rather than exposing transport pagination.
- Python virtual environments (.venv and venv) belong to the dependencies group,
  not the source/document group. The formal client had 7,101 changed files in one
  result, demonstrating why this distinction matters.

## Verification

- Test, build, release-validation, fixture request, and native-client logs:
  `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/Review-20260930-nR7FHr`.
- Isolated native app uses the release executable with a separate bundle ID and
  home directory. Fixture uses the real IPC controller and diff snapshot code;
  it never launches a real Codex Agent.
- Fixture forces 4 KiB pages and 160 ms latency. A 5,200-line Python diff takes
  64 contiguous requests, exceeding the old eight-second refresh period. Its
  final marker is reachable without any pagination click; the full file menu
  appears without downloading later file bodies.
- Native checking caught two additional defects before release: a partial
  refresh displaced the complete cached file, and highlighting changed line
  metrics after a pixel-only scroll restoration. Both received regression tests.
- A streaming animation timing test failed once while GUI QA was active; its
  later full-suite rerun passed. No unrelated animation implementation was changed.
- Final validation: protocol contracts and desktop state tests passed; 224 native
  tests in 30 suites passed (64.467 seconds); icon tests passed. Release build and
  strict archive-signature/resource-rendering validation passed.
- The final native candidate showed complete Python changes with syntax colors
  and added-line backgrounds in light and dark appearance. A later file opened
  from the existing cursor without manual pagination. Result details and the
  selected Agent stayed in place.

## Remaining User Burdens

1. Confirmed in `WorkProcess.swift`: long tool output still adds an
   `operation_more` row labeled "Load more output". Opening/scrolling the detail
   should trigger bounded automatic reading, with localized retry only on error.
2. Confirmed in `ConnectionViews.swift`: browsing a folder with more entries
   still requires the "Load More" button. Near-end prefetch should append entries
   without moving selection, losing the current folder, or opening the wrong path.
3. Recovery-path audit only, not reproduced this round: Run launch and queued
   launch still contain messages instructing the user to refresh/reconnect after
   successful task start but failed conversation hydration. Verify reconnection
   against the accepted run ID before changing this behavior; never resubmit the
   original task merely to restore its view.

## Known Limitations

- First access to a late file still reads its prefix in the serial diff protocol.
  A future per-file interval/index endpoint should fix that actual transfer cost;
  this client release does not pretend to provide random-access downloads.
- Cache is per app/window session, not durable storage. Eviction and app restart
  may require a new automatic read.
- Residual native issue: after closing/reopening a long highlighted file at its
  bottom, the saved view moved approximately three lines upward. It no longer
  jumps hundreds of lines or loses the readable cached file, but exact bottom
  restoration still needs work. Do not report exact scroll restoration as passed.
- Native acceptance covers local IPC. Cross-snapshot, permission, disconnect,
  identity, cancellation, and cache cases are regression-tested, not a claim that
  every remote Relay network condition was manually exercised.

## Independent Review

Continued the existing ChatGPT Web design review:
https://chatgpt.com/c/6abbb5f0-d954-83e9-8da6-4c152c7acddf

Checked directory-boundary parsing, same-page tails, non-hunk changes, bounded
cache behavior, and late callbacks against the actual formatter and client tests.

## Delivery

- Published macOS 0.1.389 / build 389 in the private Relay catalog. Re-read the
  catalog and confirmed the entry is active.
- Release ID: `macos-release-3120fd28bdc545a1aa4ecb3549c1e58d`.
- Archive: `release/clients/macos/Parallel-Codex-macos-0.1.389.zip`.
- SHA-256: `08b353fc88648fabd8868220802dd40efdf8505cbe882604a719e7f60b2cc753`.
- Archive size: 13,288,004 bytes. Signed with Parallel Codex Local Signing.
- Other release component artifacts and their hashes were preserved unchanged.
- Installation/restart approval was requested but not received this turn. The
  formal client remains 0.1.388 (PID 43798); pcrd PID 43170 and the active worker
  were not restarted or stopped. QA app and fixture were shut down.
