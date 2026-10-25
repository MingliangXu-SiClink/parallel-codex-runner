# macOS 0.1.406: Smooth Reading and Default Agent Switching

## Status

The remaining mechanisms from the ScrollHitch and AgentSwitch diagnoses are implemented and verified in the 0.1.406 client build. Version 0.1.405 only partially addressed those diagnoses. It did not remove the default completed-Agent selection RPC from the cached reading path or protect ordinary trackpad/momentum scrolling from passive timeline updates.

The 0.1.406 package is built and signed locally. Publication, installation, and restart authorization was requested this turn but has not been received. The formal installed client remains **0.1.405 (405)**. No formal client or scheduler restart was performed. Live read-only checks show pcrd **0.1.66**, healthy, paired, and no active runs at the time of the final acceptance check.

## Changes

- Default cached Agent clicks register continuation intent and display the existing answer without awaiting the selection receipt. Reading identity, latest choice, and confirmed server source are separate.
- Selection writes are single-flight. Only unsent intermediate choices coalesce; uncertain receipts reuse the original command identity. Late receipts do not navigate, hydrate again, or overwrite a newer queue revision. B-C-B remains a real final selection, even when B was previously confirmed.
- New submission waits for the latest valid confirmation, retains its original draft, rechecks navigation identity, and prevents duplicate preparation during the wait. Rejection does not silently submit using the old source. Running-Agent browsing and adopted-result previews remain read-only.
- Ordinary wheel, trackpad, and momentum activity defer passive same-context timeline presentation before native data-source/cell replacement. The coherent displayed snapshot stays mounted; only the latest received snapshot is applied at idle. Explicit navigation, disclosure, find, jump, typography, and resize retain their own immediate behavior.
- Inner work scrolling forwards real activity phases to the conversation coordinator even when the inner view consumes the displacement. It does not duplicate displacement or synthesize inertia. Cancellation, phase-less wheels, teardown, and lost-phase idle paths release deferred presentation.
- Geometry commits deduplicate the same content generation, width, total height, and row-measurement revision. The completed marker is recorded after layout/document geometry/anchor work, not at invalidation time.
- Live duration and token updates publish through a result-details-local object, not the entire conversation model. The full current Run/event count still drives fetching. Lifecycle, source, terminal statistics, and business state changes continue to publish normally.

## Verification

Evidence directory: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/SmoothReading-20261004`.

- `test-client.log`: **347 tests in 44 suites passed**, plus protocol, desktop-state, and icon checks. Tests include default queue-managed warm switching, held/dropped/rejected selection receipts, B-C-B, historical receipts after newer dispatching state, duplicate submit preparation, cross-context/cancellation guards, real event phases, and momentum coalescing.
- The 200-update metric-only test observes **0 whole-conversation publications** and **200 local metric publications**, while retaining the latest event count. This is the isolated metric path, not a claim that all necessary polling publications vanish.
- The 200-snapshot tracking/momentum test keeps the native item array unchanged during scrolling, then commits the latest revision once after momentum ends, without requiring another refresh. Geometry and reading-anchor assertions pass.
- Existing large work-log/reflow tests report zero geometry mismatch and zero measured reading drift. The 1,000-operation work-layout benchmark uses 30 warm samples and reports P95 **19.318 ms** on this machine. This benchmark is not a whole-application frame-time guarantee.
- `resource-smoke.log`: packaged math and syntax rendering pass. Strict code-signature verification passes for both the release app and isolated QA app. Signature-stripped executable hashes match, proving native acceptance exercised the final release executable code.
- Native completed-Run scenario: hold Agent 2's receipt, show cached Agent 2, then show cached Agent 3 while Agent 2 is still in flight. The details accurately show source confirmation pending. Release the receipt chain: Agent 3 remains visible and becomes the confirmed continuation source. See `qa-held-agent2`, `qa-held-selection`, and `qa-confirmed-selection` PNG/AX artifacts.
- Native streaming scenario: after scrolling up, repeated live refreshes preserve the same visible progress paragraph and scrollbar value **0.7804459691252144**. Explicit work disclosure remains expanded, inner-boundary scrolling continues the outer page, and jump-to-latest exposes the newest streamed snapshot at scrollbar value 1. See `qa-stream-reading`, `qa-stream-stable`, `qa-work-scroll`, and `qa-stream-latest` artifacts.
- Both native scenarios use an independent bundle/preference domain and private homes (`/private/tmp/pcr-smooth406`, `/private/tmp/pcr-smooth406-stream`), outboxes, sockets, registries, and simulated service. No real Agent lifecycle request was made. The optional completion watch is rejected by the private fixture. All QA clients/services have been stopped normally.
- `native-acceptance.json` records hashes, assertions, artifacts, and actual formal installation/service state. `partner-review.txt` and `partner-review.png` record independent design/implementation review, including the historical-receipt and equal-total-height edge cases tightened before the final build.

## Boundaries

This fixes the identified redundant work and cached-selection waiting paths; it does not claim that every frame on every machine has no hitch. Uncached content still requires fetching. Already-dispatching daemon-owned messages cannot be rolled back by a later client click, and are not presented as having been rolled back. Scheduler updates, Android changes, and research-task operations are outside this client-only change.

## Subsequent Deployment on 2026-10-04

The user subsequently authorized publication, installation and restart as part of the submission-latency repair. The exact tested 0.1.406 executable is now published and installed through the built-in updater; the formal client restarted successfully at 09:59:16 CST. Settings report **0.1.406 (406)**. The installation record is `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/SendLatency-20261004/client-installation.json`; its executable SHA-256 matches the release and the signature-stripped code matches this record's earlier native acceptance. Actual pcrd is now **0.1.68**, healthy and paired, and was not restarted during this client update. Both current runs continue. The initial Status section above describes the earlier acceptance-time state, not current deployment. See `2026-10-04-macos-send-latency.md` for the new backend's measured improvement and deferred activation boundary.
