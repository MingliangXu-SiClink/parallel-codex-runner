# macOS Transcript Rendering Lifecycle

Status at 2026-10-04 22:17 CST: macOS **0.1.410 / build 410** is published, downloaded, hash/signature-verified and prepared by the built-in update verifier. The formal client is **0.1.409 / build 409**; this work did not install or restart 410. Formal-client baseline PIDs and all baseline Node PIDs remain unchanged. No foreground QA or desktop input was used. The actual scheduler remains healthy and paired **0.1.69**; 0.1.70 activation returned 75 while a user run was active.

## Observation and Evidence Boundary

The supplied screenshot shows several overlapping layers of a long numbered Chinese reply below an otherwise normal prompt and Agent selector. It does not establish that the stored answer contains duplicates.

- Static inspection of the installed 408 binary confirmed its explicit TextKit 1 initialization and existing text-view clipping. Merely adding that initialization again would not address the observed defect.
- Before changing the implementation, an actual offscreen collection/renderer replay of 24 rapid candidate changes and 18 streaming/restyling/width changes had correct mounted reply views, line geometry and bitmaps. This did **not** reproduce the screenshot's unique compositor trigger.
- A controlled native-completion test did demonstrate that the old coordinator replaced a submitted batch's parent snapshot while that batch's completion remained pending. Text, data-source indexing and native geometry were therefore not protected as one applied snapshot.
- A separate retained-bitmap test demonstrated the former transparent text surface's dependence on ancestor repainting: drawing replacement text did not erase previous glyph pixels when only that surface owned the redraw. This is a stronger drawing-ownership contract, **not** a claim that transparency itself violates AppKit or that the test reproduces WindowServer behavior.
- The original four-test pre-fix run failed two tests with three issues. The same tests passed after the lifecycle changes. The basic replay's pre-fix passes are retained as negative evidence, not represented as a reproduction.

## Changes

- `ConversationTimelineView.Coordinator` retains one applied snapshot until the real native batch completion. Subsequent validated presentation snapshots coalesce to the newest target rather than modifying the pending batch. Completion is guarded by collection identity, conversation context and applied generation. A queued drain cannot overwrite a newer direct update; teardown discards pending work.
- Native completion handles interrupted animation without assuming data rollback. Deleted/reloaded item roots are hidden before insertion so retained exit views no longer draw obsolete branches. Configure paths restore visibility before same-content fast paths.
- Reply text views own an opaque background matching their existing semantic surface over the resolved canvas. Attributed-content, appearance/background and container-width changes invalidate the affected text surface, covering old positioned or restyled lines. Body clipping adds a second boundary around the existing non-self-resizing, clipped TextKit 1 view.
- Same-ID streaming retains the text view and selection. Agent changes retain the question, history and tabs rather than rebuilding the whole conversation. Projection and measurement caches remain enabled; no cache-clearing workaround or additional polling timer was introduced.
- Existing user-scroll handling cancels pending reading/disclosure restoration. Added a delayed-completion regression proving that user scrolling remains authoritative when a native batch later finishes.

## Non-Disruptive Validation

Background commands used `nice -n 15` and a single build worker. New native tests create unshown windows solely for AppKit backing scale. They do not order windows, activate applications, post desktop events or send physical input. Default test guards exclude foreground interaction suites. No Delivery QA client, native-control command, UI-smoke window, Android device or research workspace was used.

- Guarded client checks: **310 tests in 39 suites passed**, plus protocol/state checks and native icon validation. The new serialized lifecycle suite contains **11 tests** covering delayed completion, newest-target/reversion behavior, interrupted completion, deliberate user scrolling, detached hosts, retained-pixel replacement, hidden/reused items, dark/light semantic backgrounds, rapid candidate switching and streaming/restyling/width changes.
- Explicitly reviewed offscreen reading allowlist: **5 tests in 1 suite passed**, including dark/light candidate switching, retained mounted controls, per-Agent reading position, text selection during streaming, typography and conversation changes. The rest of that suite includes interactive cases and was not run.
- Release build, strict deep signature verification, and packaged math/syntax resource smoke checks passed. The added counterexample tests were finalized after the production build; no production Swift source changed after that build, which the release verifier also checks.
- Android, relay, pcrd wheel and installer release artifacts remain byte-for-byte unchanged. Existing 0.1.70 backend evidence was verified, not counted as new backend tests for this client-only change.
- The first counterexample-test compilation referenced a private presentation helper and failed. The test was corrected to inspect the actual native semantic surface; the final full headless run passed. No installed app was modified by that failed test compilation.

## Published and Prepared State

- Relay catalog release: `macos-release-a0b1c21c49ea437ca2a159d793110623`, version `0.1.410`, build `410`, non-revoked.
- Archive SHA-256: `c911016bd568bad89b07c949ba9a87124cf13fb92db4c8bf5163fee1542b12cf` (13,961,851 bytes).
- Signed client binary SHA-256: `c99d25379d7d291dfa0fcfb97e9af3dc7492045fe388d357f39e8d7591c23c91`.
- The normal controller update endpoints provided the release ticket and archive. The download was size/hash-verified; access tickets were not saved in evidence. The windowless built-in verifier prepared the exact signed 410 binary for `/Applications/Parallel Codex.app` without running the installer or reopening a window.
- Formal installation is 409, binary SHA-256 `ec1b772391f7769825f7f00c80feb186c4c7afcfcf4c08ee83f74ff09b88a11a`. It changed from the initial 408 observation independently of this work, before the deployment baseline was captured. This work does not claim to have installed 409.
- Prepared 410 supersedes the earlier prepared 409 candidate. Restart timing was requested separately because restarting the formal app may show its window; no restart is implied by publication or update preparation.

## Scheduler and Remaining Acceptance

The guarded 0.1.70 activation script returned **75** with `active_runs = ["pcr-20261004T140156Z-3d27870f"]` and no active operation. It did not change the scheduler or client. The existing heartbeat was updated to reflect actual 409 installation, prepared 410 and this newer running task. It remains quiet while blocked and must retain idle/model-catalog checks, preserve other processes, and never launch foreground QA or restart the client.

The screenshot's exact on-screen compositor failure has not been completely reproduced. Offscreen native layout/pixel checks and the fixed lifecycle contracts are substantive evidence, but they are **not** installed-410 formal-window acceptance or proof that every source of app instability is resolved. Such acceptance remains pending installation and observation without interrupting the user's desktop work.

Independent technical discussion challenged the initial framing and led to reversion, interrupted-completion, user-scroll and appearance/reuse counterexamples. Its agreement is not used as test evidence. No private manuscript text or screenshot was uploaded to that discussion.

Evidence directory: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/TranscriptStability-20261004`.
