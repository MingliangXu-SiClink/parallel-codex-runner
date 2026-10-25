# Original Message History Failure And Management-Service Update

## Confirmed Incident

- The official client retained logical message `1f865e8c-ea04-4a1d-9be6-cba157df1cef`, attempt 1, against source run `pcr-20261015T072800Z-4f438d62`, physical Agent 11. The prompt and original command were not edited or resubmitted.
- The journal recorded `invalid_argument`: `Unsupported Codex history schema; no global database was copied: guardian_review_feedback, thread_read_receipts`.
- Global pcrd was still 0.1.92. The previously published 0.1.97 had only been used for two targeted synthesis workers, not the global send entry point. This left the ordinary continuation path incompatible with native state schema 61.
- The controller mapped an internal history adapter `ValueError` to invalid user input. Client 436 then persisted `failed`, `receiptUncertain=false`, and `retryRequiresNewCommandID=true`.
- This was not an authoritative non-delivery receipt. Read-only `commands.get` returned `in_doubt`; the source had already finalized Agent 11 and synced its result, but no matching committed submission or child continuation existed. A new command ID would be unsafe.

## Explicitly Authorized Activation

The user approved only switching the management service to the already verified immutable 0.1.97, preserving all running tasks. No client installation or restart, signing, task adoption, retry, cancellation, or original-message replay was included.

- Activated at `2026-10-16T15:13:16Z`: global daemon 0.1.92/PID 73487 -> 0.1.97/PID 32415.
- Wheel SHA-256: `4b1eebcf49e816f80e08e35755fa21a4dc81510ae09e6afa2fef0d6122e4260d`.
- Reverified the installed package against every wheel payload file and its frozen 0.1.97 input hashes. Historical source 1197/installed 1196 tests and 289 subtests, their log/XML hashes, and both native synthesis handoff reports matched the same frozen release. Mutable 0.1.98 development sources were not installed.
- Rechecked protocol/client minimum build compatibility, healthy paired relay, GPT-6.1 Sol, stable zero prompt admissions/delivery work, empty follow-up items, and detached owner process groups. Running detached tasks were intentionally allowed to continue.
- All seven existing worker PIDs and all 57 baseline client/Node/Codex/worker process signatures were preserved. Both active runs remained active. No rollback was required.
- The installed macOS 0.1.436 binary/inode and all retained client outbox/handoff data were unchanged. The client was already not running before activation; this procedure did not start or restart it.
- The original journal bytes and receipt-store identity `cc44b3a9d69c4e55a8343d27cebb258d` were preserved.
- Read-only post-check: global API reports 0.1.97; the installed 0.1.97 adapter successfully budgets the original selected native history (572860 bytes). No real native turn was submitted for this check.
- The old message still reports `in_doubt`. It has **not** been recovered, resubmitted, or marked delivered. Management-service activation is not old-message recovery.

