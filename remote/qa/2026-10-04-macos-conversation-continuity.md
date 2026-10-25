# macOS 0.1.404 Conversation Continuity

## Scope

- Client-only fix for revisiting conversations: restore the last known transcript and queued messages before awaiting remote Turn/detail/history requests.
- Do not restart pcrd, change the real follow-up queue, or interrupt Agents and Node services.
- Evidence directory: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/ConversationContinuity-20261004`.

## Causes And Changes

- Navigation restored cached transcript state but cleared the visible queue until the asynchronous Run request completed. Queue restoration now happens synchronously in the same MainActor projection update as the cached conversation.
- Queue identity was overly coupled to the selected Run. Explicit server conversation identity now takes priority; a newer Run in the same conversation can retain its queue before the conversation index catches up. Legacy responses without conversation identity still require a known Run.
- Out-of-order queue responses could overwrite newer state. The client checks the server's monotonic response revision, scoped by machine and workspace. Failed requests preserve the previous projection; a confirmed newer empty response clears acknowledged items.
- Confirmed empty queues previously risked combining unacknowledged items from different Runs. Each local outbox retains its original scope.
- Disk hydration could overwrite newer in-memory state or miss recently cached Turns during debounced persistence. Warm memory and pending writes now take priority; disk records only fill missing entries.
- Navigation restoration is read-only. It cannot enqueue, migrate, dispatch, resume, or stop work. Existing scroll anchors, drafts, and work-process expansion state remain in use.
- A cancelled old queue monitor cannot clear the replacement monitor; post-await navigation ownership is checked.

## Verification

- Full client regression suite: **313 tests in 41 suites passed**, including 10 new continuity tests. Protocol-contract, desktop-contract, and icon checks passed in `test-client-final.log`.
- Production build with MacOSX26.5 SDK succeeded; strict code signature verified. Packaged math and syntax rendering passed in `resource-smoke.log`.
- Independent native QA app, private HOME `/private/tmp/pcr-continuity404`, disposable socket server only. Signature-stripped QA executable code matches the release executable. Attestation: `native-acceptance.json`.
- Warmed conversations A and B, then deliberately blocked Turn/history/detail and queue requests. A's answer, expanded work process, unsent draft, and both server-acknowledged queued items were visible before the delayed Turn request completed.
- Switching to B did not show A's queue or draft. Returning to A while earlier requests remained blocked restored both queued items immediately. A request timeout did not blank cached B content.
- Releasing requests reconciled in place without changing the selected conversation. A newer confirmed empty fixture queue removed only the queued items while retaining the transcript, draft, and expanded work process.
- Recorded screenshots: `qa-a-initial.png`, `qa-b-initial.png`, `qa-a-held.png`, `qa-b-held.png`, `qa-a-again-held.png`, `qa-a-settled.png`, `qa-a-empty.png`.
- Fixture request audit contains no Agent lifecycle or queue mutations. The independent QA app and fixture server were stopped after verification.
- Design consultation completed in the embedded browser using generic behavior and code context, without research data or credentials. Temporary browser tab closed.

## Deployment

- Published macOS **0.1.404 / build 404** to the relay. Catalog version and archive SHA-256 match the staged release: `74f1268a17649abeb3ad422f3e23f7f886218ff8d1001cf5f00ce526e1d039b3`.
- Used the formal app's built-in updater: check update, automatically download and verify, then restart to install. No manual archive installation or updater-state rewrite.
- `/Applications/Parallel Codex.app` reports **0.1.404 (404)**. Strict signature passed; installed executable SHA-256 matches the accepted production binary: `d273a686dff65a957d45da54ca14990000e8290d0087cafbc3d747bdded78a41`.
- The client restarted as PID **53002**. Its live Settings UI shows `0.1.404 (404)` and latest-version status. Screenshot: `formal-version-404.png`.
- Verified the actual selected conversation after restart: saved replies, the expanded work process, and **one server-acknowledged queued message** remained visible. Switched to another existing workspace and returned; the same queued message and expanded process were restored. No send, retry, select-result, delete, or queue-edit control was used. Screenshot: `formal-returned-conversation.png`.
- pcrd remains **0.1.66**, healthy and paired. Its LaunchAgent definition is unchanged. Active Run `pcr-20261003T121808Z-1abdab98` remained active across the client restart. No scheduler activation was performed.
- Process comparison retained 27 recorded non-client processes; one short-lived process had naturally exited. This task did not stop Agents or Node services. Structured deployment evidence: `before-client-update.json`, `client-update-prepared.json`, `client-installation.json`.
- Restored the formal app to the user's original conversation and closed Settings. QA app, socket fixture, build/test/publish sessions, and task-created browser tab are no longer running.
