# macOS 0.1.478: Explicit Model Selection

## Scope And Cause

The macOS model pickers unconditionally inserted an empty-string default option. A failed catalog read then left only that virtual option and the previously selected model. The UI also hid the catalog failure. This was separate from the 477 localization crash repair.

## Changes

- Candidate, synthesis, and default-conversation model menus contain concrete model IDs only; no default option or default badge.
- Existing explicit IDs remain selected even when discovery fails. Legacy empty settings use only their own context's resolved model, never another stage's selection or an invented model.
- New prompt submissions, follow-ups, and deferred messages freeze resolved model IDs before persistence. Original outboxes, accepted receipts, payload encoders, and retry identities remain unchanged.
- Unresolved new selections require a model; explicit selections do not require a successful catalog refresh. Effort/Fast automatic modes are unchanged.
- Catalog errors and a reload command appear inside the existing native menu, without expanding the configuration panel. Reload does not select a model.

## Verification And Delivery

- 711 native tests in 63 suites passed; protocol-contract checks passed. Regressions cover model IDs, legacy values, immutable request snapshots, independent stages, unavailable catalogs, native menu actions, and existing delivery/draft behavior.
- Final signed archive passed strict validation and packaged-resource smoke in a fresh process after its original build path was removed. The distributed archive is exactly that verified artifact.
- Version/build: `0.1.478` / `478`; archive SHA-256: `c1c599742a79e47ab964e1cf803b2e696f3ed755faba237d6b32a06d1c72763c`.
- Existing publisher: `Parallel Codex Release Signing`. Release: `macos-release-01f5519396c7469ab82c1d88fdab7f18`, published `2026-10-27T07:29:48Z`. Original authenticated download and native verifier both accepted the exact bytes. Non-macOS manifest entries were preserved.
- Initially cancelled installation when live typing was observed. After explicit user approval, used normal application Quit and the existing signed update helper with background relaunch; no forced termination.
- Installed PID `98281`, loaded executable inode `197908153`, matching installed 478 bytes. Native helper reports installed, no rollback; the previous program remains available for rollback without rolling back user data.
- Actual installed candidate and synthesis menus opened successfully. Both showed the explicit selected model and the real catalog error/reload items, with no virtual default. No model/count selection or real message was sent during QA.
- The captured 217-character composer draft matched its pre-restart SHA-256 at post-restart verification. Settings, outboxes, accepted handoffs, queues, question-answer store, seven original worker identities, pcrd identity/runtime, run state, and the original unresolved stop receipt were checked independently. User-stopped Agents 10/11 remained stopped.
- Removed only this task's frozen build tree, relocated smoke bundle, duplicate archives/helper bundles, and upload staging. Kept the formal archive, audit receipts, and the native updater's previous program; no user research or history was cleaned.

## Remaining Boundary

This was the boundary at the end of the 478 client-only update, not its final
functional resolution. The subsequent [pcrd 109 catalog recovery](2026-10-27-pcrd-109-model-catalog-recovery.md)
records the backend repair, actual API readback and restored choices in the
unchanged installed 478 client. It supersedes the unavailable-catalog status
below without changing the historical scope of this client release.

PCR's `codex.model_options` service still reported discovery unavailable in this incident. A separate fresh, read-only native `model/list` probe succeeded; that does not establish recovery of the service path or its root cause. The new menu now reports this honestly. Complete catalog recovery is **not** claimed. No other CLI's shared catalog was substituted, and no pcrd, Relay, worker, account, or authentication state was restarted or modified for this client update.

Private build, tests, publication/download/installation receipts and safety comparisons: `~/Library/Caches/ParallelCodexMaintenance/ModelPicker-20261027-4oNytc/`.
