# macOS 0.1.403: Expanded Work Process Indentation

## Scope

Expanded work-process children now have one 32-point layout inset. Directory, file,
command and thought rows share this indentation; the parent work-process heading,
intermediate assistant replies and final answer keep their original column.

The height estimate uses the reduced reading width, matching the native collection's
layout. This avoids clipping wrapped content or introducing an unnecessary inner
scroll range. Existing work-process grouping and scroll-edge chaining are unchanged.

No backend, Android, research files, Agent configuration or task lifecycle changes
are included in this client release. Existing conversation-deletion changes from
0.1.402 are retained without further modification.

## Verification

- The final client suite passed: 303 tests in 40 suites, plus protocol-contract and
  icon tests. New mounted AppKit geometry tests cover 320, 760, 1400 and 2400-point
  widths, light/dark appearance and all four text sizes.
- Wrapped-content tests compare native row height with the enclosing work group
  at 320, 760 and 1400-point widths. Short groups fit without internal scrolling.
- A signed independent 0.1.403 client used fictional completed records and a private
  HOME/socket. Two work groups were expanded separately in light and dark mode;
  their children are visibly indented while the reply between them is not.
- Collapsing and reopening the first group preserves the second group's expanded
  state. No real conversation was deleted and no Agent action was requested.
- Removing signatures from temporary executable copies confirms the QA and release
  code are identical. Both original bundles passed strict code-signature checks.
- Packaged math and syntax rendering passed.
- After quitting the QA client, a UI observation automatically relaunched that
  target with its default HOME. It was immediately quit without further actions;
  process checks confirm that only the formal client remains. The fixture was
  stopped. Native acceptance refers to the prior isolated fixture run.

## Publication

The macOS-only 0.1.403 archive is published and its broker catalog hash is verified.
The other component artifacts and their versions are unchanged.

Archive SHA-256: `889a3d480bcd68676afa624d8f6e3ad43fc2460ab61288f80e7e577e48bd71a8`.

Before the requested client restart, the installed client was 0.1.401, the healthy
paired scheduler was 0.1.66 and run `pcr-20261003T121808Z-1abdab98` was active.

## Installed Acceptance

- The built-in updater already held a prepared 0.1.402 package. Its current policy
  preserves that package when checking for a newer release. It was installed first;
  the relaunched client then automatically prepared 0.1.403. Both client-only
  restarts used the existing verified updater, without rewriting its private state.
- The final formal client is 0.1.403 (403), PID 43259. Settings visibly reports that
  version and that it is up to date. The installer reports success without rollback.
- The installed executable matches the accepted release executable, SHA-256
  `7f2e47fb820b4cfb4171915b472a5ab0b39fc94c4bee895472596e703bdaf663`, and passed
  strict signature verification.
- The existing selected conversation, selected synthesis Agent and queued message
  remain present. Expanding its real work process visibly indents the directory,
  thought and command rows beneath the heading. Settings was closed again.
- The scheduler remains healthy, paired and at 0.1.66. Its LaunchAgent definition
  hash is unchanged. The existing run is still active. No scheduler restart or
  Agent lifecycle operation was performed.
- Fifteen observed non-client process identities remain unchanged. Four previously
  observed processes exited during verification, including the verification helper;
  these are recorded separately rather than claiming every process stayed alive.
- Isolated fixture and QA client processes are stopped. Only one formal client is
  running. Publication and installation completed across the October 3/4 midnight.

## Evidence

`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/WorkIndent-20261003/`
contains the final test/build logs, resource smoke log, signed build, native
acceptance, screenshots, fixture action log, release records and client-update
verification.
