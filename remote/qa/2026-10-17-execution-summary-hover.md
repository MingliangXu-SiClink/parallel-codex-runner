# macOS Execution Summary Hover, 0.1.444

## Scope

The external execution summary had both a full-width label frame and the field editor's filled hover style. Its unused trailing space consequently behaved like a selected table row.

- Remove the summary label's full-width frame. Keep the existing narrow-window fallback and one-line, 28-point scaled height.
- Give this entry its own background-free button style. Hover/open state emphasizes only existing foreground content; pressing does not resize it. Keyboard focus retains a content-sized outline.
- Preserve one button and one independent candidate/synthesis popover. Add the explicit accessibility button trait to the combined summary. Field editor and option-row selection styles are unchanged.
- No backend, task, message, configuration semantics, input handling, updater verification or application-termination behavior changes.

The only application/build/test input differences from the installed 443 manifest are `ComposerExecutionConfiguration.swift`, `ExecutionConfigurationTests.swift` and `Info.plist`.

## Verified Checks

All checks use the same frozen 535 inputs and MacOSX26.5 SDK:

- `test-client.sh`: 575 noninteractive native tests in 56 suites passed, plus protocol contracts and canonical icon checks.
- Strict cooperative-pool focused run: 69 tests in 5 suites passed.
- The 48-case collapsed-summary matrix covers Chinese/English, all four zooms, dark/light appearances and 260/350/960-point widths. Geometry is captured inside the external host frame; wide layouts must leave at least 80 points outside the entry's width. The independent editor's 16-case matrix is retained.
- Native raster comparison of resting and emphasized states exercises the style's shared hover/open branch: foreground-only change, unchanged entry dimensions and no filled rectangle. Representative wide, narrow and emphasized rasters were visually inspected.
- Existing candidate/synthesis edit fencing, locked synthesis values, count draft/commit semantics and native input identity/selection/IME tests passed. No production message was used as a test.
- All test windows remained offscreen and non-key. Offscreen SwiftUI accessibility children were unavailable, so the geometry/pixel tests do not claim live accessibility-hit-test acceptance.
- The no-signing build gate passed. No signing, Keychain operation, system-security change or signed remote-update manifest modification was performed.

Private evidence: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/ExecutionHover-20261017/`. Source/log hashes are recorded in `release-*.json`; native raster outputs are in `screens/`.

## Publication

- Version/build: **0.1.444 / 444**.
- Immutable archive: `release/clients/macos/local/Parallel-Codex-macos-0.1.444-local.zip`.
- Size: **14758043 bytes**.
- SHA-256: `19bb0fbb11e322027329ee6437575bd378b2abfaac227bc93c6d26a2bc25d0a6`.
- Optimized build, isolated-home process/resource smoke tests and archive integrity passed. Every unpacked filename, byte count and SHA matches the bundle manifest; ZIP file modes and embedded version/build are verified. The archive contains no Finder `__MACOSX` or `._` metadata.
- This is a source-verified local-development package, not a signed remote-updater release. The 443 archive is unchanged and retained. Atomic program exchange and reverse exchange passed on disposable private fixtures only.

## Deployment Boundary

At this record's initial creation the actual client is still 0.1.443, PID 71711. The fresh native UI selected a successful, unadopted synthesis result. Its existing normal-quit behavior could adopt that result; the client has not been quit, force-terminated or modified to bypass this condition. An explicit, reversible adoption-setting change has been requested before attempting installation. No task was stopped, adopted, restarted or resent.

The user's hourly PCR heartbeat has been deleted through the automation API, with `deleteStatus=deleted`; its configuration file is absent. The canceled monitoring/recovery workflow has not been resumed. Installation-only read checks are not a new monitor, and the Windows/WSL relay guard is untouched.

## Latest Installation Preflight

After publication, fresh read-only accessibility checks show that the user has navigated to an executing candidate Agent 1. The earlier successful-result exit hazard is no longer the observed blocker, so no adoption setting needs changing. The native editor still contains an unsent 33-character draft, and the application is in active use. No draft text was logged, cleared or submitted. A new, specific confirmation requests permission to preserve that draft and normally restart only the client; the earlier adoption-setting question is superseded. The published package has not yet replaced the loaded 443 client. No installation attempt or production setting change has been made.

## Actual Installation, 2026-10-17 20:52 Asia/Shanghai

The user explicitly authorized the update while retaining the unsent draft. Fresh UI checks observed candidate **Agent 3 still running**, one real main window, no modal, and the same 33-byte native editor value as its original persisted conversation draft. The installation guard now accepts a nonempty draft only with this explicit authorization and matching native/persisted full-text hashes; it does not clear or submit the text. The original normal-quit, task, admission, queue, handoff and answer safeguards remain in force. No adoption setting was changed.

- The one normal client replacement exited **0.1.443 / PID 71711** without force and launched **0.1.444 / PID 95808**. Every installed file matches the frozen 444 bundle manifest; `lsof` confirms the loaded binary inode **186799788**, SHA-256 **`418a79005f0f2d30afdbfbd32a55212dde52462b6f7a47bd909a2f765325b920`**. The preceding 443 program bundle only is retained privately for rollback.
- Actual reopened native input contains the same complete 33-character/33-byte draft, SHA-256 **`3d124c5531c430e5d4a143093b70a18c3817a308561c1ac0c1a67f16a4f95e57`**. Native UI and persisted conversation context match. All draft, local queue, workspace settings, default settings, answer entries and receipt-store identity hashes match the immediate pre-quit baseline. No input text was logged, edited, cleared or sent.
- The actual summary is an accessibility **AXButton**, **207.5 points wide rather than 1028**, with the original **28-point height**. It remains above and outside all input-control bounds. A native accessibility hit test 40 points beyond its trailing edge resolves to the surrounding AXGroup, not the button. This verifies the real content-width hit region rather than merely an offscreen screenshot.
- **pcrd 0.1.98 / PID 48685** and all **7 baseline persistent owners** retain their original starts. Active run **`pcr-20261017T122259Z-9e751fc2`**, physical Agent 3, owner **78523**, native Node **78602** and Codex **78616** retain their original process starts, workspaces, prompt/config hashes, native thread/turn, accepted message and recovery identity. The original worker appended native output at **20:56:39**, after the client update. A quiet three-second postcheck interval was not treated as failure or used to restart any process.
- Local backend health remains OK, pairing is unchanged, and original receipt-store bytes/ID **`cc44b3a9d69c4e55a8343d27cebb258d`** are unchanged. No claim about an independently retested public relay is made by these client-only checks. No task command, stop, adoption, retry or resend was issued.
- Background launch retained foreground PID **20915**. Later UI observations include the user bringing PCR forward and an **AXHelpTag** tooltip; the checker classifies that explicit native help role separately from real windows. No window was closed or focus changed to obtain acceptance.
- No signing, Keychain access, updater trust change, pcrd restart, data rollback or research-file operation was performed. The canceled hourly heartbeat remains absent; Windows/WSL services and guard are untouched.

Private evidence includes `user-install-authorization.json`, `native-install-before.json`, `ui-observation-*.json`, `install-before.json`, `installation.json`, `post-ui-observation-*.json`, `post-install-verification.json` and `post-native-output.json`.

**Current status: 0.1.444 is published, installed and actually running; the unsent draft is restored and verified. All pending-installation descriptions above are historical.**
