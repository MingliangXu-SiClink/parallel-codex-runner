# macOS Focused Transcript Update Hang

## Live Evidence

The reported frozen client was `/Applications/Parallel Codex.app`, version
`0.1.472`, PID `42651`, launched at `2026-10-24 01:40:08 +0800`.
Its loaded executable inode was `192274381`, matching the installed 472 bundle.
No production process was stopped during diagnosis.

Two five-second samples taken beginning at `15:36 +0800` independently captured
the same synchronous main-thread chain in all 423 and 420 sampled stacks:

```text
SwiftUI GraphHost.flushTransactions / AppKitWindowController.updateRootView
PlatformViewRepresentableAdaptor.updateViewProvider
ConversationTimelineView.Coordinator.update(parent:) [472 line 394]
ConversationTimelineView.Coordinator.replaceCollectionView [472 line 524]
NSView._setHidden -> nextValidKeyView
NSHostingView._recursiveGatherAllKeyViewCandidates -> responderNode
AttributeGraph.update_attribute -> UpdateStack.update -> print_cycle
```

The client used approximately 98% of one CPU core. A bounded read of its actual
application window through the native UI interface timed out. This is a busy
main-thread hang involving graph reentry, not evidence that the background
Agents stopped or that history/network I/O blocked the main thread.

The desktop-state writer was waiting in a SwiftUI UserDefaults observer's Update
lock. The samples do not establish a mutual lock dependency with the main thread;
this secondary wait must not be misreported as the root cause.

The application called `previous.isHidden = true` while still inside SwiftUI's
representable update. Hiding the focused native view invokes key-view discovery,
which reenters the hosting graph that is already being evaluated. The native
view replacement was originally intended to prevent obsolete drawing surfaces
from appearing in another workspace; deleting that isolation would reintroduce
a different defect.

