# macOS Composer Admission 0.1.412

Verified at 2026-10-04 23:50 CST. User authorized installation and a brief client
restart after background validation. Foreground QA, focus stealing, physical
input, Android access and interruption of real tasks remain prohibited.

## Root Cause And Evidence Boundary

This request concerns a draft that cannot enter submission, not merely the
presentation of a lost receipt. The screenshot does not persist the exact
transient flag at its capture time; that historical flag is not claimed proven.
Three independent counterexamples reproduce the faulty admission contract:

1. A completed, finalized, continuable Run and its Agent are already bound, but
   workspace content hydration disables sending and rejects durable capture.
2. The same cached conversation is readable, but opening/enrichment still owns
   a global loading flag. Content loading is mistaken for an unknown target.
3. Agent 6 is the explicit direct continuation source, while the daemon queue
   still has Agent 7. A held queue-selection request delays submission before
   persistence, although `turns.continue` explicitly validates its run/agent
   payload and does not use the queue selection as authorization.

The valid baseline tests failed with 11 assertions in three tests. Their fixed
versions pass. This is client state/command coupling, not evidence of a defect
in the SwiftUI framework itself. The actual button action and Return entry use
the same `canSubmitPrompt`; no ancestor disables the send control for passive
reading. Necessary admission facts remain published separately from metrics.

There is also a currently verified, distinct blocker: the original saved message
`0e535958-958f-427d-9a0f-04acd75e4ab7` still occupies the affected workspace's
outbox with `receipt_unresolved` / `receipt_lookup_unsupported`. Actual pcrd
0.1.69 cannot safely confirm that original command. Its prior preflight rejection
and permanent journal behavior are documented in
`2026-10-04-macos-submission-root-cause.md`. This release does not discard that
record, change its identity/source, or pretend that the message has recovered.

## Contract Repair

- The existing submission plan now governs button availability and submission
  admission. It depends on a coherent destination, not transcript, diff, model
  options, prompt history or reading-position completion.
- Navigation has a machine/workspace/conversation/run/request identity. A real
  unresolved A-to-B transition remains blocked; already bound cached content
  need not wait for enrichment. Only the owning request cleans up its state.
- Direct continuation captures the explicit source in the existing outbox,
  durably records it before clearing its draft, and does not await an unrelated
  queue-selection acknowledgement. Queue dispatch retains its own protocol.
- Known offline capabilities allow a resolved intent to be saved through the
  existing recovery path. Unknown computers are not assumed online or capable.
- Historical reading, discard operations, unavailable sources, unresolved
  navigation and existing submissions retain their safety gates and visible
  reasons. The same workspace's hidden outbox cannot silently swallow a draft.
- A durable accepted handoff prevents a second draft from branching off its old
  visible source. Committing the accepted Run invalidates obsolete source reads
  only after the current-window navigation guards pass. Another conversation
  cannot be hijacked; a later draft cannot be cleared by an older acceptance.
- No new message store, blind replay, timeout-based unlock or replacement
  navigation framework was introduced. Backend source/runtime were not changed.

## Validation

- 328 tests in 39 client suites passed in 187.283 seconds, using SDK 26.5,
  `nice -n 15`, one build job and the guarded non-foreground test script.
- New cases cover passive hydration, bound/unbound navigation, operation scope,
  explicit source capture, duplicate sends, offline known/unknown targets,
  queue rejection, hidden outbox, accepted-but-unopened handoff, late source
  reads and navigation cleanup. Existing delivery, cache, queue and rendering
  regressions remain included; outdated queue-await assertions were corrected
  to assert the real explicit command contract rather than bypass it.
- 13 installed pcrd 0.1.69 dispatch/continuation/queue tests passed; the imported
  runtime module was verified under that independent runtime, not repository
  `src`. They include adopted continuation-source and context-conflict checks.
- App icon, signed build, packaged math/syntax resource smoke and archive
  verification passed. Headless preparation used the built-in updater verifier.
- A backward-compatible optional installer request controls inactive relaunch.
  Both successful and rollback relaunches use that configuration. Tests retain
  ordinary user-initiated activation behavior and cover inactive configuration.
- No foreground UI smoke, Delivery QA, native control, screenshots, physical
  interaction or visual acceptance of the formal window was performed.

## Publication And Actual Installation

- Published and catalog-verified macOS 0.1.412 / build 412.
- Release: `macos-release-141756a224764b6c846df6c9d1ae7964`.
- Archive SHA-256: `ffdb7d8f497f54caa2344f233afaaaf5198f8ff4b9717580c18d0c442cff6b2c`.
- Installed binary SHA-256: `62f71816d485209665cad83b5305d571e224e322080add2558864934d3f3c857`.
- Downloaded through the authenticated update service and prepared with its
  built-in signature/integrity verifier, then installed by its signed updater
  helper after gracefully terminating only the authorized formal client.
- Actual client moved from 0.1.411, PID 17427, to 0.1.412, PID 28465.
- Installer reported installed=true, rolledBack=false, journal state=launched.
  The previous bundle was retained by the updater. No installation rollback was
  needed. `activateOnRelaunch=false`; frontmost PID remained 74259 before/after.
- Actual pcrd stayed healthy, paired 0.1.69, PID 9818, with the same service plist
  hash. GPT-6.1 Sol remained available. No scheduler restart was issued.
- Run `pcr-20261004T140156Z-3d27870f` remained active, active_operation=null.
  No stop, adoption, discard, rerun, resend or private task configuration edits
  were issued. No research files were changed.

The first long-lived Node baseline had eight exited processes by preparation
time, including six `cua_node` processes. The strict first preservation assertion
failed before client installation. Their exits were recorded, not hidden or
attributed without evidence; no Node lifecycle command was issued. That initial
baseline is retained. A fresh pre-install baseline was captured after confirming
the original client was still unchanged; all those Node process identities were
preserved across the actual client restart. This is not a claim that every Node
PID remained constant throughout all earlier background work.

## Backend And Original Message Still Pending

pcrd 0.1.70 remains independently staged, not active. Its sealed backend tests,
wheel/source integrity and prior receipt-recovery evidence were rechecked, not
relabelled as a new deployment. The user requires every candidate, synthesis,
finalization and other operation to finish before activation. The observed active
Run prevents activation now. The existing follow-up remains PAUSED; no permission
to resume it was inferred from approval to restart the client.

Therefore the client admission repair is installed, but recovery of the earlier
retained message is not closed. The new client keeps that original command and
draft safe; no backend-dependent recovery is claimed effective before 0.1.70 is
safely enabled and the original command is independently verified.

Evidence: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/ComposerAdmission-20261004/`.
Includes baseline red/green tests, final headless tests, dispatch tests, build,
resource check, catalog, downloaded/prepared metadata, initial and pre-install
process baselines, recorded pre-install Node changes, updater result and actual
post-install health/version/identity checks. Download credentials were not saved.
