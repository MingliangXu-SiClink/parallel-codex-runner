# Native Agent Questions: macOS 0.1.457 / pcrd 0.1.101

## Confirmed Cause

The reported compression question was a real native **asynchronous question**, not a blocking `item/tool/requestUserInput` request. The old implementation handled only the latter. The native adapter retained `AgentMessage.delivery = async` and its structured `questions` in the Agent's original output, but neither a durable async question receipt nor an async answer delivery path existed. The UI consequently displayed the Agent's prose without answer controls. This is not a model failure or a reason to resubmit the original task.

The original evidence belongs to run `pcr-20261018T101331Z-12235289`, physical Agent 1, native thread `01a12129-a8aa-7ef3-a345-b5168e31a205`, turn `01a14e80-c247-7bf0-a4a5-881f1e83e16b`, item `call_68315bbcfa6f4268a835c7e9ac888ea2`. The native event at `2026-10-18T11:13:00.640Z` contains the user's two reported choices: finish the already-started ASUS evaluation before migration, or migrate to SiClink before reevaluation. Both were retained structurally in the original `stdout.log`, lines 773-774. No answer to this production question was selected or submitted during diagnosis.

While this work was underway, the user naturally finalized and continued the compression task. The original run and its subsequent `pcr-20261018T115110Z-c10dcb88` were already finalized on later readback. They must not be reopened or replayed to demonstrate this repair. Historical, stopped, adopted and superseded question records are read-only.

Two secondary client defects were also corrected: decoding a valid option without its optional description could discard the detail response, and the manual refresh path did not merge question records independently of final-answer loading.

## Implementation

- Normalize only native structured async question items. Never infer an actionable question from Markdown, ordinary prose or a permission request.
- Retain native thread, original turn/item and the exact `request_user_input_async` question identifiers. Async receipts use a separate store so existing version-pinned blocking owners cannot invalidate them.
- Import existing capable owners' original output incrementally, with bounded reads, partial/oversized-line handling and log-identity checks. Existing outputs and native sessions are not rewritten.
- Keep the existing in-conversation question surface. Actual native radio controls select an option; custom text remains inline. No answer is preselected, and only the user's explicit submit commits it. Drafts and receipts remain scoped to machine, workspace, run, physical Agent and original question.
- An active capable owner sends the committed answer once with native `turn/steer` and the original expected turn. An explicit rejection is deferred, not repeatedly resubmitted. A lost acknowledgment is unknown, not permission to retry blindly.
- After a completed execution, deliver only the newly confirmed answer by resuming the same native thread in the retained physical Agent workspace/home, preserving model/command parameters and archiving the prior receipt/result. No workspace preparation, original-task prompt, new competition task, batch retry or result adoption runs.
- A completed Agent in a still-running batch may save its answer, but no healthy batch owner is replaced. If its native owner cannot consume the answer in flight, delivery waits for the existing owner to become durably idle. The UI explicitly distinguishes saved/queued delivery from native acceptance.
- Idle-owner replacement is protected by the existing run-operation lock, exact owner/checkpoint identity, no in-flight operation/queue, original native completion and a workspace lease. The replacement is publication-gated before side effects. User stop, adoption and an existing child continuation prevent delivery.

