# macOS 0.1.405: durable delivery and quiet cached reading

## Scope and Outcome

The user authorized release, installation, and restart after verification, for the macOS client only. Version 0.1.405 (build 405) was published, downloaded through the formal client's built-in updater, signature-validated, installed, and restarted. The formal version screen shows 0.1.405 and reports that it is current.

The scheduler was not restarted or replaced. Its verified running version remains 0.1.66, not the staged 0.1.68 artifact. No real Agent, queue, research directory, private run configuration, Android device, or web service was modified by acceptance testing.

Evidence directory:

`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/CalmDelivery-20261004`

## Root Causes and Changes

- Delivery, an ambiguous transport receipt, and opening an already accepted Run had shared failure presentation. A read failure could therefore look like a failed send and offer an inappropriate resend. The client now separates these states and durably records the accepted Run before opening it.
- Sending now waits for an atomic outbox write before transmission. Transient recovery reuses the original command identity and business payload, with single-flight execution and bounded backoff. Automatic recovery no longer exposes Edit/Retry controls or an orange retry banner.
- A late receipt could race navigation or consume the wrong visible handoff. Activation now rechecks conversation, Run, and navigation generation after its last await and before committing visible state. Acceptance in conversation A does not navigate away from B or overwrite B's draft.
- Later unsent drafts are preserved. Accepted-result opening survives normal client quit and relaunch without resending the accepted command. A definite authentication failure during result reading is truthfully shown as sent but unable to open, with Connection Settings rather than resend.
- Retry diagnostics and repeated unchanged refreshes could invalidate transcript presentation. A bounded, context-scoped cache now reuses raw transcript, timeline, and collection items per Agent. Unchanged item generations return before full collection snapshot and layout work, while still handling navigation, find, jump, and reading-anchor actions.
- A fully cached, terminal Run could continue failing needless polling and repeatedly surface reconnect/restore warnings. Only the conservative fully-read terminal condition stops that Run's polling. Active Runs, incomplete transcript pages, global workspace polling, and accepted-result recovery remain active.
- Normal application quit is no longer blocked by a pending delivery. Local draft, outbox, handoff, editor, and reading-state persistence can complete without stopping Agents.

Main implementation entry points are `DesktopState.swift`, `AppModel.swift`, `AppModel+RunLaunch.swift`, `AppModel+RunState.swift`, `AppModel+Presentation.swift`, `TranscriptPresentationCache.swift`, and `ConversationTimelineView.swift` under `apps/macos/Sources/PCRRemoteMac`.

## Automated Verification

- Full client verification passed: 333 Swift tests in 43 suites, plus protocol contract and desktop-state checks. Evidence: `test-client-final.log`.
- Existing backend command-journal regression checks passed: 4 tests. Evidence: `backend-journal-tests.log`. These checks did not deploy or restart the backend.
- Packaged math and syntax rendering passed: `resource-smoke.log`.
- Icon verification passed: 8 PNG slots, ICNS sizes, and 7 native rendering sizes.
- Release production build completed: `build-app.log`. Strict signature checks passed for the build, archive, prepared update, and installed app.
- The native QA executable's code was verified against the release executable after stripping the necessarily different bundle signatures. Evidence: `native-acceptance.json`.

The final suite also corrects two test-fixture issues: the new handoff test uses the expected decoder field names, and the pagination fixture uses a private state store instead of accumulated shared expansion defaults. Unrelated production state was not cleared.

## Native Fault Acceptance

A separately signed QA bundle used a private HOME and a private fixture controller. The fixture exercised real client transport and state handling but never launched a real Agent or touched the production queue.

