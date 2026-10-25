# macOS Client Exit and Signed Updates, 0.1.447

## Confirmed Distribution Gap

The installed local validation client was 0.1.446, but the production Relay catalog's newest macOS release was still **0.1.438 / build 438**, published at `2026-10-16T08:21:00.697869+00:00`. The existing authenticated `controller.http` software-update check for the real installed build 446 returned `available = false`. This was not a cached HTTP 502 or a newer server package being ignored: versions 439-446 had only been archived in the separate local-validation directory.

The server's signed publisher is **Parallel Codex Local Signing**, certificate SHA-1 `7CF98A08B97B137A2952B8F39C71531B62F6403F`. The installed 446 bundle has a linker/ad-hoc code requirement, not that publisher identity. The updater correctly requires a matching publisher; publishing an unsigned package or weakening this check would not be a valid repair.

The user explicitly authorized the existing certificate for this next release and migration of the local validation installation back to the same signed channel. No new certificate, trust configuration or updater-verification exception is authorized. The signing store is tool-managed: the user must not be expected to know its generated password or resolve an internal credential failure through a system dialog. Business-data Keychain access is not part of this task.

## Client Lifecycle Contract

- Closing a window, quitting and restarting to update persist the viewer, not an adoption decision.
- A completed, visible Agent stays unadopted unless a separate authorized result/continuation operation adopts it. Closing does not synchronize results, stop other Agents, discard candidates or remove their workspaces.
- Drafts, navigation, cached candidates and reading position survive closing/reopening. Other candidates remain selectable and comparable.
- Merely selecting, viewing or closing is not adoption confirmation. Already authorized background tasks and queues retain their own lifecycles; this does not change the terminal UI's explicit `/exit` behavior.
- Existing durable-answer safeguards remain: an unsaved native answer can still prevent termination rather than be lost.

### Correction to Earlier Maintenance Warnings

The inspected 446 `ConversationContextTransitionPlan` already returns `proceed` for application exit. Earlier notes describing automatic adoption on every ordinary exit were too strong. The delegate nevertheless retained selected-Agent/Sync-Back/finalization guards, entered that business transition asynchronously and returned `terminateLater`. This release removes that obsolete dependency entirely rather than relying on its lower-level exception.

The regression also found a real persistence defect: an empty window-model enumerator produced an empty array, so the null-coalescing fallback skipped the retained last-window model. The final flush now includes that model exactly once, even when no registered window remains.

## Implementation and Verification

The app delegate now checks only durable answers, commits native editor text, flushes saved viewer state and returns `terminateNow`. The obsolete updating/termination-in-progress flags and updater-specific adoption bypass are removed. Window closing retains its existing persistence-only path. The build-time source contract forbids reintroducing selected-Agent, Sync-Back, adoption or asynchronous result resolution into termination.

- Full release gate: **585 noninteractive native tests in 57 suites passed**, plus protocol and canonical-icon checks.
- Strict cooperative-pool gate: **73 tests in 5 suites passed**, including lifecycle, updater, native answers, conversation navigation and workspace readiness.
- New termination regression covers Sync Back both on and off, a selected completed unadopted Agent, two other candidates including a still-running Agent, no adoption/mutation, immediate termination and full draft persistence.
- Real offscreen native window closing/reopening retains all three candidate snapshots, the selected Agent and draft, then successfully selects a different completed candidate without adoption. No production prompt is used as a test.
- Fail-first evidence: after correcting two fixture-only compilation/decoding errors, the old implementation failed four assertions. Removing the lifecycle dependency exposed the retained-model flush problem; both behaviors pass after the focused repair.
- The initial full preflight rejected the existing draft-flush source matcher because the necessary model-deduplication lines exceeded its 240-character bound. The matcher still requires native commit before draft persistence, with a 400-character bound; behavioral draft tests remain mandatory. The failed preflight is retained privately, not counted as a passing release gate.
- Existing isolated Relay macOS-update tests: **2 passed**, including authenticated one-use download and authorization boundaries. No server application code, database schema or service is upgraded for publication.
- Optimized build, isolated process/resource smoke and no-signing build gate passed on the same frozen input set. Signing and final signed archive checks are separate gates, not implied by the unsigned build gate.