Native protocol shape was checked against the installed Codex CLI **0.159.2** generated schemas and actual loopback native execution. The general supported steering API is documented in the [official app-server documentation](https://learn.chatgpt.com/docs/app-server). The request's structured native fields and end-to-end tests, not a webpage description of a similarly named tool, establish the concrete async protocol used here.

## Verification Boundaries

All submitted test choices belong to disposable loopback fixtures, never the user's competitions. Both blocking and async tests run the actual CLI, not only a mocked HTTP/UI response. They cover original native output after delivery, same-turn steering, same-thread idle resumption, durable owner handoff, preserved files/results, duplicate submission, disconnection/restart, lost/late receipts, user stop and other Agents remaining independent. Native client tests exercise actual radio-button actions, durable answer commit, custom answer state, receipt reconciliation and question-only refresh without replacing visible conversation content.

The embedded browser collaboration backend was unavailable in this session, so no ChatGPT Web exchange is claimed. No alternative account/browser backend, real-task test answer, signing-identity migration, Keychain/trust change or competition concurrency policy change was used.

Private evidence, candidate packages, frozen gate records and delivery readbacks are under:
`~/Library/Caches/ParallelCodexMaintenance/AgentQuestions457-20261018/` (owner-only).

Final publication, management activation and client installation results are appended only after their corresponding live verification succeeds.

## Final Results, 2026-10-18

### Tested And Published

- Final frozen source: **1,219 Python tests / 309 subtests** passed. The independently installed wheel passed **1,218 tests / 309 subtests**, with only the source-checkout-only vendored-file test excluded. Installed payload bytes were rechecked against the wheel.
- macOS: **618 native tests in 58 suites** passed on unchanged final inputs (332.219 seconds); **31 focused strict tests in 4 suites** also passed. Protocol/icon, encrypted signing-store and signing-disabled isolation gates passed. The real optimized signed build, native deep/strict signature and archive/resource/byte checks passed.
- Actual CLI loopback checks passed for both original-turn async steering and completed-thread resumption, plus the real manager's durable idle-owner handoff. The independent two-Agent RPC fixture also passed, including different original answers, duplicate submission, reconnect/IPC restart and the other Agent remaining unaffected. No production question was used as a test.
- Immutable pcrd wheel **0.1.101**: SHA-256 `c0a344d2ccc8618ed8bbcfe6cf2e353fb2d5f0bd694819af34d6e058b4a3fdf6`. Only the seven audited question/adapter/worker/runtime/controller modules differ from 0.1.100.
- Immutable signed macOS archive **0.1.457 / build 457**, **14,802,427 bytes**: SHA-256 `5220fde47a83103f9a5025f5b728c88e9d158abb15fbc7e3607a7313d05704c2`.
- The official Relay catalog now contains `macos-release-a665e24efa954f94ae045dbaab943e8c`. Readback matches build, version, size, hash and the existing **Parallel Codex Release Signing** publisher. The real installed 456 client received `available=true`, downloaded those exact authenticated bytes and passed its unchanged native verifier. After installation, the actual 457 client correctly receives `available=false` against that same current catalog. Publishing was not mistaken for installation.

### Enabled And Installed

The independently installed 0.1.101 runtime was enabled only after fresh locked management-boundary checks proved detached-owner independence, idle prompt admission/delivery/queues, no adoption operation, preserved receipt-store/pairing identity and stable current-window drafts/answer state. Only the management daemon changed, **PID 55480 to 34132**. All **11 original owners** retained PID/start/group identity. The **40 original native Agent identities** retained thread, turn, message, command, workspace, native PID and output-file identity; **36** had new native output during this management-only transition. Local health and the independently authenticated Relay update business requests were verified separately.

The real client was normally quit and reopened through the production native updater/helper, with `activateOnRelaunch=false`, **PID 80018 to 37087**. The actual loaded **0.1.457** executable inode is `188517653`, SHA-256 `e49cb9d33651296d0d7ce5fd5da63eaaa467180e06432c0e6961d53b0c19fd06`; the complete installed signed bundle matches the published archive. The updater retained the previous 456 program bundle, not a rollback of user data.

The native installer succeeded and journaled `launched` on its first actual attempt. An immediate post-launch observer sampled the UI before the composer appeared, so that private wrapper's final visible-input assertion failed after installation and persistence checks had already passed. A later **read-only** reconciliation verified the restored input hash, drafts, outbox, accepted handoffs, local queue and original Agent-answer file. No second installation, draft reconstruction or user-data rollback occurred. Later workspace/Agent navigation changes were recorded rather than overwritten. This observation is not presented as proof that every historical conversation-loading issue has been fixed by this question-channel release.

After client replacement, management remained PID **34132** and all 11 owners remained unchanged. The same 40 native identities were still intact; **38** had further native output, including original tool events. A bounded absence of new bytes for the other two was not classified as a stuck Agent. Maintenance submitted **zero real answers or task prompts**, adopted no result and sent no stop signal to a production worker.

### Actual Question Readback And Limits

The 0.1.101 API advertises both question capabilities. Live readback at `2026-10-18T12:43:31Z` found two existing async requests in `pcr-20261018T115143Z-50f395f2`, physical Agents **2 and 7**. Both retained their original native question IDs, **two choices and custom-answer support**, state `open`, delivery `none`, and no committed answer. They were not recreated, selected or submitted by maintenance. The historical compression example remains finalized rather than being revived for an acceptance demonstration.

Healthy version-pinned 100 execution owners were deliberately **not** killed or hot-replaced. New 101 owners support in-flight original-turn async delivery. When an existing old owner cannot consume an async answer while running, the committed answer remains queued until its safe idle handoff boundary; the client displays that waiting state instead of falsely claiming native delivery. Actual user receipt/output for those still-unanswered production questions is therefore **not claimed**. The completed-thread and active-turn delivery paths were validated with real native execution in isolation.

The signed publication, independent installation, management activation, client restart, native continuity and user-answer acceptance are separate gates. Android, Relay application code, research directories, existing computation/concurrency policy, signing identity and system trust were not changed.
