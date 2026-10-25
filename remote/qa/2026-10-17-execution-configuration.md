# macOS Execution Configuration, 0.1.442

## Scope

Replace the composer's two-column model/effort form with a purpose-built execution configuration surface. This is a client-only change; pcrd, research files, user turns, and running workers are not modified.

- Candidate and synthesis roles each expose count, model, reasoning, and Fast in the composer. Counts belong to the role, model names carry the primary typography, and reasoning/Fast are secondary editable values without permanent field frames.
- The eight values have independent native Button semantics and anchored single-parameter editors. Model discovery retains the configured value and Codex-default identity; Fast retains Automatic/On/Off, not a binary approximation.
- Count typing, increment/decrement, and presets edit a local draft. Return or the checkmark commits; closing the popover does not publish intermediate input. Candidate count is 1...the advertised limit; synthesis additionally permits zero.
- Candidate changes retain the existing next-turn contract. A not-yet-started synthesis retains the existing live-update contract; started synthesis is read-only and shows the authoritative run configuration. Mixed next/current-turn settings are not joined by a misleading execution arrow.
- Narrow and wide arrangements use the same view tree. Narrow stages request their minimum fitting height; the stage Layout receives that proposal directly rather than through a flexible frame which changes placement semantics. Two-row fitting is checked against rendered ink bands, not only the container height.
- The advanced Run Settings sheet reuses this component rather than keeping a second eight-field form. Other execution, history, workspace and advanced settings remain available.
- Model-selection callbacks and local editor commits are scoped to machine/workspace/navigation generation/run. Cancelled synthesis-update replies cannot publish over a later edit.

## Verification

Private evidence: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/ExecutionSurface-20261017/`.

- Strict cooperative-pool focused verification: 66 tests / 5 suites passed on the final source, including count intermediate states, isolated persistence, stale contexts, synthesis locks, model discovery, the service's `auto (max)` wire label, retained marked text, actual native two-row paint, and the previous conversation-readiness/presentation cache regression.
- Native offscreen renders cover Chinese/English, light/dark appearance, four display zooms, 350/960-point surfaces, unlocked/locked running scopes, and separate model/count editors. Windows were never shown or made key; no production controls or global keyboard input were used.
- A pre-existing model-options test fixture blocked socket I/O on a cooperative Task. Under the strict single-worker test it stalled its own loopback exchange. It now serves the isolated socket on a dedicated DispatchQueue. The failed fixture run, bounded sample, and superseded layout prototypes are retained; only final matching-source reports may authorize release.
- Interactive foreground-only editor, scroll-publication, interaction-contract and motion suites are deliberately excluded by the established noninteractive test entry point. They are not claimed as executed.

## Final Publication

- Full matching-source entry point: 572 native noninteractive tests / 56 suites passed, plus protocol/state contracts and the icon gate (8 PNG slots / 7 native rendering sizes).
- Strict focused entry point: 66 tests / 5 suites passed. The same source hashes are recorded in full, strict, no-signing, build, process smoke and resource smoke reports.
- Built version/build: 0.1.442 / 442. Resource and process smoke checks use an isolated private home and do not contact the production service.
- Published immutable local archive: `release/clients/macos/local/Parallel-Codex-macos-0.1.442-local.zip`, 14848088 bytes, SHA-256 `6e339f6a36956e6904981088cec7547b842c9559027c65e5b9bceae3c5b8da4f`. Metadata is `0.1.442.json`. This is a source-verified local-development package, not a signed remote-updater release. The remote manifest and trust checks are unchanged.

## Initial Installation Boundary

At 03:31 UTC the real client was still 0.1.441, PID 4826; its installed files exactly matched the previously verified 441 manifest. The current real window was viewing completed, adoptable Agent 4 in `pcr-20261016T044140Z-9d65cdfe`, workspace `ws-17b82a4b3e224d41`, with Sync Back enabled. Seven other candidates in this run were still running. The existing app delegate adopts this selected result on normal quit when these conditions hold. Therefore no quit, installation, selection change, settings change, adoption, stop, retry or message dispatch was attempted.

The user was asked to switch the reading selection to a still-running Agent before a safe client restart. At that preflight the request was unanswered. `exit-eligibility.json` records the exact read-only boundary and `client_quit_attempted=false`; no installation-success record existed at that point.

The read-only check confirmed pcrd 0.1.98, PID 48685, health `ok`, paired controller, available effective GPT-6.1 Sol and the existing authenticated relay connection. All eight persistent owners remained alive. Outbox and accepted handoffs were empty; the one original local queued message remained. The client program and production data have not been replaced or rolled back.

No signing, Keychain operation, production-message submission or backend restart occurred in that initial attempt. Installation and real new-UI acceptance were pending a safe exit boundary; publication alone was not reported as deployment.

## Completed Local Installation

After the user's explicit ready-to-update reply, the actual selected synthesis had already finished. Its ordinary quit path was still adoptable. The installed 441 program and its workspace-navigation source were matched to their frozen evidence, confirming that changing the reading workspace does not finalize a result. A read-only navigation temporarily selected the still-running memory workspace's Agent 1. No result, execution setting or message was changed. After installation, the original fine-hand workspace and physical synthesis Agent 10 (displayed as 综合 2) were restored.

At **2026-10-17 14:30:34 Asia/Shanghai**, the actual local client was updated from **0.1.441/PID 4826** to **0.1.442/PID 85156** by normal termination and atomic program-bundle exchange. The existing source-verified local package was used; no build/signing/Keychain operation or remote-updater trust exception was introduced. The explicit human update request permitted normal termination of the foreground client; background launch used `activates=false`. Six isolated authorization checks covered the private installer predicate, including rejection of unapproved foreground exits and ambiguous/missing application instances. All existing active-Agent, fresh-window, message, pairing and owner gates remained mandatory.

Actual loaded binary:

- Inode: `186588493`.
- SHA-256: `4e3e3cb6f6831fcb701dd19809b7015b87072f47ec9906f6d411f2f7ef2b3f2a`.
- Complete installed bundle bytes match the frozen 442 bundle manifest; all matching-source release reports and the immutable published archive were reverified.

Post-install acceptance at **14:31:22 Asia/Shanghai** confirmed:

- Global pcrd **0.1.98/PID 48685**, its LaunchAgent definition, pairing and original command-receipt store were unchanged. Effective model remained `gpt-6.1-sol`; the existing authenticated relay connection remained connected.
- All **eight** original owner identities and all **22** original Node/Codex identities serving the eleven still-running Agents were preserved. Short-lived SSH descendants changed independently during observation; no signal or restart targeted them. The two active groups produced **22** and **30** native event records after installation without a test message or forced output.
- Drafts, settings, question-answer entries and the exact original local queued message were preserved. Outbox and accepted handoff remained empty. The completed fine-hand run remained unfinalized and unsynced; no candidate or synthesis result was adopted.
- The actual main window exposed the new `composer.execution-configuration` surface and all eight independently named count/model/reasoning/Fast controls. A native screenshot inspection confirmed the candidate-to-synthesis composition rendered with readable values and no overlapping controls. No production configuration was edited to test it.
- The original 综合 2 reading view was restored. The new client remained in the background; frontmost PID stayed **8466** after launch and view restoration.

The retained program-only rollback is `previous-0.1.441.app`; user message data was not rolled back. Private evidence includes `ui-before.json`, `install-before.json`, `installation.json`, `final-acceptance.json` and `ui-installed.json` in the same maintenance directory. This closes actual installation and UI-readback acceptance for 442, not merely publication.
