# macOS Execution Summary and Popover, 0.1.443

## Scope

The complete candidate/synthesis editor no longer lives inside the message input. An unframed, single-line summary sits above the input border. Selecting it opens an independent native popover with the existing eight count/model/effort/Fast controls.

- The input contains only the native message editor, input options and send/stop actions; its configuration divider is removed.
- The summary deduplicates identical model names, shows Fast only when enabled, and uses an icon/count fallback at narrow widths rather than growing into multiple rows.
- The popover retains independent candidate/synthesis settings, current-turn synthesis locks and the original edit-context fencing. Closing it does not send a message. Workspace, machine, navigation-generation or run changes dismiss stale presentation; any field commit still validates its original context.
- Existing count draft/commit semantics, native IME/selection preservation, persisted settings, queues, message receipts and the advanced Run Settings surface are retained. No backend or transport change was made.

## Verification

All checks below passed against the same frozen 535 application/build/test inputs:

- `test-client.sh`: protocol/layout contracts, 574 noninteractive native tests in 56 suites, and canonical icon checks.
- Strict cooperative-pool run: 68 tests in 5 suites, including execution configuration, native composer identity/IME, model options, workspace readiness and transcript caching.
- The new layout matrix covers 48 collapsed-summary and 16 standalone-popover cases across Chinese/English, all four display zooms, dark/light appearances and 260/350/960-point summary widths. Native offscreen windows remained neither visible nor key. Representative native raster outputs were visually inspected.
- Native input text, selection and marked-text identity survived width changes. The collapsed composer no longer reserves pipeline-editor height. Locked synthesis summaries use the run's actual configuration, not the next-turn draft.
- No-signing build gate, optimized app build, isolated-home process/resource smoke tests, archive integrity and byte verification passed. Atomic exchange and reverse exchange were tested only on disposable private fixture directories.
- Foreground interaction, global keyboard simulation and production-message test submissions were not performed. The live computer-use tool is unavailable; offscreen rendering is not represented as live user-window interaction acceptance.

Private evidence: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/ExecutionPopover-20261017/`. `release-*.json` record source/log hashes; `bundle.json` records every packaged file. Screens are in `screens/`.

## Publication

- Version/build: **0.1.443 / 443**.
- Immutable local archive: `release/clients/macos/local/Parallel-Codex-macos-0.1.443-local.zip`.
- Size: **14881936 bytes**.
- SHA-256: `9722905078b19beee4004ad406dd621189313c789eb45bd75996c68cf36d0663`.
- Distribution: source-verified local development. The signed remote-updater manifest and publisher verification are unchanged. No signing or Keychain command was run.

## Initial Installation Boundary

Published does not mean installed. At this record's initial creation the actual client is still **0.1.442 / PID 85156**; pcrd remains **0.1.98 / PID 48685**. No normal owner, Agent or competition job was stopped, retried, adopted or resumed by this change.

The user has been asked for a fresh safe reading selection before the client is normally restarted. The old client's ordinary exit may adopt a selected successful result, so a cached selection or a fabricated UI observation cannot authorize termination. The dedicated `install443.py` refuses installation without a fresh genuine user reply, stable message/window gates and matching released/installed bytes. Installation has not been executed. Preserve all queues, drafts, settings and original receipt identity; never restore user data from an older snapshot.

## Actual Installation, 2026-10-17 19:56 Asia/Shanghai

The user subsequently confirmed readiness. The existing authorized native accessibility interface was available in this turn, so fresh read-only observations replaced the earlier lack of a live inspection path. No accessibility permission, security setting or keyboard simulation was introduced. An existing PCR Settings window was closed through its normal close button without activating the application. The final checks observed exactly the main window, an empty editor, no modal and the selected, executing **Agent 1** in the compression workspace.

- The actual pre-install client was **0.1.442 / PID 67958**, not the initial publication-time PID. The normal application lifecycle helper exited it without force and launched **0.1.443 / PID 71711** in the background. Foreground PID remained **20915** across the launch.
- Installed files match every entry in the frozen 443 bundle manifest. `lsof` verifies that PID 71711 actually loads the installed binary, inode **186738785**, SHA-256 **`03d959dbaf25d9a672c48f039732b6c94141f55ba6b146cfa82b0418e4ee642c`**. The immutable archive and all six publication gates were reverified before installation; no published bytes were rebuilt or overwritten.
- **pcrd 0.1.98 / PID 48685**, all **8 persistent owners**, and the **16 original Node/Codex processes** remained unchanged. The currently running batch is **`pcr-20261017T114953Z-889e3892`**, already started before this installation. All eight Agents retain their original physical identity, owner, workspace, native thread/turn, accepted message identity, input and configuration; each produced additional native output after installation. No Agent command, adoption, stop, retry or resend was issued by the installer.
- The older compression turn and its original queued continuation had already advanced through the normal client flow before the installation baseline. That normal progression was not rolled back, replayed, or attributed to this update. Drafts, current queues, settings, question-answer entries and receipt-store bytes match the immediate pre-quit baseline exactly. Store identity remains **`cc44b3a9d69c4e55a8343d27cebb258d`**.
- Fresh `system.capabilities`, `system.health`, `controller.status` and `codex.model_options` verify the unchanged backend, healthy pairing and effective **GPT-6.1 Sol / max**. The original authenticated relay connection remains continuous; public health returns **200**, and anonymous device access remains **401**. An earlier transient read timeout during task transition was not treated as task failure and did not trigger a restart.
- Read-only inspection of the actual running UI confirms the **28-point-high external summary above all input-control bounds**, with no input ancestor or overlap. Agent 1 remains selected, the editor remains empty and there is one real window with no modal. No foreground popover interaction or production settings edit was used as a test; the complete editor's interaction/rendering coverage remains the isolated release tests listed above.
- No signing, Keychain operation, signed updater-manifest change, data rollback, pcrd restart or research-file maintenance was performed. The previous **0.1.442 program bundle only** is retained privately for rollback.

Private acceptance evidence in `ExecutionPopover-20261017/`: `user-install-authorization.json`, `ui-observation-*.json`, `native-install-before.json`, `install-before.json`, `installation.json` and `post-install-verification.json`. The last record contains all passed byte, process, native-continuation, UI-layout, message-preservation and health checks.

The hourly monitor's new baseline is `CompetitionWatch/snapshot-20261017T115949.648184Z.json`: client443, the unchanged pcrd98, eight running Agents, eight live owners and empty outbox/local queue. The previously queued prompt's full SHA-256 **`f41fc643468426ff2a3c8093d0636052796f72d909693bf2f259847764b7e5a8`** matches the already accepted new batch, which continues from physical Agent 8 of the finalized older turn. Its queue removal and prior owner retirement must not be mistaken for installation damage or used to replay that prompt.

**Current status: 0.1.443 is published, installed and actually running. The earlier pending-installation boundary above is historical, not the current state.**