Private deployment evidence and rollback definition: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/SendSchema-20261016/activation97.json`, `activation-before.json`, `service-before97.plist`, and `original-command.json`. The dedicated gate tests cover retained running detached tasks, busy delivery/admission, unknown health and queue state, and retained original queue items.

## Unreleased Development Work

The checkout now contains an unactivated 0.1.98/backend and 0.1.439/client candidate:

- Internal schema compatibility errors have a dedicated type and retain original prompts through the existing durable pending mechanism, rather than declaring invalid user input. Unknown schemas remain fail-closed; no whole-library copy fallback was added.
- Legacy recovery is narrowly limited to the observed error class and exact original identity. Lookup remains read-only and does not enqueue abandoned historical failures. Existing committed submission identity takes precedence over old failure evidence.
- A guarded recovery callback retains the original error in `history_recovery`, keeps the requirement across busy retries/crashes, and rechecks source finalization, Agent, promotion and absence of any child while holding a cross-manager continuation lock. Current normal continuation entry points share that lock. This is not a guarantee against older standalone writers that do not implement it.
- The client treats the old false-unsent classification as requiring receipt reconciliation. Completed receipts do not send again, proven-retryable receipts reuse the same command/body, and unresolved receipts remain retained without edit/new-ID recovery. Internal recovery is not rendered as a composer warning or as accepted delivery.

Validation so far: backend source full suite **1204 tests + 289 subtests passed**. Initial strict cooperative-pool delivery regression: **80 tests passed**. A full macOS run executed 618 tests in 62 suites but failed one existing scroll-animation midpoint assertion in `TranscriptScrollPublicationTests.streamingFollowsSmoothlyUntilTheReaderScrollsAway`; this is retained as a failing full run, not relabeled success. After the final presentation-state change, the three focused history-delivery tests passed (including both already-accepted and proven-retryable cases), while the same unmodified scroll midpoint assertion failed again in isolation. The combined focused invocation therefore also failed; the client candidate is not release-accepted. No 0.1.98 wheel/runtime or 0.1.439 client was published, installed, or activated by this work.

The incident is not closed until original-message recovery is separately authorized, safely deployed and observed, and the client candidate passes release acceptance. The approved 0.1.97 service switch alone must not be described as a complete fix of every delivery or responsiveness problem.

## Actual Original-Message Recovery, 2026-10-16

The user's subsequent request demanded completing recovery rather than reporting that it remained unfinished. This section supersedes the earlier undeployed-backend and unrecovered-message status, but does not claim that the client candidate has been installed.

### Immutable Backend Release

- Published and independently installed pcrd **0.1.98**, wheel SHA-256 `3879b1462e17cba5f4b58565c2d085134fc4a848eec63a8f63da10d2a1204876`, size 1171381 bytes.
- Verified every installed wheel payload file. Compared the immutable wheel against 0.1.97: only `scoped_history.py`, `plugin_runtime.py`, `remote/controller.py`, `remote/journal.py`, and `remote/ipc.py` changed. The release and all test reports share identical frozen backend input hashes.
- Full source suite: **1204 tests and 289 subtests passed**. Installed-runtime suite: **1203 tests and 289 subtests passed**, with only the source-layout vendored Textual test excluded.
- Source and installed native original-command cases both passed: reproduce the exact legacy schema failure after adoption, reopen the daemon, recover the same command without adopting again, lose the accepted reply, reopen and replay, and prove exactly one native continuation and one child, original session/history/identity retained, and native success.
- Source and installed native synthesis handoff cases both passed: three successful native synthesis continuations, eight existing candidate results unchanged, background process unchanged, and old failure evidence archived. All these validation cases used an isolated loopback provider, not real user turns.
- Four dedicated activation tests passed, including running detached tasks, in-flight admissions/delivery, only real open client windows, retained failed outboxes, already-enabled behavior, and rejecting unverified bytes/unexpected versions before activation.
- Updated only the pcrd wheel/component in `release/manifest.json`. macOS/Android/relay artifacts and metadata were preserved. No signing operation was performed.

### Actual Management Activation

- At `2026-10-16T15:44:17Z`, global pcrd **0.1.97/PID 32415 -> 0.1.98/PID 48685**. The previous daemon was confirmed exited before starting its replacement.
- Checked healthy pairing/relay, GPT-6.1 Sol, stable zero admissions and delivery activity, empty follow-up items, and independent process groups for every active detached owner. Existing plugin MCP processes used a separate state directory, not the remote recovery store.
- All seven preexisting worker identities remained live and unchanged: `4963`, `91347`, `80898`, `37941`, `77777`, `5472`, `6502`. Both previously active runs remained active. No worker was stopped, adopted, retried, or resubmitted. No rollback was needed.
- The user had independently installed and started macOS **0.1.438/PID 35474** since the earlier 0.1.97 service switch. This activation preserved its executable bytes, inode, and process, without installing, restarting, signing, foregrounding, or automating the client.
- One other baseline Node process, PID 48448, was absent after activation. Its cause was not established; the procedure issued no Node signals. This record does not claim that every unrelated Node process remained unchanged.
- Receipt-store identity, pairing, original failed journal bytes, and all current-window outboxes/handoffs were unchanged by activation. No retained message was deleted or consumed to satisfy a gate.

### Real Recovery And Native Execution

- Sent the **saved original `turns.continue` request once** through authenticated local IPC, including its original request ID, command ID, body, timestamp and capabilities. No replacement message or new attempt ID was constructed.
- Original logical message `1f865e8c-ea04-4a1d-9be6-cba157df1cef`, attempt 1, now has an authoritative **completed command receipt**. This means admission succeeded, not that the ten-hour research task has finished.
- Exactly one child continuation exists: **`pcr-20261016T154513Z-a22a1548`**, worker PID **48955**, actually loading the independent **0.1.98** package root.
- Original conversation `pcr-20261009T145249Z-33a44bfa`, promoted native session `01a12129-a8aa-7ef3-a345-b5168e31a205`, source run `pcr-20261015T072800Z-4f438d62`, physical source Agent 11, full prompt and all original settings were verified. The source remains finalized/synced with exactly the same promotion record; adoption was not repeated.
- At `2026-10-16T15:47:48Z`, **all eight candidate Agents** were `running`, in phase `working`, with real native-start timestamps and subsequent output/tool execution. They were not merely queued or marked started locally. Three synthesis Agents remain configured for the normal later phase.
- The completed journal retains the exact original request and old schema failure under `history_recovery`. Exactly one stored submission matches the original fingerprint. No existing research task was rerun.
- Final readback confirmed global 0.1.98, healthy paired connected relay, GPT-6.1 Sol, unchanged seven old workers and original client PID, and the new run alongside both original active runs.

### Remaining Client Boundary

The still-running 0.1.438 client retains the original outbox's old `failed` classification. It was not patched or deleted on disk, and the unreleased 0.1.439 client was not installed. The original research execution is now genuinely restored; client-side legacy receipt reconciliation/visual settlement is **not** claimed deployed by this backend recovery. The earlier full-client scroll-test failure remains recorded above. A stale client failure marker must not be interpreted as permission to issue a fresh message identity or repeat the research continuation.

Private reproducible evidence: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/SendRecovery-20261016/`, especially `publication.json`, `activation98.json`, `original-dispatch-before.json`, `original-dispatch-response.json`, `original-receipt-after.json`, and `real-recovery-verification.json`.