[Apple's view hierarchy documentation](https://developer.apple.com/library/archive/documentation/Cocoa/Conceptual/CocoaViewsGuide/WorkingWithAViewHierarchy/WorkingWithAViewHierarchy.html)
documents the `nextValidKeyView` side effect of hiding the first responder.
The observed application-side call chain is direct evidence; the exact internal
AttributeGraph dependency cycle and the user's initiating action were not
reconstructed.

## Repair Boundary

- The representable update callback only schedules the newest input snapshot.
  It does not change the applied parent, rows, document, or focus hierarchy.
- A main-queue drain commits the entire existing native update after the
  synchronous representable callback has returned. It compares against the
  actually rendered snapshot, not an intermediate queued snapshot.
- A single pending snapshot supersedes older batch, interaction, and scrolling
  deferred input. Existing native batch identity/generation checks remain.
- Detach and newer direct updates revoke pending work. A generation check and
  attachment check accompany work-item cancellation.
- Initial unmounted document creation remains synchronous. Fresh drawing
  surfaces, message identities, cache ownership, question drafts, delivery,
  daemon/worker lifetime, and user stop policy are unchanged.

Main-queue scheduling is not a general promise that every SwiftUI transaction
has ended. This repair removes the synchronous application-side reentry present
in both live samples; native hosted tests exercise the repaired boundary.

## Verification

The previous synchronous update path failed two new boundary tests with seven
assertions: the focused document was hidden/replaced before returning, and
queued replacements could not be revoked. These are deterministic boundary
regressions, not a claim that a coordinator-only fixture reproduced the complete
SwiftUI infinite cycle.

The repaired implementation also uses a real hidden NSWindow, NSHostingView, and
production representable for 40 context/appearance transitions. It checks actual
first-responder ownership, replacement document identity, removal of old native
views, latest content, continued main-loop service, external composer focus, and
eventual layout quiescence. No foreground activation or synthetic desktop input
is used. Existing glyph/pixel, question editor, batch, and scroll regressions
remain part of the native gates.

Private evidence directory:
`~/Library/Caches/ParallelCodexMaintenance/Freeze-20261024/`.
It contains samples, bounded process and input fingerprints, source hashes, and
per-gate results. Production prompts and credentials are not copied into logs.

## Runtime Scope

The initial read-only observation independently confirmed local `pcrd 0.1.105`
capabilities and health, controller pairing, run enumeration, and idle service
follow-up queues. It recorded 12 original worker identities and 16 descendants.
Those are preservation baselines, not per-Agent remote-computation health claims.

At this audit's initial write, the old client remains running and unresponsive.
Client replacement, forced termination permission, formal publication, loaded
version verification, and actual UI recovery must be recorded separately below.
No daemon/worker restart or task resubmission is part of this repair.

## Release And Delivery

The final source snapshot passed all existing release gates: signing-store
management, unsigned-build checks, contracts, app icon, 697 native tests in 63
suites, and the additional focused execution of 104 tests in five suites. Five
new tests cover this repair. The focused tests overlap the native suite and are
not 104 additional unique tests. Signed build, strict/deep signature validation,
and immutable archive checks also passed on the same source hashes.

macOS `0.1.473` / build `473` was formally published at
`2026-10-24T08:03:09.582527Z` through the existing release service. Catalog readback
matched the exact archive, size `14972688`, SHA-256:

```text
d86d8736d27c615a21c66727d7b2e19a669818049eaae60a1fb8c8dd07e99e5a
```

The dedicated offline identity `Parallel Codex Release Signing` was reused.
No keychain/trust changes or signature verification bypass were made. App-source
changes relative to 472 are limited to Info.plist, ConversationTimelineView, and
TranscriptRenderingLifecycleTests. No daemon changes were built or installed.

The authenticated client update check failed with `IpcError` in 5.22 seconds.
This is recorded separately from the successful local health observation and
formal service publication; public/authenticated download is **not verified**.
The exact locally built, formally published archive was instead staged using
the unchanged native update verifier and the matching server catalog metadata.
Staging is not installation.

## Normal Exit Attempt

At `2026-10-24 16:13 +0800`, a single normal client-only quit attempt timed out
after 30 seconds. The existing native installer was cancelled and exited without
replacing the app. PID 42651 remains the original unresponsive 472 client. There
was no forced termination, task command, daemon restart, or program/data rollback.
Published 473 is prepared but has **not** been installed or loaded.

Immediately before the attempt, local health and read-only enumeration succeeded;
12 worker identities and 15 current descendants were recorded. All 16 service
follow-up queues were idle and empty. Persisted client outbox/handoff/deferred
queues were empty. The answer store contains 30 accepted answers and one draft;
its original identities and content fingerprints were retained. The original
baseline's `answer_draft_count` used a nonexistent `drafts` property and reported
zero; subsequent observations correctly count entries whose status is `draft`.
The baseline's full answer-store fingerprint itself was not affected by that
reporting error.

After cancelling installation, all persisted input fingerprints matched those
immediately before the normal quit attempt. No actual window/editor contents
could be read while the app was frozen, so unpersisted text is not claimed to be
preserved. Human authorization for a targeted forced client exit is pending.
Actual installed-client UI recovery and workspace-switch acceptance remain open;
the passing native tests and formal release do not stand in for that acceptance.

## Authorized Recovery And Actual UI Acceptance

The user subsequently authorized: "允许，仅处理卡死客户端". A third live
five-second sample of the unchanged PID 42651 at 16:16 again captured the same
focused-document / `nextValidKeyView` / AttributeGraph loop throughout all 3,848
main-thread samples. Fresh PID/start-time, loaded inode, published/staged program
bytes, persisted inputs, original workers, and idle service queues were checked
again under the maintenance lock before acting.

At `2026-10-24 16:18 +0800`, only that exact frozen client PID was terminated.
The unchanged signed native installer replaced the program and relaunched it
without foreground activation. Loaded version is **0.1.473**, PID **35655**,
executable inode **193073401**, executable SHA-256:

```text
fad964584556e7fa101f06893a6ec579161e04825cf5c1d816671fa7d05a5f7c
```

The actual loaded bytes match the signed, formally published bundle. The daemon,
all 12 pre-existing worker PID/start-time identities, and all three then-present
native Codex process identities survived unchanged. This is a client replacement,
not a recovery or resubmission of research tasks. No remote job was stopped or
reconfigured by this procedure; per-Agent remote computation was not audited as
part of this client-only acceptance.

The real installed application's main window became readable through native AX.
The original movement-behavior workspace was shown, then the memory-state
workspace was selected, then the original workspace was selected again. Both
navigation transitions completed, the conversation content rendered, and the
message editor remained present and focusable. No text was entered, answer
submitted, result adopted, or Agent control invoked. A subsequent AX read took
411 ms. Private screenshot: `installed-473-ui.png`.

After those interactions, CPU was 0.2% at observation, compared with approximately
99% while frozen. A further five-second sample showed a servicing/mostly idle
main run loop, with 3,408 of 3,628 main-thread samples waiting for a Mach event;
none contained the prior `print_cycle`, `replaceCollectionView`, or
`nextValidKeyView` hang chain. This verifies the current window recovered and the
tested navigation remains responsive, not a universal proof against future hangs.

Post-navigation fingerprints still match the pre-recovery prompt drafts, outbox,
accepted handoffs, deferred messages, local queues, service queues, and full answer
store, including its one draft. Unpersisted pre-hang text remains unverifiable as
disclosed before authorization. No user-data rollback was performed.

A later read-only `system.health` request hit its 10-second timeout while
capabilities, pairing, run listing, and queues still returned and the actual UI
remained responsive. This bounded timeout is retained in `final-acceptance.json`,
not converted into a successful health result or a new task failure. The separate
authenticated update-channel check remains unverified as documented above.
