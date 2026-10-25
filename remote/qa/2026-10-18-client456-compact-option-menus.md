# macOS 456: Compact Execution Menus

## Scope

Restore the accepted local-menu interaction, not the entire 445 application.
Clicking model, reasoning effort or Fast no longer inserts an editor beneath
the candidate/synthesis columns. The shared expanding editor and its navigation
state are removed. Each value instead owns a native AppKit pop-up menu; choosing
an item applies it directly through the existing context-validated settings path.

The SwiftUI labels remain responsible for complete model names, fonts and icons.
A transparent native control supplies menu tracking and accessibility without
enlarging that label's alignment rectangle. This avoids SwiftUI Menu's implicit
truncation of long custom labels. Equal catalog labels retain distinct wire IDs.
Retired controls cancel tracking and lose their target/action; stale selections
and newly locked synthesis cannot change another workspace, turn or stage.

Unchanged: native small-arrow count menus, immediate count selection, same-slot
Custom editing, the narrow 368-point window-bounded surface, lock/disabled states,
and the single-line candidate count/model plus synthesis count/model summary.
There is no lower options section, new settings dialog, confirmation step,
visible count range, omitted model name or new summary row. Existing 455
conversation and notification repairs are retained, not downgraded to 445.

Compared with the frozen 455 source record, only Info.plist, the execution
configuration implementation, its tests and the client contract script changed.
The other native source inputs are byte-identical. No backend, concurrency,
research-directory, adoption, lifecycle or updater-verification change is included.

## Verification

Private evidence:
`~/Library/Caches/ParallelCodexMaintenance/ExecutionMenus456-20261018/`.

- Final frozen-source run: 615 tests in 58 suites passed, 330.619 seconds.
- Strict cooperative-pool run: 47 tests in five suites passed, 9.195 seconds.
- Separate pagination isolation: 15 tests passed, 1.356 seconds.
- Native menu target/action tests exercise candidate and synthesis model,
  effort and Fast selections, persistence, independent values, hit testing,
  full titles, duplicate labels, locked synthesis and retired controls.
- The owning surface/frame stays unchanged through those selections. Adding
  100 choices does not enlarge its label. Count-menu/custom-entry, full-name,
  summary, zoom/theme/language, window-boundary, draft/marked-text, lifecycle,
  conversation and update-verification regressions pass.
- Native offscreen captures were inspected. No QA window, foreground activation
  or simulated global input was used. Menu actions were exercised programmatically;
  the images are not screenshots of an installed 456 application or opened OS menu.
- Protocol/state contracts, icon, signing-store and signing-disabled gates pass.
  All release gates match the same 225 frozen native input hashes.

A SwiftUI Menu prototype was rejected before publication because its native
label rendering introduced ellipses. An earlier full-test attempt was stopped
after focused testing identified a missing accessibility declaration; only its
verified isolated testing helper was signalled. That attempt is not a pass.
The final native control declares its accessible element and passes the complete
run above. Native control sizing is checked using AppKit alignment rectangles,
not the extra bounds used by the native cell's alignment insets.

## Publication

- Version/build: `0.1.456` / `456`.
- Existing offline publisher: `Parallel Codex Release Signing`.
- Strict deep signature, archive validation and packaged resource checks pass.
- Immutable archive: `release/clients/macos/Parallel-Codex-macos-0.1.456.zip`.
- Size: `14787946` bytes.
- SHA-256: `731a39655ad8b1dd99048a2f9e4b922acf59f649e5c074e65d7d964a63519941`.
- Server publication: `2026-10-18T11:18:25.286527Z`.
- Release ID: `macos-release-45b541ad03e44031b958c9b7320bdc95`.

The actual authenticated server catalog and local release manifest report 456.
The installed 455 controller is offered 456. Its ticketed download matches the
published hash/size, and its unchanged native verifier accepts preparation.
Every staged file hash and publisher matches the signed release bundle. No
ticket or private signing material was recorded. Older releases remain immutable.

## Live Boundary

This run did not install, quit, relaunch or activate the client. During observation
the installed/running client changed independently from 454/PID 42872 to
455/PID 75423; the latter's loaded inode matches the installed binary. This is
not installation of 456 and is not claimed as an unchanged client process.

pcrd remains 100/PID 55480, with unchanged LaunchAgent/runtime and receipt-store
identity. Seven common owners retain their process/start-time identities. One
departed owner was read back by its original Run ID: finalized, successful Agent 1,
no active worker, completion cause finalization. Two additional owners appeared;
the observation was not frozen or restored, and no Run was restarted or replayed.

Only actual open windows were inspected. A main window and the shared-model
Settings window appeared during the interval; the main store had one draft and
empty outbox, accepted-handoff, local queue and deferred-message maps. Final
observation found no open windows; the app was not reopened for inspection. No
legacy QA window stores or earlier copies of user state were restored.

Local health is OK. Separately, the existing Relay connection remains connected
since `2026-10-18T09:56:31.525461Z`, and protected authenticated update check and
download succeeded. No extra bridge consumer or manual token refresh was used.
No task, research-file, adoption, stop, recovery or message-submission action was
performed. These are release-continuity observations, not a competition compute audit.
