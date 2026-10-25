# macOS Recoverable Conversation Deletion

## Scope

- macOS client 0.1.402, build 402. No scheduler, Android or relay application-code changes.
- Delete removes a conversation from this Mac's sidebar and search. It is recoverable locally; it does not purge server history or other devices' history.
- Project files, working copies, Codex sessions, unsent drafts and cached reading state are retained. There is no automatic trash expiry or permanent-purge command.
- No real user conversation was deleted during acceptance. The native fixture uses a private home and fictional completed/running conversations.

## Behavior

- Sidebar context menus and the conversation toolbar expose Delete Conversation with a confirmation sheet. The sheet explains the Mac-only scope and retained files.
- Deleting the selected conversation clears only its mounted projection and opens New Conversation. Its draft stays bound to the old conversation instead of appearing in the new composer.
- Deleting another conversation does not change the current selection or interrupt an independent running Agent.
- Active or unresolved tasks, target-specific operations and queued/submitting follow-up messages prevent deletion of their conversation. Other conversations can still be deleted.
- Undo restores the most recently deleted conversation. Application settings contains Recently Deleted with per-item Restore controls; restoration does not take over the current selection.
- A shared store synchronizes windows, writes atomically with private permissions and scopes records by machine and workspace. Persistence errors are reported rather than acknowledged as successful deletion.
- Visibility is filtered before publishing conversation indexes from caches, polling, pagination and search. Newly discovered run/resume aliases are absorbed transitively, preventing deleted history from returning under another identity.
- Saved window navigation and direct selection cannot reopen a hidden conversation. A newly discovered alias of an already-mounted conversation preserves the current reading view but disables sending and offers Restore.

## Verification

- 301 Swift tests in 40 suites passed, including 11 deletion-focused tests. Protocol/state contracts and icon rendering also passed.
- Cases cover durable scoped records, same-title independence, transitive aliases, error handling, selected/other deletion, cross-window propagation, live operations, pending submission, restored window navigation and late asynchronous publications.
- The signed native QA client is isolated under `/private/tmp/pcr-delete402`. Its signature-stripped executable matches the signed release executable; raw hashes differ because the QA bundle has a separate bundle identifier/signature.
- Final-build native checks cover the context menu, confirmation, selected deletion, active-task protection, actual quit/relaunch persistence, search filtering, accessible recovery, unchanged navigation after Restore and preserved answer/draft.
- Native testing identified that a macOS List flattened the Restore button into an inaccessible row. A scrolling lazy stack preserves an independently accessible button; semantic activation was verified.
- Packaged math/syntax resources and strict deep signatures passed. The fixture logged no Agent-lifecycle or other forbidden mutation requests.

Evidence: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/ConversationDeletion-20261003/native-acceptance.json`, final test/build logs and `qa-final-*.png`.

## Release and Deployment

### Actual Publication and Preparation, 2026-10-03 23:27 CST

- macOS 0.1.402 (402) was published to the authenticated private broker. The catalog's size, version and SHA-256 match `release/clients/macos/Parallel-Codex-macos-0.1.402.zip`: `14817db7a1fc037854c79a88977ce754620e10913f8f28c33ac5438a5e3788a4`.
- The release manifest changed only the macOS component/artifact. The pcrd 0.1.68, Android 0.1.268/build 269 and relay 0.1.20 artifacts remain unchanged; no runtime for those components was activated by this task.
- The real installed 0.1.401 client checked for updates, automatically downloaded/verified the archive and visibly offered Restart to Update for 0.1.402. The staged executable matches the native-accepted release binary and passes strict deep signature verification.
- Production installation/restart was not performed because the current restart question has not received an answer. The running client is still 0.1.401, PID 21764, with the same process start identity. Deletion is available in the prepared update, not yet in the running old client.
- Read-only checks before and after preparation show healthy, paired pcrd 0.1.66 with active task `pcr-20261003T121808Z-1abdab98`. Its LaunchAgent definition is unchanged. No Agent, scheduler or webpage service was stopped by this task.
- The private native QA app and fixture server were stopped; the temporary design-consultation browser tab was closed. No real conversation, draft or research file was deleted.

Evidence: `relay-catalog-402.json`, `client-update-prepared.json`, `production-update-402-ready.png` and `before-client-update.json` in the evidence directory above. A later production installation must be verified independently and must not be inferred from these preparation checks.
