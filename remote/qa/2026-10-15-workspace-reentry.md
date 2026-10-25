# macOS 434: coherent workspace reentry

## Observed state and scope

The user reported the composer still showing "Opening the destination
conversation; your draft is retained" after leaving and reentering a workspace.
Read-only inspection confirms the official **loaded client is 0.1.433**, PID
19317, inode 184179721. This is not explained by an uninstalled 433 release.
The actual daemon remains 0.1.86, paired; the deployment automation remains PAUSED.
The original accepted handoff has settled and the outbox/handoff dictionaries
are empty. No real message needs to be resent.

The existing window was already showing a complete answer and an enabled
continuation when inspected. The report is therefore not treated as proof that
every workspace is permanently stuck. The failure is reproduced independently
through the complete reentry path below, without navigating the live window.

Source scope is the macOS client. No pcrd, Android, research workspace, private
Agent configuration, live desktop state, queue or real task is modified. No
client or service restart is authorized/performed by this verification.

## Root causes

The opening warning reflects an unresolved *submission destination*, not the
accepted-message receipt fixed in 433. Remaining reentry paths still coupled
destination binding to unrelated reading/discovery work:

- When a body falls out of the bounded 12-record memory cache, entry forgets its
  destination even though the exact machine/workspace/latest-Run metadata remains
  known. It waits for global refresh and index discovery before restoring it.
- Disk restoration scans workspace cache contents instead of first reading the
  exact selected Run. The live cache includes a 158 MiB historical record; it
  should not be a prerequisite for opening an unrelated small latest answer.
- A cached native-session answer can be installed without binding that session
  into the composer settings. Reading is ready while sending still references
  a different session until later discovery finishes.
- An index can select another conversation before replacing the previous Run,
  exposing an incoherent destination. Proven conversation-alias repair can also
  leave its navigation target using the old alias until slow enrichment ends.
- Resetting a workspace clears the navigation target without advancing its
  request ID. A delayed old navigation can become eligible again after returning
  to the same workspace/conversation.

Read-only production metadata capture for two affected research workspaces found
`runs.list` taking **8.248 s** and **9.202 s** (20/16 runs, both complete pages).
Native-session discovery took 1.139/1.404 s separately. These measurements explain
why depending on those reads is visible, not an assertion that the supplied
screenshot itself was timed. The user-paused backend finalization optimization
is not resumed; this repair removes discovery from the client's known-destination
and cached-answer path instead.

## Repair

- Reuse only metadata under the exact machine/workspace/latest-Run key, checking
  both Run ID and workspace path, when a body is evicted. This binds the already
  known destination without fabricating a complete answer or accepting unknown
  identities.
- Restore the exact selected cached Run before global health/index revalidation.
  Warm native history binds its own session immediately. Other historical index
  pages continue to enrich the selected view rather than authorizing it.
- Commit an index-selected conversation and its cached/indexed Run together.
  Repair a navigation alias only when the index proves the same target Run or
  session. Real unknown/cross-workspace destinations remain blocked.
- Advance the navigation request ID on workspace reset. Old reads cannot become
  current merely because the user comes back to the same workspace.
- Do not persist an unhydrated, empty indexed projection over a readable answer.
  Context loading also hydrates a metadata-only current Run, not only a Run whose
  ID differs from the destination.

The warning text and destination validation are not removed. The repair changes
the underlying binding/reading transitions, not their error presentation. It does
not grant sending permission merely because arbitrary cached text is visible.

## Verification Design

