# macOS 0.1.407: completed Agent reading

Status: verified complete. macOS 0.1.407/build 407 was published, installed through the in-app updater, and restarted. The scheduler was not restarted.

## Verified Starting Point

- The installed client was 0.1.406, build 406. Its warm-cache selection and nonblocking continuation confirmation fixes were installed, not merely staged.
- A cold completed Agent could still wait for its full work request, including an already-running preload's load token. File changes were separate requests; there was no explicit requirement to finish every diff before showing the answer.
- The scheduler was healthy and paired at 0.1.68. Two user runs were active. No task lifecycle action, private run configuration, or research directory was changed for this repair.

## Changes

- Select a cold branch immediately. For a terminal Agent, request its answer artifact independently with `include_events=false` and `include_diff=false` before falling back to the work stream.
- Present the first readable answer without waiting for work pagination, file changes, or continuation-source confirmation. Fetch the remaining answer pages and work in the background.
- Preserve the completed execution glyph during a read. Loading belongs to the content area, not the execution status.
- Merge overlapping reads into the latest branch cache. A late work response cannot erase or duplicate a newer answer, and answer-only reads do not complete the work cursor.
- Keep a stable answer presentation identity. A matching completed event can supply the full answer while artifact pagination is incomplete; unrelated final events and progress messages are not suppressed.
- Drop responses from obsolete selections or workspace generations. A cold A-to-B-to-A navigation starts a new A read; a repeated click on the same pending selection joins that read.

Main code: `AppModel+RunControls.swift`, `AppModel+RunState.swift`, `Transcript.swift`, `ConversationResultNavigator.swift`, `AppModel+Presentation.swift`, and `Models.swift` under `apps/macos/Sources/PCRRemoteMac`.

## Acceptance

The final-source suite passed: 357 tests in 44 suites, including the cold A-to-B-to-A and no-preload/held-answer-tail cases. Protocol contracts, desktop state, native layout, scroll chaining, and icon tests passed. No continuation-misuse diagnostic appeared. SDK 26.5 was explicitly selected for the installed compiler; no global toolchain configuration changed.

The signed 0.1.407/build 407 release passed strict signature verification and the packaged math/syntax resource smoke test. Its native-tested code matches the release after signature stripping. Release binary SHA-256: `b0fcec41c3c5152e51e9dc432228b03f8a5be507ed6e6932a32f6ade072bfe64`.

Native acceptance uses a separate bundle identifier, private HOME, and fake Unix socket. It simultaneously holds the work preload, diff request, continuation confirmation, and remaining answer pages. No real Agent execution, queue submission, or source-selection operation is sent to production.

Final native observations:

1. Agent 3's first 256-byte answer page was visible while all four requests were held. The Agent still displayed its completed status; files and continuation source were still loading independently.
2. Releasing only the work request displayed the complete matching final event once, without waiting for the held answer tail or replacing the answer's reading identity.
3. Releasing the remaining requests retained the complete answer once, confirmed Agent 3 as the continuation source, and completed the file-list read without an error. The fixture's file list was empty; this is not a visual-diff-content acceptance test.

All hold-arrival timestamps were later than the final QA client's launch. `native-acceptance.json` records the exact release/code hashes, 357 tests, signature checks, and three screenshot hashes. The private client and fake service were then closed.

Evidence directory: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/AgentReading-20261004`.

The earlier trial in `phase-one` is exploratory evidence, not final-release acceptance. The final binary must match `native-acceptance.json` and the installed client.

## Scope

An uncached answer still needs a network read. This change removes client-side dependencies on unrelated work and diffs; it does not assert that every network response is instantaneous or that the backend's shared locks have been eliminated.

The pcrd 0.1.69 idle-only deployment remains a separate authorized follow-up. This release does not restart or activate the scheduler runtime.

## Deployment

- The trusted relay catalog contains active, non-revoked macOS build 407/version 0.1.407. Published archive size: 13,885,804 bytes; SHA-256: `a01bcdb8bbebc74ce231186e9f6d2c4f3ad328a9e8a70e172d851539dabb749c`.
- The existing client checked for updates and prepared 0.1.407 in its own updater. Before restart, the prepared bundle's strict signature, version, target path, archive digest, and binary digest were verified; the installed client was still 0.1.406 at that point.
- Clicking the authorized in-app restart installed the exact signed release at `/Applications/Parallel Codex.app`. Its binary SHA-256 matches the native-tested release above. The running client PID is 77681. The formal settings UI reports `0.1.407 (407)` and latest-version status.
- After restart, the original live conversation, selected Agent 1, five working Agent statuses, and GPT-6.1 Sol composer setting were present. No production Agent tab or task action was clicked during acceptance.
- Before and after installation, pcrd was healthy and paired at 0.1.68 with active run `pcr-20261004T022618Z-e3fb4228`. The scheduler launch configuration hash remained `517a3d6136b90648689111366acec3e7537e947335f7df6b427ad535e93943e1`. All baseline Node process identities remained unchanged. Naturally exited non-Node command processes are listed honestly in `client-installation.json`; they were not restarted or stopped by deployment.
- Other manifest artifacts, Android, relay, and the staged pcrd 0.1.69 wheel were unchanged. The existing idle-only pcrd follow-up was updated to reflect the installed 0.1.407 client and current active run; its checks, authorization boundaries, and quiet unchanged-state behavior were preserved.

Evidence: `relay-catalog-407.json`, `client-update-prepared.json`, `client-installation.json`, `installed-version.ax.txt`, `installed-version.png`, and `update-ready.png` in the evidence directory.

## Independent Discussion

Continued the actual ChatGPT Web discussion at `https://chatgpt.com/c/6ac13f12-19b8-83e8-98da-518f59751ddc`. Its review identified the risk of suppressing a full final event behind an incomplete artifact. The implementation and regression assertions were adjusted before final verification.

It also identified ambiguous prefix matches and a potential answer-tail/work serialization barrier. Prefix substitutions are reversible and require an unambiguous matching visible answer; work and answer-tail tasks now advance independently. The final-source tests cover these boundaries. `web-review.png` records the pre-publication independent review, not installation proof.
