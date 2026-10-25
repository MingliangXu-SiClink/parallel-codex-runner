# Question Layout And Receipts: macOS 0.1.460 / pcrd 0.1.102

## Scope And Decision

The user reported a tall, poorly arranged question/answer block and an ambiguous local-save label that did not establish whether the intended native Agent received the answer. The latest explicit decision is **do not interrupt; wait for a safe boundary**. No maintenance action may interrupt the original Agents, duplicate their confirmed answers, rerun their tasks or replace their workspaces to force delivery.

Private, owner-only evidence is under `~/Library/Caches/ParallelCodexMaintenance/AgentQuestions460-20261018/`. The decision is also recorded in the existing private five-competition scope. Neither this release nor its isolated tests changes research directories, compute concurrency or the hourly maintenance automation.

## Confirmed Causes

1. The docked question view used an eagerly sized scroll area with a maximum height of 280 points. Even a short submitted question/answer could reserve that large block. The new view measures its actual content, caps only genuinely long content and collapses answered questions into a small native disclosure row. Unanswered questions stay ahead of submitted history, including when a stale transcript still lacks a service-accepted answer. Expanding a receipt row retains the original question and confirmed answer; no new window, answer selection or navigation change is involved.
2. The answer sender previously stopped observing after PCR accepted a durable answer. That acceptance is not the native execution's receipt. The client now observes the original question receipt after service acceptance, without resubmitting the answer or rewriting unchanged durable commands. Receipt projection is bound to the original machine/workspace/run/physical Agent/request and answer ID. Late empty responses cannot cause an accepted answer to be sent again; another answer ID with the same values is not treated as this answer's acknowledgment.
3. The two real confirmed answers for Agents 2 and 7 were already received by PCR, under their original IDs and exact values, at the initial read-only check on `2026-10-18T14:39:01Z`. Both were still native `queued`, not native-consumed. Their original healthy in-flight executors loaded runtime 100, which has no live asynchronous-answer consumer. Updating the desktop or manager does not replace the code already loaded into those processes. The independently deployed 102 service already supports safe idle continuation; the existing 101+ native executor supports live asynchronous replies. This client-only release does not claim to retrofit an in-flight 100 process.

## Receipt Contract

- A durable local commit is not delivery.
- PCR's accepted/queued receipt is displayed separately from native receipt.
- Async `sent` is a native acknowledgment only after the executor matches the original turn response. A blocking RPC's completed write is described only as sent, not as proven native receipt.
- Uncertain delivery, failed local storage and ended questions remain distinct. A failed receipt read cannot turn an old queued snapshot into a definitive claim about current native receipt; a previously verified native acknowledgment is not weakened by that transport failure. Original choices are preserved, never selected by maintenance.
- The explicit no-interruption decision remains in force. Existing queued answers must wait for the original safe session boundary. Native delivery can be claimed only after an actual subsequent matching receipt.

## Verification Boundary

Isolated loopback tests cover durable commit, original answer identity, lost/late receipts, restart reconciliation, duplicate submission prevention, receipt-state distinctions and actual offscreen native layout at narrow/wide widths and standard/large text. No real competition answer is used as a test. Offscreen windows are never ordered or made key.

Backend Python sources and the immutable 102 wheel are unchanged. Their prior independent source/installed-wheel evidence is reused explicitly, not presented as a new test run. Publication, client installation, original worker continuity and actual native answer consumption are separate gates; measured release results are appended after verification. Embedded-browser collaboration tools were unavailable, so no external consultation is claimed.

## Verified Release And Installation

- Final frozen-source native suite: **628 tests in 58 suites**, passed. Focused strict suite: **49 tests in 5 suites**, passed. Protocol/icon, signing-store isolation, no-signing isolation, optimized signed build, deep/strict signature and full archive validation passed against the same final source hashes.
- Actual offscreen native renders verified compact committed-answer rows at 420/980-point widths and standard/extra-large text. A separate 12-item answered-history fixture verified that all three radio controls for the current unanswered question remain visible before history. Its window was neither visible nor key; rendering did not choose or submit an answer. These are isolated native renders, not screenshots of the user's research window.
- The official signed **0.1.460 / build 460** archive is **14,820,832 bytes**, SHA-256 `3b843945426e4def5892ad7acb33a1135fe30679bfb67600daf2140e13a57628`. Catalog release **`macos-release-75899cd5bf0b48c796804114b0a92985`** was published at **2026-10-18T15:20:08Z**. Server readback matched build, version, size, hash and the existing publisher. The existing **Parallel Codex Release Signing** identity was retained; no new identity, login-keychain change, trust change or weaker updater check was used.
- The real installed 459 client received **available=true** through the existing authenticated controller connection, downloaded the exact official bytes, and its production native verifier accepted the complete staged 460 bundle.
- At **2026-10-18T15:22:02Z**, exactly one normal, non-activating native client update completed: **459 PID 84657 to 460 PID 26483**. Native installation reported `installed=true`, `rolledBack=false`; the program-only 459 fallback was retained. No user-data rollback occurred. Real current windows, drafts, outbox, handoffs, local queues and immutable original answer bodies were checked before and after. No Agent selection or result adoption was performed by maintenance.
- Independent reconciliation verified every installed bundle file, signature, actual loaded inode **188701832** and binary SHA-256 `f4fc617f33189b2ec7a309e38f1cab6ef8167dd16382d7fcd31de965158a0019`. The installed **460** authenticated update check then returned **available=false**. Publication and actual installed/loaded software were verified separately.

## Original Work And Remaining Delivery State

The manager remained **pcrd 0.1.102 PID 88104** throughout this client-only update. All **11 original owners** and **40 original native execution identities** retained their original namespace, native session/turn, workspace, command identity and output inode. **39** original native stdout streams had genuine byte/event increments after the saved cursor. A missing increment on the remaining stream was not treated as a stall. No healthy owner, native process or remote compute job was signaled, and no task or original answer was submitted by a maintenance script.

Both original confirmed answer IDs, exact values and original request/thread/turn/item namespaces were independently rechecked after installation. Their actual native delivery state remains **`queued`, `queued`**, not native accepted. The new client no longer labels this state as successful native receipt. Per the user's explicit decision, those original in-flight 100 executors were **not interrupted**; their answers are preserved for the existing safe original-session boundary. This is not a claim that either native Agent already consumed or acted on its answer.

The actual current app window was checked read-only: one real main window, currently selected **Agent 3**. It did not expose either of the two original answer receipt rows. Maintenance did not switch it to Agents 2/7 or open an old QA window to manufacture live UI acceptance. Layout proof comes from the isolated native renders and exact installed bytes; original delivery state comes from the real original request receipts. These evidence boundaries remain separate.

Local execution health and remote business connectivity were checked separately. The original manager's link briefly timed out at `15:07:23Z` and was already reconnected by `15:07:29Z`, before installation. Later authenticated official-update check/download and the final check succeeded. The old keepalive error was not treated as a current failure, and no Relay/WSL restart or token replay was performed.

Private final evidence includes `source-delta.json`, `publication.json`, `server-publication.json`, `authenticated-download.json`, `client-install-result.json`, `client-install-reconciled.json`, `answer-transport-verified.json`, `continuity-after.json` and `resolution.json`. A compact repair/await-safe-boundary record was added to the existing private five-competition scope. The hourly follow-up remains unchanged and active; no paused historical automation was resumed.
