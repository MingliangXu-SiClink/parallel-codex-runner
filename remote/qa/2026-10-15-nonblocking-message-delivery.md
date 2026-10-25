# macOS 435: Nonblocking Message Delivery

## Scope And Activation Boundary

The user requested the next macOS version, not a cosmetic removal of warnings.
Normal delivery, destination hydration and accepted-result handoffs must not own
the composer. Messages must remain durable, ordered and bound to their original
machine, workspace, conversation and branch.

This is a client-only release. No pcrd activation, client installation or restart,
real task submission/retry/finalization, research-file change, Node stop, QA
window presentation or focus change is authorized by this verification. The
previous deployment automation remains PAUSED.

Before work, the actual official client was 0.1.433, PID 19317, executable and
loaded inode 184179721. The paired daemon reported 0.1.86. Build 434 had been
published separately. See `live-before.json` in the evidence directory below.

## Root Causes And Changes

- A single workspace outbox or accepted marker disabled the entire composer.
  The client now durably queues later messages without changing the first
  command, its receipt lookup or its execution settings.
- Page readiness and message admission were the same gate. A queued intent now
  freezes its destination before any asynchronous read. Missing destination
  metadata is resolved in the background; a stale page cannot supply another
  workspace's Run, Agent or settings.
- A queued message may arrive while its predecessor is being promoted or
  settled. A persistent causal-chain identity and atomic append/promotion keep
  that message behind the right predecessor instead of forking an older turn.
  Intentional identical text remains distinct messages with distinct IDs.
- Queue promotion creates the durable outbox owner and removes the queued owner
  in one write, before any network mutation. Receipt settlement resolves its
  dependents and consumes the accepted marker atomically, after the validated
  Run is durably cached. Status writes compare exact owners instead of replacing
  unrelated workspace maps or resurrecting consumed receipts.
- Accepted-result reconciliation previously relied on a page/global refresh
  after a failed read or local write. It now has its own bounded-backoff
  background lifecycle and does not require another user action.
- Older continuation responses can contain a path-derived workspace projection
  while their top-level receipt identifies the registered workspace. The client
  rereads that exact Run with its registered `workspace_id`, then repeats full
  command, Run, original prompt, path, session and parent checks. It does not
  ignore a conflicting receipt or authorize an unrelated Run.
- Normal transport/handoff stages are no longer composer warnings, progress
  controls or header warnings. A local queued message is not called sent. Only
  an actual acceptance receipt changes that message to Sent. Genuine storage,
  authentication, identity and permanent rejection protections remain.

The queue is locally durable until its exact turn command is dispatched; it
continues while the app is open independently of the displayed page, and resumes
from disk after process restart. This does not claim an offline local message
has already reached pcrd or that a terminated app can perform network I/O.

## Invariants

1. Persist before clearing the submitted draft or issuing a mutation.
2. Keep frozen settings and original IDs; reconcile an uncertain original
   command instead of inventing another send.
3. Do not bind a new message to the old visible Run during navigation/handoff.
4. Do not overwrite a different workspace's draft, answer or Agent selection.
5. Returning a capacity request to the draft pauses dependent messages; approval
   redirects their dependency to the same original confirmed-command identity.
6. Stop pauses the deferred queue before a late source read can promote it.
7. Deletion has no undo/reinsertion record. A late read cannot recreate a deleted
   item; edits/reorders cannot change a command already durably promoted.
8. Preserve real capacity checks, workspace synchronization protection,
   explicit branch choice and receipt-store/replay-window safeguards.

## Verification

