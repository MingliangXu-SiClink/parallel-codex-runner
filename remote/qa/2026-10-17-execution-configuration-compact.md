# macOS Compact Execution Configuration, 0.1.445

## Scope

The independent execution editor was too wide, with phase labels occupying otherwise empty space. This change keeps the editor outside the message input and reduces its footprint without changing configuration semantics.

- Popover width: 640 to 480 points at standard text size. All four app text sizes scale this dimension consistently.
- Side-by-side stage gap: 56 to 32 scaled points. The narrow layout still stacks the stages, preserving the same view identities and any open editor.
- Remove permanent phase text, including "Next turn", "This turn" and "Started". Scope remains available in field tooltips and accessibility hints, not as another visible label.
- Already-started synthesis shows the actual run's configuration, a small lock and muted model/count text. Its count, model, reasoning and Fast fields remain genuinely disabled; the edit handler also rejects all four mutations. Candidate settings remain editable for their existing future-run scope.
- Preserve the content-width, background-free external summary from 0.1.444. No task, message, input, lifecycle, backend, updater trust or configuration-scope changes.

Exactly four of the 535 frozen application/test/build inputs differ from the installed 444 manifest: `ComposerExecutionConfiguration.swift`, `ExecutionConfigurationTests.swift`, `scripts/test-client.sh` and `Info.plist`. There are no added or removed frozen inputs.

## Verification

All release gates use the same frozen source and MacOSX26.5 SDK:

- `test-client.sh`: **577 noninteractive native tests in 56 suites passed**, plus protocol contracts and canonical icon checks.
- Strict cooperative-pool focused run: **71 tests in 5 suites passed**.
- New geometry regression covers all four zooms: two 208-point scaled columns separated by 32 points inside the standard 448-point inner width, and the retained stacked layout at a narrow width.
- New running-state raster matrix: Chinese/English, all four zooms, dark/light, unlocked/locked synthesis, totaling 32 cases. The existing 16-case editor and 48-case summary matrices are retained. Representative native rasters were visually inspected for layout, text fit and disabled-state clarity.
- Locked-synthesis regression rejects count, model, effort and Fast edits without modifying stored settings. Candidate editing, stale-context fencing, count intermediate typing and native input/selection/IME preservation tests passed.
- Source contract rejects reintroducing `Text(scope)` as a persistent phase label. Scope-only help/accessibility information remains.
- Test windows were offscreen and non-key. These raster/geometry checks do not claim live interaction or an offscreen accessibility-tree acceptance result.
- Optimized build, isolated-home process/resource smoke tests and the no-signing gate passed. No production prompt was used as a test.

Private evidence: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/ExecutionCompact-20261017/`. Release JSON records freeze source and log hashes; `screens/` contains native test rasters.

## Publication

- Version/build: **0.1.445 / 445**.
- Immutable archive: `release/clients/macos/local/Parallel-Codex-macos-0.1.445-local.zip`.
- Size: **14758887 bytes**.
- SHA-256: `eb1b400f1c7fcb8b2eeac378e7ef064109abf11f8ed99c60c71f6b5821689555`.
- Every extracted filename, byte count and SHA matches the frozen bundle manifest. ZIP file modes and embedded version/build are verified; no `__MACOSX` or `._` Finder metadata is included.
- This is a source-verified local-development package, not a signed remote-updater release. The signed channel and its trust checks are unchanged. The preceding 444 archive is retained.

## Installation, 2026-10-17 21:30 Asia/Shanghai

The first preflight stopped before any quit or application replacement because the current automatically opened conversation had no explicit saved navigation entry. The installer did not write a replacement entry or change the user's selection. A read-only follow-up confirmed an already adopted synthesis, an empty native editor, no draft in that workspace, and a matching finalized latest run. The narrow fallback requires those facts at each live guard, with no pending handoff, delivery, admission, answer or follow-up operation. It is not available to unaccepted results or nonempty/undurable drafts. The existing five synthetic exit-safety checks and disposable atomic exchange/reverse checks passed.

One actual normal replacement exited **0.1.444 / PID 95808** and launched **0.1.445 / PID 8027** in the background. No force quit, adoption-setting change, task command or data rollback was used.

- Every installed file matches the frozen 445 manifest. `lsof` confirms the actual loaded binary inode **186823407**, SHA-256 **`83987b650b2cb4ecb1570c0b808cb1eea65eaa0bb48e13bf72345bdac61b48d6`**.
- The reopened native conversation header hash and all visible Agent labels/selections match the immediate pre-quit view. The native input was empty immediately before this update and remains empty, with the same full-text hash. All existing draft maps, workspace/default settings, local queues, answer entries and receipt-store bytes/identity match the immediate baseline. Older draft text was not restored over the user's newer state.
- **pcrd 0.1.98 / PID 48685** and all **5 baseline persistent owners** retain their original process identities/start times. There were no actively executing runs at the installation boundary, so no claim of new native output or continued active computation is made. Nothing was stopped, retried, adopted or resent by this update.
- Backend health and pairing remain OK. These client-only checks do not independently revalidate the public relay.
- The actual external summary remains an **AXButton**, **207.5 by 28 points**, above/outside the input. Its unused trailing space still does not hit the button. No extra real window or modal was introduced. The full editor's compact/locked appearance was accepted using isolated native renders; no foreground popover was opened to manufacture live UI acceptance.
- Background launch retained foreground PID **13034**. No activation, simulated keyboard input, QA window, signing, Keychain access or system-security change was performed.
- The previous 444 program bundle only is retained privately at `ExecutionCompact-20261017/previous-0.1.444.app`. User data must not be restored to an earlier accepted-message state for a program rollback.
- The user-canceled hourly PCR automation remains absent. No monitor, Windows/WSL guard or remote service was modified.

Private deployment evidence includes `ui-observation-*.json`, `ui-before.json`, `native-install-before.json`, `install-before.json`, `installation.json` and `post-install-verification.json`.

**Status: source fixed, regression gates passed, immutable local package published, client installed and actual loaded bytes verified.**