Evidence directory:
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/WorkspaceReentry-20261015`

- `red-tests.log` reproduces seven failing tests on the published 433 source,
  including missing scoped destination, wrong native-session binding, incoherent
  index selection, stale alias, unchanged navigation epoch and actual absent
  native answer after body eviction. The private metadata replay also fails on
  both captured real destinations. Raw private diagnostics stay outside the repo.
- `green-navigation-tests.log` passes the same root cases and negative wrong-Run
  or wrong-workspace metadata cases. The later delayed-navigation regression
  holds cache I/O, leaves/reenters the exact workspace, then releases the old
  historical-turn read and checks the new answer and draft remain untouched.
- The memory-eviction regression uses the real structured disk cache, expires
  the memory entries and holds health requests. It asserts the exact current
  answer actually renders offscreen and the continuation request body still names
  that Run and chosen Agent. No prompt is submitted to obtain this evidence.
- `native-reentry.png` contains the saved history, correct question and complete
  Agent 8 answer while health is held. The window remains unshown and non-key;
  the image is visually inspected, not treated as a status-label-only check.
- Both captured production workspaces are replayed offline under the same
  destination/command identity; no live task is restored, adopted or restarted.

The anonymized follow-up in the existing embedded-browser technical discussion
again returned "unable to load this response". No independent answer or agreement
is claimed. Source changes and measured behavior are verified locally.

Release review additionally validates machine, workspace, Run ID, conversation
membership and native-session key before installing a cached projection. Invalid
cached metadata cannot displace the valid indexed destination or expose another
workspace's answer. This extra negative regression is part of the final suite,
not a relaxation of the submission gate.

The initial full run (`client-full-tests.log`) passed 494 tests. The final run
includes that additional cached-identity guard/regression:

- `client-final-tests.log`: **495 tests in 53 suites passed**, 238.189 seconds;
  standalone protocol/desktop-state contracts and all icon checks also passed.
- `strict-tests.log`: **49 tests in six suites passed** under
  `LIBDISPATCH_COOPERATIVE_POOL_STRICT=1`, including the complete workspace
  navigation suite and the private production metadata replay.
- The cold memory-eviction/held-health regression passed in 97 ms, including its
  native answer and exact continuation-source checks. The warm fixture's 50
  workspace/400 complete-answer switches had p95 0.939 ms and maximum 1.337 ms;
  slowest native appearance across 40 branches was 34.722 ms. The existing
  100 ms warm-native assertion was not relaxed. These are controlled fixture
  observations, not a promise about arbitrary cold data or system load.

Final full-suite, strict-pool, build/signature, immutable archive and real update
catalog results are appended only after those steps complete. Installation and
publication remain separate claims.

## Verified publication

- Source/test/resource/script, branding, package and Info.plist hashes match before
  final tests, after tests and after the release build. Sorted hash-manifest
  SHA-256: `30cfb9509a3c8cf8fa96b77f2cadddebb1182b35e4e164c4cd2e973f7fbcac5f`.
  Generated conversation protocol checks passed.
- Built and extracted applications identify **0.1.434/build 434**, pass deep/strict
  signature validation and packaged math/syntax rendering. The authority and
  designated certificate requirement match the existing published release.
- Immutable archive: `release/clients/macos/Parallel-Codex-macos-0.1.434.zip`,
  14,276,736 bytes. SHA-256:
  `22290540a1410b610b33ee57572e1bfcc470b15efea11766db4e698df1447721`.
  Signed executable SHA-256:
  `96f41db82df1197ad819be07fed0417e16985e270d5fe9e3ddc10dab0b703917`.
- Actual Relay publication completed **2026-10-15 06:28:20 UTC** as
  `macos-release-742c26b46cbd42c897ed67c2f8146566`. The admin catalog and existing
  authenticated client update-check endpoint both confirm non-revoked build 434
  with the exact archive hash. This is a real update-feed publication, not only
  creation of a local package. No download ticket or installation is requested.
- The local manifest was updated atomically for macOS only. Manifest SHA-256:
  `f79cd59951f5a11b0ccaf998b45ede912084d91984069357bebbbecd7da27c70`.
  All non-macOS components/artifacts remain unchanged and hash-verified.

Before/after publication observations retain official **0.1.433**, PID **19317**,
installed/loaded executable inode **184179721**, paired daemon **0.1.86**, an empty
outbox/handoff set and the deployment automation **PAUSED**. All **30** baseline
Node processes retain their original PIDs/start times. No Agent, Node, daemon or
official client is stopped/restarted, and no real task is submitted or replayed.

**434 is published, not installed or running.** The new behavior cannot be claimed
inside the unchanged 433 process; its activation requires an authorized client
update. The existing paused backend/client deployment is not resumed.
