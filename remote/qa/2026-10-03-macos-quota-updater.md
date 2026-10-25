# macOS Quota Cache and In-App Updates

## Scope

- macOS client: 0.1.401, build 401.
- Scheduler: 0.1.68, isolated installed runtime; activate only after every Agent, synthesis and finalization operation is idle.
- Android, relay application code and physical phones are unchanged.
- Do not stop, adopt, abandon or re-run user tasks to deploy this release.

## Diagnosis

The old quota reader had only an in-memory cache and a 15-second total read budget. A client restart loses its local last-good snapshot; a scheduler restart also loses the server snapshot. The UI could therefore have no number to display after a transient read failure.

Live read-only probes succeeded in approximately 9, 12.4 and 10.8 seconds. In the timed probes, account discovery took approximately 5-10 seconds. The expected proxy environment was present. There is no preserved phase diagnostic for the screenshot's failure, so these observations do not prove that the screenshot was caused by a timeout or a missing proxy.

Evidence: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/QuotaUpdater-20261003/usage-diagnosis.json`.

## Quota Behavior

- Successful normalized limits are written atomically with private file permissions. Failures and pending refreshes never overwrite last-good numbers or their original fetch timestamp.
- Cache identity includes the effective executable path, Codex home, credential/configuration source digest and stable account identity. No raw token, account ID or configuration is stored in the snapshot.
- Cold restore is allowed only for an unambiguous file-credential source. Explicit auto/keyring/ephemeral stores, alternate providers, credential environment overrides and known additional managed/system configuration sources are memory-only.
- A changed account/configuration source, explicit sign-out or unsupported account invalidates the relevant old snapshot. A stale in-flight request cannot commit into a new source.
- Account discovery and limit reads use bounded transient retries on one read-only app-server connection, with a 25-second total budget and sanitized phase diagnostics. No model thread or turn is started to obtain quota.
- The client retains the last actual number during refresh/failure. Reset boundaries label the number as a previous observation rather than inventing a new balance.
- Native QA also found that changing the refresh cadence did not wake a previously scheduled 60-second sleep. Poll identity now follows the current context and server pending/retry/settled phase, so a manual refresh can settle promptly without a loading-triggered feedback loop.

## Update Behavior

- Checks use the existing authenticated private release broker and device-bound download tickets. Checking for an available update automatically downloads, verifies and stages it inside private app support storage.
- There is no normal Finder/Downloads ZIP handoff. The user is offered Restart to Update or Later; prepared updates survive client restarts.
- The archive's hash, size, bundle ID, forward build/version, designated requirement and signed publisher are verified. Paths, entry count, expanded size, links and special files are checked before extraction.
- The helper preserves the complete signed app bundle, handshakes before GUI exit and never sends scheduler, result-adoption or Agent-lifecycle commands.
- Update termination uses the registered real SwiftUI app delegate, a shared one-shot guard and synchronous draft/reading-state flush. Cancellation restores ordinary quit behavior.
- After the GUI exits, the helper copies a candidate beside the destination, verifies it again, checks for competing GUI instances and exchanges bundles atomically using `RENAME_SWAP`. Unsupported locations/volumes do not fall back to delete-and-move.
- Post-swap verification or launch failures restore the previous bundle. LaunchServices is preferred; if it fails without creating a target instance, the verified target executable can be spawned directly. Successful process creation completes installation; later runtime crashes do not cause automatic data-incompatible downgrades.
- Private installation journals and the previous signed app are retained for diagnostics/recovery. No public update endpoint or new authentication access is introduced.

## Verification

- Swift client suite: 290 tests in 39 suites, plus protocol/state contracts and icon PNG/ICNS/native-rendering checks.
- Python core suite: 885 tests and 102 subtests.
- Isolated installed 0.1.68 wheel: 63 focused tests and 4 subtests, without source `PYTHONPATH`.
- Packaged math/syntax resource rendering and strict deep code signature checks.
- Native signed QA clients use a private home and a read-only fixture, not a new real Agent task. Build 402 is a fictional QA-only candidate and is never published.
- Native cases cover last-good quota during failure, sign-out invalidation, private automatic preparation, restored preparation, preserved unsent draft, actual 401-to-402 replacement/relaunch and rejection of a bad archive hash.
- An actual native LaunchServices error during testing restored build 401; this is retained as rollback evidence. The final successful update is recorded separately and must not be confused with that failed iteration.

## Release and Deployment

Release, production installation and scheduler activation are recorded only after their corresponding steps have actually completed. Local QA output is not evidence that an installed runtime has been activated.

Evidence directory: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/QuotaUpdater-20261003`.

### Actual Deployment, 2026-10-03 22:04 CST

