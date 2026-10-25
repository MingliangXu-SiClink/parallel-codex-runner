# macOS Delivery Recovery and Background Verification

Current status, 2026-10-04 22:58 CST: macOS 0.1.411/build 411 is published, downloaded and signature-verified in the built-in update staging directory; it is not installed. The formal client is 0.1.409/build 409. pcrd 0.1.70 is installed in an independent runtime but is NOT active; the healthy, paired scheduler remains 0.1.69. Activation returned 75 because run `pcr-20261004T140156Z-3d27870f` is active. The actual retained message is not yet recovered. No client or scheduler restart was performed during this round. Earlier version and run observations below are historical.

## Root Cause

- The original failed continuation was rejected before creating a new run while the previous finalization worker was still active. The previous turn's visible status had become `stopping`, so the `finalizing`-only guard lost its retryable classification. The command journal retained a generic permanent failure and the client exposed an uncertain-receipt recovery state.
- A second failure boundary exists after a run is durably created but before its response is acknowledged. Retrying under a new command ID could create a duplicate; retaining only the failed journal entry could leave the accepted message apparently stuck.

## Changes

- The backend recognizes active finalization operations even when the visible run status is `stopping`. Known preflight rejections are typed as not applied and retryable; generic ambiguous failures are not assumed safe to repeat.
- Prompt command identity is committed with the actual new run. Read-only `commands.get` checks the original command and payload identity without starting a task or waiting for a mutation journal lock.
- Positive receipt recovery reads committed state, not an uncommitted in-memory run. A legacy retry qualification is consumed before its next execution, preventing a second ambiguous attempt from retaining a stale retry permission.
- The client retains the original logical command ID, reconciles accepted messages automatically and waits for previous-turn finalization in the background. Genuine uncertainty retains the message with a neutral, non-busy presentation and periodic read-only receipt checks; it is not hidden by inventing success or blindly resending.
- Cross-conversation acceptance preserves the currently viewed conversation and its draft. Fresh workspace entry selects the most recent conversation, while later refreshes preserve explicit navigation.
- Agent tabs retain explicit phase labels. One three-second opacity pulse represents confirmed execution for the whole current turn, rather than multiple loading spinners. The animation runs on a CALayer, does not update transcript layout, and stops for reduced motion, hidden windows, disconnected state, older turns and completed execution.

## Non-Disruptive Validation

- Stopped the dedicated Delivery QA client and its private fixture. Neither was relaunched after the user's objection.
- Guarded the QA launcher and native-control wrapper behind fresh interactive authorization. The packaged UI-smoke entry point also refuses execution without that authorization; verified exit status 78 without constructing a GUI.
- Default client tests now exclude focus-changing suites. A static preflight rejects any newly introduced, unclassified foreground interaction test. An initial unfiltered invocation was terminated when inspection identified those suites; the final run used the guarded headless path.
- Background builds and tests used one build worker and `nice -n 15`.
- Built-in update preparation now has a windowless command path using the same archive, bundle, destination and signature verifier. It prepares the candidate without installing, terminating or reopening the formal app.

## Test Evidence

- Python source: **907 passed, 104 subtests passed**.
- Installed independent 0.1.70 runtime: **906 passed, 1 deselected, 104 subtests passed**. The excluded assertion checks that vendored Textual lives under the source checkout, which is not applicable to an installed wheel. The first installed run's cwd-dependent cleanup test was rerun successfully from the checkout; no user task was used for testing.
- Latest guarded macOS tests: **299 tests in 38 suites passed**, plus the native icon checks. These include receipt recovery, duplicate prevention, navigation/draft preservation and the new compositor animation lifecycle tests.
- The preceding client revision passed all **371 tests in 45 suites** before the final activity-indicator and headless-entry changes. Do not substitute that number for full interactive acceptance of the latest revision.
- Signed release build, strict signature verification and packaged math/syntax resource checks passed. Android, relay and the existing pcrd installer artifacts remain byte-for-byte unchanged.
- Earlier private native-fixture evidence recovered a post-acceptance error with exactly one accepted dispatch and one logical command identity while leaving conversation B and its draft intact. That evidence predates the final header/activity changes. Latest foreground acceptance was intentionally not repeated.

## Actual Release State

- Relay catalog contains `0.1.409` / build `409`, release `macos-release-df1bd789ad214655aac1f2836a610aac`.
- Published archive SHA-256: `033a9436e68eec6721ea2388e726735f24c1602055dcc798961daeb8fbd3babb`.
- The controller downloaded the published archive, verified size/hash, and the app's built-in verifier prepared the same signed binary in `Parallel Codex/Updates`. A first preparation command supplied a nonexistent local archive path and failed without changing the installed app; the corrected path came directly from the download response and preparation succeeded.
- The installed `/Applications/Parallel Codex.app` remains 0.1.408 with binary SHA-256 `50a29101f0bd34b92d25255a6fe2b658024ecdde8ad1af8ddd31a05fbaef3d9f`. No restart or foreground update action was invoked.
- All formal-client baseline PIDs were preserved. One observed Node PID, `85382`, exited during the observation interval; its cause was not established. No Node stop or restart command was issued. This is not recorded as proof that every observed Node process survived.
- The scheduler's actual 0.1.69 health is good and pairing is intact. 0.1.70 activation is deferred while any candidate, synthesis Agent or finalization operation remains active. Receipt optimizations must not be described as active yet.