Private evidence: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/ExitUpdates-20261017/`. Native test windows are offscreen/non-key. The embedded-browser collaboration tools were unavailable; no alternate browser or unverified model consultation was substituted for primary code, catalog and authenticated API evidence.

## Release Status

### 2026-10-18 Authorized Publisher Migration

The user subsequently explicitly answered **"允许新建专用签名身份并迁移"**. That supersedes the earlier existing-certificate-only restriction, but does not authorize changing the login Keychain, system trust, or updater validation.

The new identity is **Parallel Codex Release Signing**, certificate SHA-1 `5ED83FED0C4215E97CC203DE5EE86D3957600D78`. The original signing store is preserved. Signing now uses an isolated encrypted PKCS8 store in `Signing/Release`, with a tool-generated owner-only credential. The private key is AES-256-CBC encrypted with 100,000 PBKDF2 iterations and decrypted only through a pipe for routine signing. It is not copied into application bundles, release archives or evidence logs.

The initial new-keychain setup exposed OpenSSL 4 import-format compatibility and native identity-selection trust requirements. The same newly generated key/certificate was retained; no replacement key or system trust override was created to work around either failure. The redundant new keychain, failed private P12 containers and plaintext creation-only material were removed after encrypted-store and native signature verification. No old keychain or unrelated item was deleted.

The final backend is upstream `rcodesign` **0.29.0**, downloaded from its official GitHub release, checked against the published SHA-256, and kept only as a private build dependency with its license. It signs offline with explicit publisher requirements and hardened-runtime flags, ignoring ambient configuration. The native **`codesign --verify --deep --strict`** check remains mandatory. An isolated, encrypted-key canary passed that unchanged native check and satisfied its exact certificate-pinned designated requirement without any system trust change. This does not claim Apple Developer ID/notarization or automatic migration for old-publisher clients.

The build now checks the managed signing store before compilation. Invalid credentials, unsafe permissions or mismatched key/certificate fail closed without a dialog or publisher regeneration. Explicitly disabled signing still avoids all signing state/tools. Isolated regression tests cover creation opt-in, reuse, invalid stored credentials, restrictive file permissions, symbolic links, encrypted-at-rest keys and unchanged signing materials. Final release-gate and deployment results are recorded below after their respective checks complete.

Primary tool references: [Apple certificate selection and trust requirements](https://developer.apple.com/documentation/technotes/tn3161-inside-code-signing-certificates), [upstream offline signing documentation](https://gregoryszorc.com/docs/apple-codesign/stable/apple_codesign_rcodesign_signing.html), [official tool release and checksums](https://github.com/indygreg/apple-platform-rs/releases/tag/apple-codesign%2F0.29.0).

### Final Results, 2026-10-18

All release gates were rerun on the final **537 frozen inputs** after the signing-tool changes: **585 native tests in 57 suites** passed (330.391 seconds), **73 strict tests in 5 suites** passed (14.620 seconds), both encrypted-store tests passed, the disabled-signing gate passed, and both isolated server-update tests passed. The optimized unsigned build, real default offline signed build, isolated signed process/resource smoke and final unpacked ZIP version/bytes/modes/deep-strict-signature/resource checks all passed. Signing changed only executable/signature bytes.

- **Packaged and signed:** `release/clients/macos/Parallel-Codex-macos-0.1.447.zip`, **14,783,721 bytes**, SHA-256 `3ba7eb2b9083d55ef9753d4055aa5c93d106e88f6282479de01a09d6c7d788d0`. No Finder metadata or private signing files are in the archive.
- **Server published:** `macos-release-c334a99dda0946749bdc38307ad47e7b`, at **00:40:00 Asia/Shanghai**. Readback agrees on build 447, publisher, requirement, size and SHA-256. No server/pcrd restart or schema/configuration update was needed.
- **Authenticated delivery verified:** the actual installed build 446 changed from `available=false` before publication to `available=true` for 447. Its existing controller downloaded the real archive using the official one-use ticket; bytes/hash match the signed package. No authentication token or ticket was persisted in evidence.
- **Actual client verifier verified:** the source verifier implementation is byte-for-byte unchanged in the isolated probe. It accepted that authenticated package for an explicitly isolated, re-signed same-publisher lower-build fixture, and rejected the real unsigned 446 host and verified old-publisher 438 bundle. Fixture apps were never installed or launched. The fixture is not presented as a real old-client migration or a future release.
- **Installed and loaded:** normal client-only exit/relaunch completed at **00:42:42**, client **PID 50490 -> 57024**. The loaded main executable is inode **186914152**, SHA-256 `815fac978914c4e09109df3ab5129aeefe0ff0e5aa21e05a3071a97105a249a0`; complete installed bytes match the unpacked signed release. Rollback protection retains only the previous program bundle, never an older user-data/outbox snapshot.
- **Real UI and state preserved:** native post-check at **00:43:35** confirmed the same Saved conversation/Agent, exact native/persisted input hash, all draft/settings/queue/answer maps and original receipt identity. The current input was empty; no previously sent/cleared historical draft was resurrected. The external summary remains 207.5 by 28 points and trailing blank space is not a button. No modal or extra unchecked real window was left; foreground application was unchanged.
- **Tasks and owner identities preserved:** pcrd **0.1.98 / PID 48685**, its LaunchAgent bytes, all **4 persistent owner** identities/start times, receipts and protected descendants remain unchanged. There were **no active native Agent runs at this boundary**, so this is not claimed as a live-computation continuation test. No prompt, adoption, stop or retry action was sent by the installer.
- **Post-install update check:** the real installed build 447 correctly returns `available=false` against the now-current server build 447. This is genuinely current, unlike the earlier 446-versus-438 distribution gap. The canceled hourly PCR automation remains absent.

Migration evidence is private under `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/ExitUpdates-20261018/`: frozen gate reports/logs, `new-signing-identity.json`, signed/unpacked manifests, `server-publication.json`, authenticated check/download/verification reports, native before/after observations, `installation.json` and `post-install-verification.json`. Public `release/manifest.json` updates only the macOS component/artifact; Android, pcrd and Relay component metadata are unchanged. This completes source repair, testing, signing, server publication, authenticated delivery, explicit publisher migration and actual installed-version verification as separate checked steps.

### Earlier Existing-Store Block (Historical)

Source and unsigned build gates are complete. **Signing, server publication and installation are not complete.** At `2026-10-17T15:50:32Z`, the installed client is still 0.1.446, the actual server catalog is still build 438 with 234 records, and the enabled pcrd 0.1.98 remains PID 48685 with its original start time. No 447 signed archive or publication-attempt marker exists. The same 536 frozen source inputs still pass all recorded source and unsigned-build gates.

### Managed Signing-Store Failure

The first direct signing invocation omitted the dedicated-keychain unlock/selection preflight and opened SecurityAgent. It ultimately returned `errSecInternalComponent`; that is a failed attempt, not a signed release. Its log and the staging-only changed signature resource were preserved. The untouched tested bundle remains the source of any subsequent signing attempt.

After the user correctly rejected being asked for an internal signing password, the release helper was changed to read the existing private credential in memory, unlock only the dedicated signing keychain, and select that keychain explicitly for codesign. The credential is not included in process arguments, logs, reports or a new file. No new certificate, password reset, trust, search-list or key-access-policy change is performed.

Both the existing credential with the build script's newline normalization and its exact saved bytes returned `errSecAuthFailed` (`-25293`). The native `security` tool independently rejected the same normalized saved credential. The original signing identity is present in that dedicated store; no matching usable identity was found in the login-keychain identity listing. This establishes that the saved signing configuration is not currently usable, not when or why it became inconsistent. No user password was guessed and no unrelated saved items were queried.

The corrected preflight stops on that failure before any codesign or production replacement. There is no SecurityAgent process at the final readback. Further automatic signing retries are stopped. Continuing with the original publisher requires a usable original signing store/credential; a replacement signing identity and one-time application migration require separate explicit authorization. Old-publisher clients cannot silently cross that publisher boundary. The user was asked only for that additional scope decision, not for an internal password.

Private failure/readback evidence: `managed-signing-block.json`, `release-sign.log`, `release-sign.json`, and `failed-sign-attempt-1.json` under the evidence directory above. Until the final signed archive, server publication, authenticated download and local installed bytes have been verified, neither server availability nor installed recovery is claimed.

No task, adoption setting, research file, pcrd process, Windows/WSL guard, Android artifact or periodic automation has been changed. The canceled hourly PCR automation is not recreated.
