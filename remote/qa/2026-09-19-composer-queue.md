# Inline Composer Queue

## Scope

- Replaced the single queue-summary button with individual rows attached to the
  top of the composer, following the supplied visual reference.
- Each row has a one-line preview, edit and delete buttons, and a menu for moving
  the message earlier or later. Clicking the preview also opens the editor.
- Editing uses a separate native multiline popover and never takes over the main
  input draft. Normal messages do not show a non-actionable retry option.
- Three rows are visible at once; longer queues scroll independently with the
  existing native edge-fade behavior. The status and count remain visible.
- This iteration does not implement steering of a running Agent or change the
  queue's Agent-selection policy. No placeholder steering button is presented.

## State Handling

- Row actions identify messages by UUID, not by a captured array position.
- Read the current daemon queue before applying the individual edit, deletion,
  retry, or reorder. Abort on network failure or workspace/run changes.
- Reject edits if the original text changed or the item has already left the
  queue. Preserve other rows and newly added rows from the fetched snapshot.
- Lock dispatching rows and prevent reordering across them. Guard asynchronous
  rollback so a failed request cannot restore a queue into another workspace.
- Reuse the existing daemon replace/clear protocol. This is not an atomic
  per-item server mutation or a claim of eliminating all concurrent-client races
  between the read and replacement requests.

## Verification

- Final full macOS contract/client script: 137 tests in 20 suites passed in
  92.842 seconds. Five new tests cover queue mutations and native layout.
- Native layout tests cover narrow and wide viewports, every app text size,
  light/dark appearance, nonblank rendering, and 12 rows in a bounded viewport.
- Built the release app and verified its code signature. Reopened the ZIP and
  verified bundle version, signature, and absence of Finder archive metadata.
- Manually exercised the actual built macOS client under a separate QA bundle
  ID and HOME with a synthetic local IPC queue. The scheduler was disabled;
  launching/finalizing Agents was forbidden by the fixture.
- Verified direct preview/pencil editing, multiline save, cancel, moving a row
  earlier, deleting one row, and preserving the existing main draft throughout.
- Final-build edit was confirmed by `followups.get`: the updated multiline text
  was persisted in the isolated daemon store, not merely changed on screen.
- Verified an eight-message queue scrolls without growing the composer, the last
  row remains readable, and a 920-point-wide window with extra-large text keeps
  its controls visible while truncating previews. Checked dark appearance in the
  actual client; light appearance is covered by native rendering tests.
- Quit/relaunch retained the main input draft. The QA app and fixture were stopped
  after acceptance. No production queue messages were edited or consumed.

## Release And Activation

- macOS 0.1.374 (374).
- Relay release: `macos-release-2a331dbc82c54c8293f204bb3002ddf1`.
- ZIP SHA-256:
  `a64c41c1e74e0974c8b71fb0839957688eedeb4a377abe757019c19c18f89f66`.
- Archive size: 8,600,820 bytes.
- Authenticated app-update check returned build 374. Ticket issuance, private
  download, byte count, and checksum verification succeeded.
- Production client remains 0.1.372, PID 154, pending restart approval. pcrd
  remains PID 98927. Neither production client nor daemon was restarted.
- Android, pcrd, Relay, protocol, and Agent selection were not changed here.

Build, test, and isolated fixture records:
`~/Library/Caches/ParallelCodexMaintenance/ComposerQueue-20260919-U5pBjL/`.
