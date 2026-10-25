# macOS 439: Actual Installation And Legacy Receipt Settlement

## Scope And Cause

The user required completion of the client delivery, not another backend-only fix. Global pcrd 0.1.98 had already recovered the original continuation, but the running 0.1.438 client still classified the retained submission as definitively failed and did not reconcile its completed receipt.

The fix narrowly recognizes the historical `invalid_argument` / `Unsupported Codex history schema; no global database was copied: ` error. `DesktopState.swift` makes these old failed submissions eligible for automatic receipt reconciliation even when the old `receiptUncertain` field is false. `AppModel.swift` uses the original command and destination. A completed receipt attaches the existing continuation through the durable accepted-handoff path; it does not submit the user prompt again. Retryable and unresolved receipts retain their distinct existing identity protections. Presentation derives from this actual recovery state rather than exposing the stale failure.

No production outbox, handoff, queue, history or receipt was manually edited or deleted. No research workspace was changed by this client update.

## Frozen Validation

All successful client test/build reports have the same frozen input hashes as the installed bundle. Private evidence is at `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/ClientReceipt-20261016/`.

- Full noninteractive client matrix: **551 tests in 55 suites passed**. Foreground interaction suites were deliberately excluded by the existing runner because interactive QA was not authorized.
- Strict cooperative-pool regression: **111 tests in 5 suites passed**, covering delivery, accepted destinations, nonblocking queues, scroll ownership and the hidden streaming publication case. Cases include legacy completed/retryable/unresolved receipts, wrong identities, slow or late acceptance, restart and persistence failures.
- The explicit no-signing build regression passed. It uses the real build script and forbidden fake signing/keychain tools to prove `PCR_MAC_DISABLE_LOCAL_SIGNING=1` exits before any signing setup, `security` or `codesign` invocation. The default signed-build path was not weakened.
- A private hidden test compiled the actual `NativeScrollMotion.swift` with an injected clock and passed midpoint, retarget, cancellation, completion and animation-disabled checks.
- The old wall-clock midpoint assertion was invalid for streaming restoration: the production streaming path intentionally commits geometry atomically; explicit jumps own animated motion. The hidden streaming test now checks the atomic result and that reader scrolling cancels follow behavior. No production animation behavior was changed to satisfy the test, and real midpoint coverage remains in the injected-clock tests.
- Release build and isolated-home application/resource smoke tests passed, including packaged math and syntax assets. No real user prompt was submitted for testing.
- The first default-SDK attempt failed because SDK 27.0 lacked the SwiftUI macro plugin. Its original failing log is retained. The successful cohort explicitly used the existing installed macOS 26.5 SDK and SwiftPM native build system; no system/toolchain setting was changed. The earlier 618-test failing run remains a failing historical run, not relabeled success.

## Artifact And Distribution

- Version/build: **0.1.439 / 439**.
- ZIP: `release/clients/macos/local/Parallel-Codex-macos-0.1.439-local.zip`.
- ZIP SHA-256: `f9b7c391ff2bc43b88ebb6d73633c738cec7026aa3c4395aa52ff02d56bc6994`; size **14808059 bytes**.
- Main executable SHA-256: `2d3672f954a11673611c79331338290f269119d962d7bc5ba0187ca9835ba49c`.
- The package was built fresh, not copied from an old signed bundle. No signing or keychain command was executed, and no quarantine, Gatekeeper, permissions or security setting was changed.
- This is an explicitly labeled source-verified **local development distribution**, not a signed remote-updater release. The existing remote updater's publisher-authentication rules and `release/manifest.json` were left unchanged. Manifest SHA-256 after local publication: `552f2821aee667ebf956937ea780e807d2abe6b417845728f5cdc1f74fe66dee`.
- The remote updater derives publisher identity from its current host. A later signed remote update can therefore fail closed from this local build. No unsigned bypass or ad-hoc signing fallback was added. The old program bundle is retained for independent recovery; reverting the program must never restore stale user data or a pre-acceptance outbox.

