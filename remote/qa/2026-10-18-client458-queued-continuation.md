# Queued Continuation Recovery: macOS 0.1.458 / pcrd 0.1.102

## Original Message, Not A Replacement Task

The reported workspace is `ws-ac82fa6ce348418e` (memory decoding across people/days). The screenshot's text matches original queue item `817e67cf-6b69-47f2-ad68-1ed472ffee1b`, command `follow-up-817e67cf-6b69-47f2-ad68-1ed472ffee1b-attempt-0`, fingerprint `bbe32f133f9bcd144ddf2a44e41b6cbaf92f3a9c2a968ff3a47920c9f56dbc74`. Its prompt SHA-256 is `dcaed35fe3c45b9c4fb0004d37bb85693bf20e09fc1f26f037d6452f484b81d0`.

Read-only receipt/native evidence establishes this timeline, in Asia/Shanghai:

- The preceding run `pcr-20261018T113734Z-8596fffa` finished its native work at **19:40:16**. The saved queue configuration includes a **60-second** follow-up delay; this policy was not changed.
- The completed command journal records dispatch beginning at **19:42:14**. The attached image file was created at **19:43:49**, before the next run existed. This is the attachment's filesystem timestamp, not an independently recorded UI capture event.
- The original command created `pcr-20261018T114430Z-dd19a3a0` at **19:44:30**, with its reply committed at **19:44:39**. Its native thread is `01a1212b-c09e-7ff0-9cfe-57df65f924f6`, native turn `01a14ed4-1aec-7013-b705-cce8db9b9150`; the retained execution receipt is now `completed`.
- At the initial live inspection, the daemon queue, actual main-window durable queue, deferred messages, outbox and accepted handoffs were empty. Exactly **one** committed business run matches the original command identity. A later user-created eight-candidate continuation was already running and was not replaced.

Thus this original message must **not** be resent to demonstrate recovery. It had entered continuation while the old composer still labelled it as unsent. The precise distribution of the historical multi-minute delay among scheduler wait, result preservation, session promotion and other internal calls was not recorded. We do not retrospectively assert that every second came from the retry defect below, or call the completed original message a presently stuck Agent.

## Reproduced Queue Defects

Two new regressions fail on the previous implementation and pass after this change:

1. A transient finalization/transport response cleared the durable dispatch and changed the queue back to `waiting`. The next eligible poll could start another full configured countdown. A restart observed during source finalization could also clear the dispatching state before replay. The queue now retains the exact command/body/source and its retry backoff, waits without touching an active owner, then continues the same claim without repeating the countdown. A claimed message cannot be edited into a different command while its outcome is uncertain.
2. Reconciliation accepted a child using only its parent run and equal prompt text. Another legitimately submitted command with the same text could incorrectly consume the queue item. Reconciliation now verifies the original client/command identity and the fingerprint of the complete frozen continuation body against committed run metadata. Different identities pause for review rather than stealing the item or resending it. The new matcher also recognizes the production message's actual original receipt in a read-only probe.

The macOS composer now distinguishes a claimed continuation from messages that have not been dispatched. Its existing row shows a small native activity indicator and its existing footer shows the actual continuation state. Only genuinely waiting successors count as unsent. No new window, expanded panel, result adoption, capacity limit or computation policy was introduced.

## Verification And Delivery Boundaries

- Frozen source Python: **1,221 tests and 311 subtests passed**. Independent installed-wheel verification: **1,220 tests and 311 subtests passed**, with only the source-checkout-only vendored-file check deselected. Payload bytes match the immutable wheel.
- The restart/finalization regression preserves the exact original command and parameters, waits for the original owner, and dispatches without another 60-second countdown. Lost-response reconciliation covers both the original accepted command and a different command with identical text. Existing unrelated-workspace, stopped/paused queue, in-flight edit and late-response cases remain in the affected suites.
- Native rendering is checked in isolated, never-ordered test windows at narrow and wide widths; no production queue message is submitted as a test.
- An initial full native run had one timed-out asynchronous history-loading fixture (`invalidHistoryPageCannotBeMarkedCompleteOrReplaceSavedHistory`). Its source was not changed or its assertion relaxed. It passed the subsequent targeted run; final full-suite results are recorded separately below, not inferred from that focused pass.

The embedded browser collaboration tools were unavailable, so no ChatGPT Web exchange is claimed. No alternate account/browser, real competition prompt, research directory change, result adoption, process kill, concurrency adjustment, signing identity migration or trust change was used.

Owner-only evidence is under `~/Library/Caches/ParallelCodexMaintenance/QueueDispatch458-20261018/`, including `original-message-correlation.json`, frozen gate records and isolated native renders. Publication, activation, installation and original execution continuity are separate gates; their actual final results are appended only after verification.

## Tested And Officially Published

- Final frozen macOS source passed **620 native tests in 58 suites** (333.524 seconds), plus **26 focused strict tests in four suites**. The earlier history-fixture timeout did not recur in the targeted or final full run; its original log and failure record remain retained. No unrelated history implementation or test assertion was changed to obtain a pass.
- Protocol/icon, encrypted signing-store, signing-disabled isolation, optimized signed build, native deep/strict signature, full archive/resource validation and frozen-source checks passed.
- Immutable pcrd **0.1.102** wheel: **1,178,714 bytes**, SHA-256 `70e162d05e65587e3e3e415e9512e97e471dfcb3608aaac5626ba10affa96a9b`. Only `remote/follow_up_queue.py` differs from 0.1.101; the independently installed runtime passed the full applicable Python suite and payload-byte verification. This package preparation alone does not claim management activation.
- The signed macOS **0.1.458 / build 458** archive is **14,806,546 bytes**, SHA-256 `078d4bc88a089e91f87f044afce276a8936ff546ad63b5f76e38d383e58c3736`.
- The official Relay catalog contains **`macos-release-8ec6bf11b37a480587c45ab80bc4f48b`**. Server readback matches version, build, archive size/hash and the existing **Parallel Codex Release Signing** publisher. The actual 457 installation received `available=true` through the existing authenticated controller connection and downloaded the exact advertised bytes. No production token was manually refreshed or persisted in the evidence.

## Activation Gate And Superseding Client

The 458 maintenance operation **published the build but did not install it or activate the manager**. Before any LaunchAgent or client replacement by this operation, the management preflight correctly rejected two original user-confirmed answers still marked locally pending. No `backend-activation-attempt.json` was created, no pending answer was deleted/marked accepted by maintenance, and the 101 manager and original workers remained in place.

During preparation of 459, a fresh read found the real installation had independently advanced to 458: the native install journal records `newBuild=458`, `state=launched`, `installed=true`, `rolledBack=false`. There was no 458 client-install attempt from these maintenance scripts. The trigger of that concurrent normal update was not inferred. All later preflights use the actual new client PID/version and current saved state, not the earlier 457 PID or bundle. The old 457 bundle/data is not restored.

Investigation found a separate production startup defect: only additional windows started `AgentAnswerOutbox`; the ordinary main window did not. A 459 client-only repair is therefore required before the strict manager-switch gate can safely pass. The existing 101 answer API already supports that sender. The 102 wheel and its full Python verification are unchanged, not rebuilt under the same version. See `2026-10-18-client459-main-window-answer-dispatch.md` for the actual final installation, answer receipts and management activation results. Publication of 458 must not be mistaken for recovery or installation.
