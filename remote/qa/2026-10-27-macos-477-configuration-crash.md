# macOS 0.1.477: Execution Configuration Crash

## Confirmed Cause

The two real 0.1.476 crash reports at 14:15:19 and 14:15:59 both terminate on
the main thread with SIGTRAP in `NSBundle.module` initialization, called by
`AppLanguage.localized` while constructing `ExecutionStageView`.

The app contains its resources in `Contents/Resources`. The generated SwiftPM
accessor instead probes the app root and an absolute build-machine path.
`localized` eagerly created `[.main, .module]`, so `.module` trapped before the
valid main-bundle localization could be used. The candidate count/model was
not the cause. Earlier rendering gates did not exercise localization.

## Repair and Regression

- Installed `.app` localization follows the existing file-preview resource
  convention: use the app's own resources and explicitly locate the packaged
  resource bundle. Missing app resources never fall through to `.module`.
- Non-app SwiftPM execution retains `.module`. Language lookup uses the
  bundle's actual localization identifier, including SwiftPM's `zh-hans`
  directory spelling, rather than assuming `zh-Hans` on every deployment.
- A synthetic app fixture verifies both present and absent localization;
  absent resources return the existing key fallback, not build-tree resources.
- The packaged rendering gate now executes real Chinese and English
  configuration localization, retaining the math/syntax/document-preview checks.
- Final native run: 704 tests in 63 suites passed. The first full run exposed
  the lower-case SwiftPM localization issue; it was fixed, not skipped.

Only `AppLanguage`, the rendering gate/test and the macOS version were changed
for this repair. No execution-configuration layout or interaction was redesigned.

## Publication, Installation and Actual UI

Final archive: `Parallel-Codex-macos-0.1.477.zip`, 14,981,502 bytes.
SHA-256: `8d56a7336829a848276744c4e60a95cda2cdc3cd85ac03c8860f313d97acadf4`.
The existing offline encrypted `Parallel Codex Release Signing` identity and
unchanged native strict signature verification were used. This remains the
internal signed channel, not an Apple Developer ID/notarized release.

The private frozen build tree was renamed so its embedded absolute path no
longer existed. A fresh process from the unpacked final signed archive, with
isolated HOME, then passed the resource/localization gate. The archive was not
repacked after acceptance. A preliminary private candidate was never published
and was removed after its replacement was verified.

Published through the existing Relay catalog at 2026-10-27T06:41:48Z:
`macos-release-b2db0dfc2762463aadc3208463372065`.
The existing paired controller downloaded the same hash, and the installed
0.1.476 native verifier accepted it. The already-crashed/closed client program
was atomically exchanged; the previous program remains in the normal Updates
directory. No running client was killed and no user data was rolled back.

Actual installed/loaded version: 0.1.477, PID 89462. The loaded executable inode
matches the installed file; executable SHA-256 is
`a8fba3e0fab27b982b05e71dda5237a026f622de80941081fe3941e89df58ff5`.
In a fresh normal client process, clicking the original configuration entry
displayed `configuration.popover`, both stages and their controls. Opening its
candidate-model menu also succeeded. No count/model was selected or changed.
When the user interacted with the app, further UI actions were stopped.

Before/after evidence preserves the pcrd identity/configuration, all seven
worker identities, original run/Agent states and event-log tail/size, follow-up
queues, original pending stop receipt, persisted settings/message drafts/outbox
and answer draft identities. pcrd remains 0.1.108; Relay was not restarted.

Private crash signatures, full native test log, frozen source patch,
publication/download/verification/install receipts and continuity snapshots:
`~/Library/Caches/ParallelCodexMaintenance/ConfigCrash-20261027-I7G0j6/`.
