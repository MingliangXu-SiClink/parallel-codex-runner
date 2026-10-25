# macOS 0.1.408: execution state is not loading

Status: verified, published, installed through the in-app updater, and restarted as macOS 0.1.408/build 408. Only the client was restarted.

## Starting Point

The installed client was verified as 0.1.407/build 407. Its previous fix preserved completed Agent status while reading content, but `ResultStatusGlyph` still used an indeterminate `ProgressView` for running, preparing, waiting-for-models, and stopping Agents. In the formal client's result inspector, a working Agent was exposed as a busy indicator.

## Changes

- Work uses a static blue `waveform.path`; preparation uses `gearshape`; queued and model-waiting Agents use `clock`; stopping uses `stop.circle`. Completed green circles, failed red crosses, and stopped terminal symbols are preserved.
- Every Agent selector includes a visible phase label. Queued means `Queued`/`排队中`; model waiting means `Waiting for model`/`等待模型`. Preparing and stopping remain distinct from running and terminal states.
- Glyphs occupy the same 12-point scaled slot. Phase labels reserve a measured, text-size-aware width; explicit tab widths match the scroll/reveal geometry. State changes do not squeeze the Agent identity or shift neighboring tabs.
- The selector keeps its existing name, phase, recommendation, selection, and candidate-position accessibility label. Result details hide decorative glyphs and expose the execution phase as text, not a progress control.
- Body, file, network, and update loading indicators remain in their own content areas. No backend execution, queue, continuation-source, or task-lifecycle behavior is changed.

Code: `ConversationResultsInspector.swift`, `ConversationResultNavigator.swift`, `ConversationResultDetails.swift`, and `ConversationPresentation.swift` under `apps/macos/Sources/PCRRemoteMac`.

## Verification

New tests check execution symbols and localized labels, native symbol availability, all supported text sizes and both appearances, absence of native indeterminate controls for execution glyphs, fixed glyph dimensions, and late-Agent visibility in two coexisting narrow selectors. Source contracts additionally reject execution `ProgressView` and require the visible scaled phase label.

Final evidence is recorded in `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/AgentStatus-20261004`. All 360 tests in 44 suites passed on the final code. Protocol contracts, desktop state, native layout/scroll/icon checks, packaged resource smoke tests, and strict code-signature checks passed. The final test log contains no continuation-misuse failure.

Native acceptance used a distinct bundle identifier, private HOME, and a fake Unix socket with eight mixed execution states. Working, stopping, and completed selections were checked independently; execution glyphs were not exposed as loading controls. Narrow selectors, both appearances, and all supported text sizes were covered by native tests. `native-acceptance.json` records the accepted binary and screenshot hashes. The isolated QA app and its fake server were closed before deployment. No real Agent or user queue was manipulated.

The final signed production binary SHA-256 is `50a29101f0bd34b92d25255a6fe2b658024ecdde8ad1af8ddd31a05fbaef3d9f`; its signature-stripped code matches the independently tested native build. The 13,889,262-byte release archive SHA-256 is `bd0c446fb52be3ac064187498e43f5e7ee5df3fbcd1cdc795a9727b80f4b7cf7`.

## Deployment

- The trusted relay catalog published the non-revoked macOS 0.1.408/build 408 release. Other component versions were left unchanged.
- The installed 0.1.407 client downloaded and prepared 0.1.408 through its own updater. Before restart, `verify-ready` checked the prepared manifest, exact binary, target path, archive hash, and strict signature while confirming the installed client was still 0.1.407.
- The authorized `update.restart` action installed the prepared release and restarted only the macOS client. The installed binary has the exact accepted SHA-256 and valid signature; the new client PID is 87906. Settings visibly reports `0.1.408 (408)` and the latest version.
- Read-only formal-client acceptance showed all five real candidates still working, with visible `工作中` labels and static blue waveforms in the selector and result details. The original conversation and Agent 1 selection were retained, and GPT-6.1 Sol remained selected. Real candidate tabs and lifecycle actions were not clicked.
- Post-update `system.health` remained healthy and `controller.status` remained paired. pcrd stayed at 0.1.68 with the unchanged service plist SHA-256 `517a3d6136b90648689111366acec3e7537e947335f7df6b427ad535e93943e1`; run `pcr-20261004T022618Z-e3fb4228` remained active. Baseline Node services retained their PIDs and start identities. Four unrelated short-lived non-Node processes naturally exited, as recorded by the verifier; this is not reported as every process remaining alive.
- `installed-running.png`, `installed-running.ax.txt`, `installed-version.png`, and `installed-version.ax.txt` record the actual formal-client UI. The pcrd 0.1.69 runtime remains staged and was not enabled by this client update.

## Discussion

Continued the actual ChatGPT Web discussion at `https://chatgpt.com/c/6ac13f12-19b8-83e8-98da-518f59751ddc`. Independent feedback prompted visible phase text instead of relying only on a new symbol, avoiding a frozen spinner-shaped ring, stable icon/text slots, and the more explicit queued label. The review is design input, not installation or performance evidence.

## Scope

This is a macOS client presentation repair, not a claim of faster execution or newly measured switching performance. pcrd and Android are not changed or restarted by this release. The separately authorized idle-only pcrd 0.1.69 activation retains its existing safeguards.