Evidence directory:
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/NonblockingDelivery-20261015`

- `red-focused-tests.log`: two behavioral tests fail on the prior behavior with
  six issues, including disabled admission and inability to preserve a second
  queued message. This is not just a string-presence test.
- `queue-final-focused-tests.log`: isolated socket tests exercise slow/fragmented
  acceptance, multiple ordered turns, repeated identical text, offscreen
  dispatch, restart receipt recovery, stale reads/deletion, another Agent,
  capacity confirmation, Stop, exact scoped rereads, atomic ownership and local
  storage failure. These behaviors also passed the frozen-source full gate below.
- `scoped-read-verification.json`: the running pcrd 0.1.86 accepted the new scoped
  `turns.get` request shape and returned the registered workspace identity for
  the inspected existing Run. This was read-only; no user turn was submitted.
- `native-composer.png`: unshown, non-key native window renders the editable
  composer and retained queue. No foreground QA window or simulated input.
- `client-final-tests.log`: 515 tests in 54 suites passed in 255.452 seconds,
  followed by the packaged icon checks. Generated protocol and desktop-state
  contract checks also passed. The final run used MacOSX26.5.sdk and the native
  build system; earlier SDK/plugin and Testing search-path invocation failures
  are not counted as passing verification.
- `strict-tests.log`: 67 tests in 7 suites passed in 34.419 seconds with
  `LIBDISPATCH_COOPERATIVE_POOL_STRICT=1`. This includes the delivery queue,
  accepted destinations, navigation, readiness and background refresh suites.
- The final regressions include reconnecting without a manual queue retry,
  append/promotion/settlement races, late status writes, and a slow local disk
  write racing identical drafts in different workspaces. Only the captured
  workspace's matching draft can be cleared after the durable write.
- `source-hashes-before-tests.txt`, `source-hashes-after-build.txt` and
  `source-hashes-after-tests.txt` matched. The final input manifest SHA-256 is
  `58db1b113c4fdbabc4ffd0855d9bbac70531feffec8ba5a26e4a71158c5e2e87`.
- Final full-test log SHA-256:
  `e151a2b5860f0aca81285cc9e115014f43284e5f06a0552e39e099fadbdd87da`.
  Final strict-test log SHA-256:
  `003ef2be146c8e4f9d9f701800c52111362c2ba100e763f25ac0d5a3e6b68ca7`.

The task's existing embedded-browser technical conversation was reused and an
anonymized architecture question was submitted once. The site returned
`无法加载此回复`; no readable external review or agreement is claimed. Local
reasoning and reproducible checks are the evidence for this release.

## Publication

Published macOS **0.1.435 / build 435** at 2026-10-15 08:38:41 UTC through the
existing Relay release path. Catalog readback reports release
`macos-release-e66528990af24a2c8a22e3da8014c004`, not revoked. The authenticated
native updater check also returns build 435 with the same archive SHA-256.
See `publication-complete.json`, `relay-catalog-after-publication.json` and
`live-after-publication.json`; this is more than a local build or staged ZIP.

- Immutable archive:
  `release/clients/macos/Parallel-Codex-macos-0.1.435.zip`.
- Archive bytes: 14,385,243. SHA-256:
  `a98a1c990e5ff101a385f16df6a7cb7c484c456379c799b3b20cc81440a94ca1`.
- Executable SHA-256:
  `0d04578e6db0e4ede77311ff67d5cb6f78cb22fed6c01e1aada976f85b2ed66b`.
- The built app and ZIP-extracted app passed strict/deep signature checks,
  version/build checks and bundled math/syntax rendering smoke checks. Signing
  authority remains `Parallel Codex Local Signing`, with the same designated
  requirement as build 434. The archive excludes Finder resource metadata.
- `release/manifest.json` was atomically updated for macOS only. Every other
  component and artifact is unchanged, including the staged pcrd 0.1.88 wheel
  with SHA-256
  `3b4233e08fd4facba85d7a65829379638f7f311445b71a229b602f287174f55e`.

## Live Preservation And Activation

`publication-preservation-check.json` compares the fresh snapshots immediately
before and after publication. The official running client remains **0.1.433**,
PID **19317**, executable/loaded inode **184179721**. The actual paired daemon
remains **0.1.86**. Client process identity and inode, the durable message
markers, and all 59 baseline Node process identities are unchanged; no baseline
Node is missing or restarted.

The existing deployment automation remains **PAUSED**. This release was not
installed or activated, and neither the client nor pcrd was restarted. No real
user task was submitted, replayed, stopped, adopted or discarded by this work.
The isolated socket and unshown native-render tests do not claim real-user
acceptance of an installed build 435. Published availability and actual running
version are deliberately separate results.