## Client Boundary Closed, 2026-10-17 Local Time

The user's subsequent instruction explicitly required finishing the client installation and real UI acceptance. The former "Remaining Client Boundary" above is now historical, not the current status.

macOS **0.1.439/build 439** was actually installed at `/Applications/Parallel Codex.app` and normally restarted in the background, **PID 35474 -> 64075**. Its loaded executable inode **186149678**, SHA-256 and complete bundle contents match the frozen release. No signing/keychain operation, security change, manual production-data edit, pcrd restart or worker replacement was performed.

The real client automatically reconciled the original **completed** receipt and settled its old failed outbox and accepted handoff. The same original command still points to exactly one existing child, `pcr-20261016T154513Z-a22a1548`, whose eight candidates remain running. Actual main-window accessibility inspection and screenshot show the compression workspace as running rather than sending-failed, the original prompt once, existing output, preserved Agent 8 selection, an editable composer, and the user's independently queued message retained exactly once with the same identity/body/destination and no early dispatch. All eight preexisting worker identities and pcrd 0.1.98/PID 48685 were preserved.

The frozen client passed **551 noninteractive tests** and **111 strict focused tests**, plus the no-signing build, hidden-clock motion, release and packaged-resource checks. This is a source-verified local development installation and separately labeled local ZIP publication, **not** a signed remote-update release. The signed channel and its authentication rules remain unchanged; future signed updates can fail closed when the current local host lacks the expected publisher identity.

Full acceptance and preserved historical test-failure details: `remote/qa/2026-10-17-client439-legacy-receipt-settlement.md`. Private installation/UI/process evidence: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/ClientReceipt-20261016/`.