1. Warm Agent switches kept saved answers visible.
2. With an acceptance receipt withheld, the message remained visible as sending without the retry/edit banner. Nine transport attempts reused one command identity and one business payload, producing one logical fixture task.
3. Normal Cmd+Q while awaiting the receipt succeeded. Relaunch restored the same durable command and preserved a later unsent draft.
4. After navigation to B, acceptance of A's command persisted without changing the selected conversation or B's independent draft.
5. Returning to A showed Sent/Opening, not a false workspace conflict. Normal quit and relaunch after acceptance resumed opening only, without resending the command.
6. A temporary source-metadata failure retained the readable cached answer without a restore-failure or Retry notice.
7. A definite result-read authentication failure showed Sent/Unable to Open and Connection Settings, not Failed to Send. Restoring access opened the original accepted Run and preserved both drafts.
8. The completed fixture had no remaining outbox or accepted-opening record.

Evidence: `final2-fixture-requests.jsonl`, `final2-delivery-observations.json`, `final2-state-*.json`, `final2-qa-*.png`, matching accessibility captures, and `native-acceptance.json`.

The private fixture permitted workspace/settings and source-selection operations. One exact synthetic `prompt_history.append` attempt was observed and rejected as fixture read-only. This is not a claim of zero fixture mutation or of globally exactly-once delivery.

Independent ChatGPT Web consultation reviewed replay age, ambiguous receipts, navigation races, accepted-state truthfulness, and the native acceptance boundaries. Screenshot: `independent-review.png`. Its advice is supporting review, not a replacement for tests or runtime evidence.

## Release and Installed Verification

- Release: 0.1.405, build 405, published and independently read back from the relay catalog.
- Archive: `release/clients/macos/Parallel-Codex-macos-0.1.405.zip`.
- Archive SHA-256: `5c9380a167a9991583d5c8c56198039d0c3d2b7a6b70c72028eece36a0a96743`.
- Archive size: 13,837,203 bytes.
- Installed executable SHA-256: `085ed2deb44c6d1c6c171dd3501190bade1972bb3d878517a2496c4b2eb2563b`, matching the accepted release build.
- Verified formal client PID: 95889; the previous client process exited through the authorized in-app update flow.
- Formal Settings screenshot: `formal-version-405.png`. Prepared update screenshot: `formal-update-ready.png`.
- Formal main conversation was restored, with Agent 3 selected and work-process content visible: `formal-restored-conversation.png`. The Settings window was subsequently closed without sending a message or changing the selected Agent.
- Scheduler remained healthy and paired at 0.1.66. The active Run remained `pcr-20261003T174256Z-ebdf2416` across installation.
- Scheduler LaunchAgent SHA-256 remained `e3f03671471bc3a649bd5781f1967963a832c9c9942783773fa5176fbabe04a2`.
- Twenty-six recorded non-client processes retained their identity across the client update. The exited PID 95591 was the earlier verification helper itself, not an Agent. No service restart was requested or performed.
- Android, pcrd, and relay release artifacts were preserved. Evidence: `staged-release.json`, `relay-catalog-405.json`, `client-update-prepared.json`, and `client-installation.json`.

The private QA client and fixture were stopped, the temporary collaboration browser tab was closed, and the original formal conversation was left visible. The existing dirty repository reorganization was preserved; no commit or unrelated revert was made.

## Remaining Boundaries

- Default Agent switching still waits for the service's source-selection confirmation RPC. That queue-sensitive path was not rewritten in this client-only release. Cache reuse and reduced unchanged refresh work do not establish that all switching latency is resolved.
- No frame-rate benchmark or guarantee that every scroll hitch is eliminated was made.
- The backend receipt journal is finite. Known ambiguous commands are not automatically replayed beyond the client's 29-day safety window; legacy commands with unknown first-attempt time are held rather than assigned a fictitious fresh date.
- A complete terminal cached Run stops only its own unnecessary polling; global recovery and active-task monitoring are not disabled.
- Formal restart preserved the conversation, Agent selection, drafts, and work-process visibility. Its inner work-process scroll fraction was observed at zero after restart rather than its previous bottom position. This release does not claim exact restoration of every nested reading position across application restart.
- A true permanent authentication problem still requires a meaningful connection action. Automatically hiding or indefinitely retrying that problem is not considered a successful fix.