- Private release broker publication and catalog verification completed for macOS 0.1.401 (401). The archive SHA-256 is `b4f95d4a7134215ce2d4288241962ffbc18094e4ee2cf5d0ab416283dda7e32b`; no fictional QA build 402 was published.
- `/Applications/Parallel Codex.app` was replaced atomically, strictly signature-verified and restarted. The installed executable matches the native-accepted release binary. Settings visibly reports `0.1.401 (401)` and that it is current; the running client PID is 21764.
- The real quota popover displays weekly remaining 88%, the reset date/time and the actual last-fetch time. A separate read-only service check also returned available limits with 12% used. See `production-401-version.png`, `production-401-quota.png` and `client-installation.json` in the evidence directory.
- Only the old GUI PID 80858 was terminated for the one-time bootstrap installation. The scheduler definition was unchanged; all 37 recorded Node/Codex/Python processes remained present, and active task `pcr-20261003T121808Z-1abdab98` remained running before and after installation.
- The 0.1.68 activation guard returned 75 because that task is still active. The actual scheduler is healthy and paired at 0.1.66, not 0.1.68. Therefore the new server-side disk cache and richer visual comparison are not yet active in production.
- The old follow-up ID `pcrd-0-1-66` no longer existed when an update was attempted. After checking local automation records for duplicates, a quiet thread heartbeat `pcrd-0-1-68` was created for the already-authorized idle-only activation. It supersedes the pending 0.1.67 activation, does not restart the client again and reports only actionable changes or failures.

### Activation Attempt and Verified Rollback, 2026-10-04 05:37 CST

- All live `active_runs` were empty, `active_operation` was null, and detached workers were supported. The activation script validated the archived client/runtime acceptance and wheel evidence, then rechecked idle state before changing the scheduler definition.
- The new scheduler reached healthy 0.1.68, but its required `codex.model_options` verification returned `pcr_error: Codex model options are temporarily unavailable. Please try again later.` The activation therefore failed acceptance and the script restored the original LaunchAgent definition and bootstrapped 0.1.66. Its immediate failure report had not yet observed the restored version; the independent subsequent check did.
- At `2026-10-03T21:36:56Z`, the actual scheduler is **0.1.66**, healthy, paired, and connected, with no active runs or active operation. All six baseline Node processes retain their original PID/start time, and the formal macOS client retains PID 95889 and installed version **0.1.405 (405)**. No client restart, real Agent lifecycle action, or research-directory modification was performed.
- A read-only model-options request also fails on the restored 0.1.66 service. This is evidence that catalog availability is not yet verified, not evidence of a specific Codex/network cause. A read-only `codex.account_usage` request returns `status=available`; this does not prove that 0.1.68's disk cache is active.
- To prevent repeated restart/rollback cycles, the activation helper now checks that the current model catalog actually contains GPT-6.1 Sol before touching the service definition. The amended helper was exercised and returned **75**, with `reason=model_catalog_unavailable`, `actual_version=0.1.66`, and `restarted=false`. Existing build, test, wheel, idle, detached-worker, and post-activation checks remain intact.
- **0.1.68 is still pending activation** and still supersedes the former pending 0.1.67 package. Neither persistent quota caching nor visual file comparison may be reported as enabled in production. The existing follow-up remains conditional and quiet while preflight cannot pass.
- Evidence: `deployment-0.1.68-failure.json`, `deployment-0.1.68-deferred.json`, and `deployment-0.1.68-rollback-verified.json` in the evidence directory.

### Successful Activation, 2026-10-04 05:41 CST

- On the next authorized follow-up, the current catalog preflight passed. The helper again verified the existing build/test/wheel evidence, detached workers, and absence of all active runs and active operations before switching the scheduler. It completed with exit code 0 and verified healthy **pcrd 0.1.68**. No test or safety check was bypassed.
- Independent post-activation queries at `2026-10-03T21:41:21Z` confirm version **0.1.68**, healthy state, `active_runs=[]`, no active operation, pairing retained, and relay link connected. `supports_visual_file_comparison=true`. A model-options query without a forced current-model parameter returns nine options, including **GPT-6.1 Sol**.
- The read-only quota endpoint returns `status=available`, with actual observation timestamp `1791063633.341572`. No quota percentage or reset has been inferred, and this is not a claim of a forced fresh refresh.
- All six Node processes from the earlier pre-deployment baseline retain their original PID/start time. The formal macOS client still runs as PID 95889 with installed version **0.1.405 (405)** and was not restarted. No Android device or real Agent lifecycle was operated.
- **0.1.68 is now active** and supersedes the former pending 0.1.67 activation, including its visual file comparison support. This successful attempt supersedes the earlier failed attempt's pending-production state; the failure/rollback artifacts remain intact as history.
- Evidence: `deployment-0.1.68.json` and `deployment-0.1.68-verified-20261004.json`. The completed heartbeat `pcrd-0-1-68` is paused after final verification, not used to trigger another client or scheduler restart.