## Actual Client Replacement

Completed at `2026-10-16T16:23:04Z`; independent readback at `2026-10-16T16:25:23Z` (2026-10-17 local time).

- Installed application: `/Applications/Parallel Codex.app`.
- Old client **0.1.438/PID 35474** exited through normal application termination. No force signal was used.
- New client **0.1.439/PID 64075** started through normal `NSWorkspace.openApplication` with activation disabled. Launch did not change the foreground application's PID.
- `lsof` verified that PID 64075 maps the installed executable at inode **186149678**; the binary and every packaged resource match the frozen bundle manifest. This is not merely an updated on-disk Info.plist while an old executable remains loaded.
- Existing updater installer lock and same-volume atomic whole-bundle exchange were used. Exchange/reverse-exchange was independently tested in private temporary directories before production use.
- Old 438 quit-sensitive source files were first matched against its own frozen release evidence. The actual selected candidate was running and could not be adopted, with no active management operation, admission/delivery mutation, accepted handoff or pending answer. The real current window, not leftover QA windows, was checked repeatedly.
- Program backup: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/ClientReceipt-20261016/previous-0.1.438.app`. No rollback was needed. No old desktop state was restored.

## Original Message And Actual UI Acceptance

- Original logical message remains `1f865e8c-ea04-4a1d-9be6-cba157df1cef`, original attempt 1, original command `1f865e8c-ea04-4a1d-9be6-cba157df1cef-attempt-1`.
- Read-only `commands.get` returns **completed**, pointing to the same unique existing continuation **`pcr-20261016T154513Z-a22a1548`**, worker PID **48955**. No second matching continuation or replacement command was created by the client update.
- The actual 439 client automatically settled the original failed outbox and accepted handoff using that existing result. The procedure did not click retry, replay a prompt, adopt a result or change a journal.
- Read-only accessibility inspection and the actual window screenshot confirmed the selected compression workspace changed from `sending failed` to `running 0/8`; there is no old failure banner or unsent bubble. The original user prompt appears once in the transcript, existing responses are present, Agent 8 remains selected and the composer is editable with its normal in-progress stop action.
- During preparation the user independently queued another message. Queue item **`E892805C-E008-4753-A1F0-72D7A2D3A31D`**, its full body, settings, conversation and destination (the existing child, Agent 8), order and unpaused state were preserved exactly. It remains durably saved locally, waiting for the currently running task under the normal queue rules. It was not cleared or prematurely dispatched to manufacture acceptance.
- No keyboard input, QA window, foreground activation or test prompt was used to validate the actual production client. Visible native UI evidence is `ui-before.json`, `ui-after.json` and `client439-actual.png` in the private evidence directory.

## Running Work Preserved

- Global pcrd remains **0.1.98/PID 48685**, with local `system.health.status=ok`, pairing retained and GPT-6.1 Sol available. This turn did not restart or replace it. The remote relay link separately reported `connecting` with HTTP 502 at the 16:25 readback and again at 16:28 UTC; local health is not evidence of an uninterrupted remote relay connection. This client update did not change relay settings or claim to repair that separate connection condition. All four task groups and local IPC remained active/readable.
- All eight preexisting worker identities were unchanged through installation and readback: **4963, 91347, 80898, 37941, 77777, 5472, 48955, 50965**.
- The recovered continuation's eight candidate Agents remained genuinely running. The four active task groups were not stopped, paused, adopted or resubmitted.
- Receipt-store identity remains `cc44b3a9d69c4e55a8343d27cebb258d`; service definition, pairing, retained drafts and queued user message were preserved.
- Worker identities are the preservation evidence. Unrelated provider/Node processes may legitimately appear or exit while user work continues; this record makes no claim that every process on the machine stayed static.

Primary evidence: `installation.json`, `actual-installed-verification.json`, frozen test/build reports and `bundle.json`. This closes the concrete old-failure-marker/client-installation incident; it does not claim that every future transport failure or all unrelated responsiveness issues are eliminated.
