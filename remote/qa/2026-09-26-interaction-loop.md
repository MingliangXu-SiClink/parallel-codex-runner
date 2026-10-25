# Conversation Interaction Loop

## Delivered Changes

1. The composer distinguishes sending, queueing, and stopping a turn. An empty
   live composer stops the whole turn and durably pauses its queue. A draft keeps
   a separate stop control. Stopping one candidate is explicitly an Agent action.
   The daemon invalidates stale queue claims before stopping; it refuses to stop
   a turn whose next queued message is already dispatching.
2. Only explicit progress/work phases enter work disclosures. Receiving a final
   answer no longer retroactively hides legacy messages that were already visible.
3. Running candidates expose live changes through immutable, scoped diff pages.
   A scan does not hold the run manager lock or overwrite final patch artifacts.
   Visible inspectors refresh every eight seconds. Full-file previews retain
   selection and scroll offset, including while syntax highlighting is replaced.
4. Long answer reflow restores a text-character anchor in addition to the row
   anchor. Overflowing work logs consume their own vertical wheel events at both
   limits instead of unexpectedly scrolling the containing answer.
5. Per-conversation drafts now retain cursor/selection. Removing a queue item
   offers undo. Queue editors survive dispatch/removal of their original row and
   retain unsaved text after a conflict. Compare-and-swap generations prevent a
   stale editor from replacing another client's queue changes.
6. File failures appear in the preview pane, retaining the last readable document
   and a retry tied to the originating file/Agent. Queue and run-operation errors
   stay in their own context. Background diff refresh failures keep existing data.

## Actual Verification

- Python source suite: 779 tests plus 58 subtests passed.
- Installed-wheel regression: 188 tests plus 11 subtests passed, including live
  diff pagination/expiry, concurrent status access, queue claims and CAS, IPC,
  and package metadata. New protocol fields are optional for older clients.
- macOS final contract/desktop/bootstrap checks passed; 193 Swift/AppKit tests in
  26 suites passed. The release app was built and signed, not only type-checked.
- Native walkthrough used the release binary in a separate app identity and HOME,
  with a real IPC/controller/queue store and deterministic candidate fixtures.
  No paid model request or production run was started by the fixture.
- Switched running candidates; opened the live file list and a complete 180-line
  highlighted Python patch beside result details. Appended lines while reading
  the middle; automatic refresh retained the file and document scroll offset.
- Submitted a queue item with Return, removed it, and undid removal. Server action
  logs show the same item ID restored, without a second enqueue. While the edit
  sheet was open, simulated dispatch; save failed without losing edited text.
- Used the composer stop: both fixture candidates stopped and the persisted queue
  became paused. Used the Agent menu: only the selected candidate received stop.
- Opened a valid Markdown file, then a missing file: the valid preview remained,
  with a local error and retry action rather than a conversation-wide error.
- Scrolled the work log to its end using the pointer inside its visible viewport;
  further wheel movement did not move the outer transcript. Its scroll position
  survived leaving and reopening the conversation.
- Found and fixed an additional lifecycle bug during native QA: a retiring editor
  could overwrite a saved selection with zero. A reproducing AppKit composer test
  first failed, then passed. Rebuilt and repeated conversation switching and app
  restart: the draft and selection at UTF-16 location 6, length 3 were retained.
- An isolated-client sample with an open inspector used about 135 MiB RSS; 237 of
  261 main-thread samples were waiting for events. This is a small-fixture sample,
  not an FPS benchmark or proof about very large production conversations.

## Release

- macOS 0.1.384 (384) published through the private Relay update channel and
  installed at `/Applications/Parallel Codex.app`. Strict signature validation
  passed; the installed executable exactly matches the tested release build.
- pcrd 0.1.59 wheel installed in the existing service Python environment.
- Relay and Android artifacts were not changed. Remote machines' pcrd services
  were not restarted by this release.
- Prior local pcrd PID 869 exited gracefully with code 0. Immediately before this
  restart, it reported no active runs. Web watcher PID 16091 was not stopped or
  replaced. An HTTP probe already timed out before the scheduler restart, so this
  release does not claim to have validated that unrelated web app's response.

### Activation Check

The replacement scheduler process initially blocked during Python initialization
in a directory-open system call, before pcrd created its IPC socket. macOS logs
recorded a Python Full Disk Access preflight denial at the same time. Requested
the user's help with any OS authorization prompt; no security setting was changed
and no alternate-identity workaround was used. Activation is not yet accepted.

Release ID: `macos-release-cf1f5fd70d9c46b3b6a9be65a9d42be1`

Archive SHA-256: `ccdbbacc0bed0ced708bf2e49fe8860201958c55acbfc42f66378502d8c2508e`

Wheel SHA-256: `e44a943ce94f4a8b51a38b17c913d2a876a058f8ac16f2f5a9aab709a522f134`

Logs, deterministic fixture, previous installed app, and build artifacts:
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/Interaction-20260926-ipN1ox/`

## Remaining Validation

Very large repositories, long-lived streaming sessions, real remote-host live
diffs, touchpad momentum on physical hardware, and Android UI changes are not
covered by this native macOS fixture walkthrough. No claim is made that these
six changes finish the broader ongoing product-improvement program.
