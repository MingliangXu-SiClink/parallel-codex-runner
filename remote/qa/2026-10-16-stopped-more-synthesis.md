# Stopped more: synthesis navigation and continuation

## Scope and Observations

- Affected run: `pcr-20261015T072800Z-4f438d62`.
- The original eight candidates succeeded. The three completed synthesis results retain physical Agent IDs 9, 10, and 11.
- The two later `more` candidates have physical IDs 12 and 13, displayed as candidate Agent 9 and Agent 10. No identity collision or renumbering of stored results occurred.
- At 2026-10-16 05:21:25 UTC, `batch_created` admitted candidates 12/13 and requested a synthesis refresh.
- At 07:15:50 UTC, the user stopped this additional batch. Candidates 12/13 were cancelled. No additional successful candidate or synthesis refresh followed.
- All three old synthesis results incorrectly retained `stale=true`, `can_continue=false`. Their original workspaces, native threads, and answers remain available.

## Root Causes

1. `PluginRunManager._start_additional_batch` invalidates old synthesis results when additional work is admitted, before any input changes. Cancellation did not retract that premature invalidation.
2. macOS `rememberedSelectedAgent` gave the daemon queue source precedence over a completed-turn reading choice. Stale results do not issue `followups.select_agent`, so polling restored synthesis 3 after a click on synthesis 1/2. A delayed queue acknowledgement could similarly overwrite the reading choice.

These are independent defects. Removing the stale flag without evidence, hiding the warning, remapping display indices, or recreating native sessions would not be a root fix.

## Source Fix

- Reuse `RunProgressProjection` to reconstruct exact successful candidate result fingerprints, input generations, each synthesis input binding, and its retained successful result.
- A settled, non-finalized run can retract an invalidation only when the original inputs and synthesis result still match the event evidence, no execution owner is active, and no intervening successful-input generation changed.
- Append `synthesis_revalidated` and persist the correction. Missing history, a new successful candidate, a changed result under the same ID, success followed by removal, or a replacement synthesis attempt cannot certify the old result.
- Keep the explicit reading choice in the existing persisted Agent selection table. Polling and queue acknowledgements no longer replace it. Queued messages retain their original source; a new direct follow-up captures the explicitly selected physical Agent ID. Finalized-source protections remain unchanged.
- No protocol/state-envelope version change, prompt replay, new dependency, or generic result-version subsystem.

Changed sources:

- `src/parallel_codex_runner_core/plugin/progress.py`
- `src/parallel_codex_runner_core/plugin_runtime.py`
- `apps/macos/Sources/PCRRemoteMac/AppModel+RunControls.swift`
- `apps/macos/Sources/PCRRemoteMac/AgentContinuationSelection.swift`
- `apps/macos/Sources/PCRRemoteMac/AppModel+FollowUps.swift`
- `apps/macos/Sources/PCRRemoteMac/AppModel+Presentation.swift`

## Existing Record Repair

Evidence and maintenance tools are under:
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/StoppedMore-20261016/`.

- Rebuilt input/result provenance from all 77,175 original events. The retained eight input results and three synthesis results match their original event fingerprints and input generation.
- Used the actual enabled 0.1.92 runtime, the existing per-run operation lock and cross-process state lock, an exact target-record comparison, worker-idle checks, and byte-level event verification.
- Corrected only `stale_synthesis_agents`, `event_count`, and `updated_at` for this stopped run. Preserved all other runs and all result/configuration fields. The original record is retained privately as `original-run.json`.
- Appended audit event 77,175. The preceding history still hashes to `60a615144016533a63aae0384c57a11fd06815b3420b4dd9bfa9c07db3e00c34`.
- Actual pcrd readback confirms Agent 9/10/11: `success`, `stale=false`, `can_continue=true`, original native thread unchanged, readable answer pages of 1,614 / 1,323 / 1,833 bytes.
- Physical candidates 12/13 remain cancelled. The run remains cancelled, not adopted/finalized. No user prompt was submitted and no stopped Agent was resumed.
- The affected follow-up queue is idle, empty, and retains its existing manually selected source 11; no queue contents or selection were changed by maintenance.
- The first maintenance attempt aborted before writing production data because read-only EventLog access updates mtime. The final gate verifies actual event bytes, inode, size and cursor instead; it does not bypass the evidence check.

## Verification

- Red tests reproduced the stale-result continuation failure and the navigation snap-back before source changes.
- Backend regression file `tests/test_synthesis_validity.py`: 12 passed, including each synthesis's original-session continuation route using isolated, mocked execution/sync boundaries.
- macOS: 44 focused tests across `AgentPreloadTests`, `FollowUpPresentationTests`, and `FollowUpReconciliationTests` passed with `LIBDISPATCH_COOPERATIVE_POOL_STRICT=1` and foreground interaction disabled. Covers repeated switching/polling, late queue acknowledgement, persisted reading selection after reload, and duplicate submission of an explicit original-Agent follow-up.
- Normal-mode `RunAvailabilityTests`: 15 passed. Total passing targeted client checks: 59; these do not constitute the complete client suite.
- Maintenance transaction checks: 4 passed, covering exact-field correction, target/cursor races, and preservation of another task's concurrent update.
- Full backend verification: 1,168 passed and 289 subtests passed. The final source hashes and results are recorded in `source-tests.json`, `source-tests.xml`, and `source-tests.log` in the maintenance directory.
- Broader client regression was not successful: current macOS rejects `.atomic + .completeFileProtection` writes with Cocoa 513 / POSIX EPERM, also reproduced by an independent Foundation-only probe. Do not count the focused tests as a full client pass. No storage guard or protection setting was weakened to make tests pass. Apple describes the protected-file availability restriction in [completeFileProtection](https://developer.apple.com/documentation/foundation/nsdata/writingoptions/completefileprotection).
- A strict-pool run containing an older blocking `RunAvailabilityTests` fixture was terminated at the exact test-only helper PID 65352. The final strict run excludes that fixture; normal-mode availability tests are recorded separately. No production Node/Agent process was stopped.

## Deployment Boundary

- The existing-record correction is effective in the running service. The general recurrence-prevention source changes are not installed in the running applications.
- Actual daemon remains healthy 0.1.92; actual macOS app remains 0.1.436 (build 436). No LaunchAgent, runtime arguments, service PID, client PID/binary/inode, pairing identity, or command receipt-store identity changed.
- The same real run `pcr-20261016T044140Z-9d65cdfe` remains active. All 25 baseline non-browser-helper Node/Codex processes retain their original identities. Four CUA browser-helper processes exited between snapshots; they are not PCR execution owners.
- No app installation, production restart, release publication, or automation change was made for this fix. Existing published 0.1.95/438 artifacts were not modified.
- An unnecessary signing attempt on an isolated diagnostic probe failed with `errSecInternalComponent`. The user corrected this scope; all signing work was stopped. This is not a request for the user to unlock anything, and signing is not a prerequisite for this bug repair.