## Follow-Up and Limits

The deferred activation script checks build/test/wheel evidence, the prepared signed client, all active runs and operations, detached workers and the actual GPT-6.1 Sol model catalog before changing only the scheduler interpreter. It preserves pairing, backs up the old definition, verifies the new service and rolls back on failed service/model verification. It must never restart the macOS client, open QA windows, operate Android, resend a user task or modify its private configuration.

App heartbeat `pcrd-0-1-70` is active at five-minute intervals and stays quiet for an unchanged/non-actionable state. It only activates the scheduler after the idle guards pass, verifies and records the result, then deletes itself. Formal-client restart timing has been requested separately because it can redisplay the PCR window; no foreground action was taken to solicit or enforce that restart.

Missing journal records are not automatically evidence of non-acceptance. Replay is constrained to the original replay window and the same receipt-store identity; ambiguous legacy records remain retained without blind replay. Restoring a backup that preserves an old store identity is not independently detected by this scheme.

Evidence directory: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/DeliveryRecovery-20261004`.

## Later Observation and Prepared-Update Supersession

At 2026-10-04 22:17 CST, read-only live verification found the formal client at **0.1.409 / build 409**. Its installation occurred independently of the transcript-stability work; the earlier 408 observations above are historical, not the current installed version. macOS **0.1.410** is now published and prepared by the built-in verifier, superseding the earlier prepared 409 candidate, without restarting the formal client or opening QA. See `2026-10-04-macos-transcript-stability.md` for lifecycle fixes, 310 headless tests and actual release evidence.

## Actual Retained Message: Root Cause Rechecked

At 22:42 CST, the original screenshot submission and old journal were matched by the full original identity and payload fingerprint. The continuation was rejected at 19:22:54; its preceding finalization had completed by 19:24:10. The old permanent failure and actual running 0.1.69 receipt-protocol gap explain why it still does not continue. The user explicitly retained the all-tasks-idle upgrade requirement. A separate stale-capabilities defect for first submissions after a daemon upgrade was reproduced failing, fixed and retested. Additional upgrade-shaped regression tests passed (312 headless client tests, 15 installed-runtime targeted tests and eight read-only acceptance-verifier tests). See `2026-10-04-macos-submission-root-cause.md`; actual message recovery, not merely service activation, is now the closure condition.

The actual scheduler remains healthy, paired **0.1.69**. A guarded activation attempt for 0.1.70 returned 75 because newer run `pcr-20261004T140156Z-3d27870f` is active, with no active operation. No scheduler or client restart occurred. The existing heartbeat was updated with current client/update/run observations; it preserves all idle and model-catalog preconditions. Activation now verifies the sealed 409 binary hash rather than comparing later client-only source timestamps to that historical artifact; immutable build/test/signature evidence and backend source/wheel equality remain required.

At 22:58 CST, 0.1.411 was published, downloaded and prepared by the windowless built-in verifier, superseding prepared 410 while preserving the actual 409 client and baseline Node processes. The new client cache repair and unchanged staged 0.1.70 backend are covered by their separate evidence. No foreground acceptance or installed-411 claim is made. The guarded activation and original-message verifier both returned 75; the original continuation has not been accepted or resumed by the running 0.1.69 service.

The existing `pcrd-0-1-70` follow-up is currently PAUSED. Its updated prompt preserves all idle/model guards and requires original-message execution before closure; the paused status was preserved and resumption of silent checks was requested. The earlier active-schedule description above is historical and is not a current monitoring claim.

## Superseded And Recovered On 2026-10-05

The 0.1.70 staged runtime was superseded by 0.1.71, which preserves these receipt
and recovery fixes and adds bounded Agent preparation. Following the user's new
installation authorization and repeated all-tasks-idle checks, actual pcrd
0.1.71 was enabled and independently verified healthy and paired; macOS 0.1.413
was installed through the built-in updater without foreground activation.
The existing client automatically recovered the original command, unchanged
prompt and Agent 7/source Run into `pcr-20261004T162708Z-9104f9b6`. The read-only
original-message verifier observed durable outbox handoff and real target-Agent
execution at 00:28:16 CST. The verifier did not resend or alter the command.
The obsolete paused `pcrd-0-1-70` automation was then deleted. Earlier pending
and paused statements above are historical observations, not the current state.
See `2026-10-05-agent-preparation-pipeline.md` and the original-message verification
JSON for actual versions, health, identity/source checks and preserved deployment
process baselines.
