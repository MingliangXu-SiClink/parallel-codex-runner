# macOS Long-History Main-Thread Stall

## Production Evidence

- Scope: PCR only. Read-only production diagnostics; no task controls, signing,
  installation, service restart, client restart, history cleanup, or outbox replay.
- Actual loaded app: 0.1.436/build 436, PID 4569. Actual global pcrd: 0.1.92,
  PID 73487. The two recovered synthesis supervisors and the original live
  candidate supervisor remained present.
- First four-second sample at 22:17 Asia/Shanghai: 331 main-thread samples;
  roughly 76 were in `selectedTranscriptBlocks -> conversationHistory(for:) ->
  TranscriptFormatter.blocks -> append -> equivalent -> Collection.split`.
  Other samples included idle run-loop time. This was not evidence of a
  sustained deadlock.
- Second four-second sample during approximately 99% client CPU: 336 of 337
  main-thread samples traversed the same branch-history formatting path.
  Header/title/loading-state evaluation, the toolbar, and the conversation
  body all reached it. This establishes an actual UI-thread computation stall,
  not just slow IPC or an in-app loading label.
- The selected run's original readable cache contained an unresolved, failed
  message, 845 work/answer blocks totaling about 4.2 MB for the selected synthesis,
  and 77 historical conversation entries. No original message was removed or
  marked delivered to improve the UI.

## Root Cause And Changes

1. The UI constructed its cache input by rebuilding the selected branch history
   *before* checking whether the presentation was already cached. A pending or
   failed message made every view evaluation repeat that work, even on a cache
   hit. Input now contains the unmodified history; the same carried-branch
   derivation runs only on an actual formatting miss.
2. Presentation equality previously used the entire transport transcript,
   including global event cursors, seen-event IDs and answer receipts. It now
   compares only fields actually read by the body formatter, including block
   identity/order/content/operations, branch provenance, final-answer text and
   presentation identity, pagination visibility, and errors. Full transcript
   equality remains unchanged for transport reconciliation and late-page guards.
3. Background empty-page/cursor-only merges can reuse the already prepared
   blocks without advancing the body/timeline generation. They still commit
   the new full transcript, including transport cursor and receipt state.
4. Text-equivalence checking no longer allocates two fully split/normalized
   strings for each pair of large work blocks. Exact equality has a fast path;
   other comparisons advance lazily with the original Character/whitespace
   semantics. Repeated user messages remain distinct.

No transport validation, byte cursor guard, message identity, receipt meaning,
workspace isolation, execution owner, or task lifecycle was weakened.

## Reproduction And Verification

Before the fix, a new regression failed against the original code:

| Case | First body projection | 30 unchanged getter reads |
| --- | ---: | ---: |
| Read-only real selected cache | 528 ms | 15,957 ms |
| 1,200 synthetic work blocks, about 5 MB | 829 ms | 25,051 ms |

After the fix, under the same debug-build test harness:

| Case | First body projection | 30 unchanged getter reads |
| --- | ---: | ---: |
| Read-only real selected cache | 73 ms | 0.217 ms |
| 1,200 synthetic work blocks | 87 ms | 0.191 ms |

These are body-projection measurements, not installed-app frame latency.
The real cache was read directly, never copied to a fixture or uploaded.

- 81 focused tests passed, including real-cache replay, Unicode equivalence,
  branch isolation, same-source history changes, message identity, cursor-only
  projection reuse, late pages, answer replacement, slow transport and restart.
- Native hidden-window test retained all 1,200 work blocks and the original
  pending message. Thirty repeated native body updates while unrelated state
  and cursors changed had a maximum update duration of 0.106 ms. No command was
  submitted and no QA window was made visible or key.
- Existing native tests painted 40 complete prepared branches while a metadata
  read was held, and exercised 24 live-branch switches without saved final text.
- Additional final regressions cover equal text with changed message IDs,
  same-identity history replacement, real run errors, and discarded-run display.

Final verification:

- Full non-interactive macOS regression: 547 tests in 55 suites passed in
  343.569 seconds. Foreground/focus-changing suites were excluded by the
  repository's normal non-interactive runner.
- The final additional identity/terminal-state test and late-page assertions
  were also included in a separate 82-test strict cooperative-pool run. All
  passed. Final real-cache repeat: 70 ms cold and 0.212 ms for 30 unchanged
  getters. The native update test measures the coordinator/layout update,
  not a physical display frame or end-to-end key-event latency.
- Protocol contract executable and desktop-state executable both passed.
  Their wrapper's redundant second full-suite invocation was cancelled while
  waiting for SwiftPM; the independent full-suite result above is the actual
  regression evidence, not a claimed successful wrapper exit.
- Release executable compilation passed in 55.29 seconds. Its explicit,
  prohibited-activation resource-smoke mode passed math, syntax and document
  resource checks without constructing an AppModel or opening a window.
  This is not a newly packaged or installed app.
- An initial additional test fixture omitted required protocol keys, and a
  subsequent nested `#require` macro did not compile. The fixtures were fixed
  before the final strict pass. No production code or security setting was
  changed to bypass either failure.
- Read-only post-check at 14:37 UTC confirmed the same service PID/runtime,
  command-receipt store, pairing, client PID/version/executable bytes/inode.
  Health was `ok`. Active runs were then two rather than three; the check does
  not claim that every original native PID must survive natural completion.

## Delivery Boundary

This is a client-source repair. No new client version or backend package was
published or activated by these changes. The running 0.1.436 process still has
the old code; a successful source test is not evidence that the user's current
window has stopped stalling. No signing or signing-related security changes
were performed.

The pcrd process also had elevated CPU during initial observation, with Python
JSON/text-read activity in its sample. This is not yet attributed or claimed
fixed. First-time projection cost, large work-log/cache pressure, and all other
sources of application latency are not covered by the unchanged-getter result.

Private evidence directory:
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/ClientHang-20261016/`.
It contains the two production app samples, the daemon sample, source hashes,
read-only production identity snapshots and validation logs. Samples and private
conversation caches must not be attached to public reports.
