# macOS Work Process and Review Panels

## Delivery

- macOS 0.1.379 (379) published to the authenticated Relay update catalog.
- Release ID: `macos-release-59d2658d13fe4eac851ceb68718a0ae3`.
- Published at: `2026-09-19T17:18:09.827235+00:00`.
- Archive SHA-256: `13c194418945619de31cc6e3c338758a49cf8f5aa040e9bb926d14fe732e639f`.
- Archive size: 13,740,228 bytes. Local, uploaded, and stored catalog artifacts match.
- Signature: `Parallel Codex Local Signing`; extracted archive passed deep/strict verification. This is the existing internal signing identity, not Apple notarization.
- pcrd 0.1.54 wheel staged in `release/pcrd/`, SHA-256 `04b3583da26dfc71381182a9c015171ecc2a7830a6462fabca73807f42faad27`.
- Per user instruction, no production install or restart. `/Applications/Parallel Codex.app` remains 0.1.378. Production pcrd PID 82469 still has its original September 19 22:17:11 start time.
- Android 0.1.266 (267) reused unchanged. No physical Android device was operated.

## Implemented Behavior

1. Expanded work process is one bounded native scroll region. It has no surrounding border and fades only at overflowing edges. The final answer is outside the region. Search can reveal matches inside it.
2. The workbench keeps conversation, result details, and file review as separate native split columns. Opening a file does not replace results. Closing panels preserves the conversation editor, focus, selection, and usable layout. Narrow windows can scroll horizontally rather than clipping controls.
3. Native TextKit file/diff presentation uses file-type syntax highlighting in light and dark appearances. Added and removed lines remain distinct. pcrd stores full-context patches before candidate cleanup, alongside compact patches for existing consumers.
4. Quota labels retain actual provider fractions, with up to two decimal places. Integer input remains integer; missing/nonfinite values are not displayed as invented percentages.
5. Previous/next Agent and result-details controls are in the conversation's top-right toolbar. The inline Agent bar no longer duplicates them.

## Regression Fixes Found During Native QA

- Replaced the SwiftUI split layout that retained stale widths after closing a review panel, shifting the conversation outside the viewport.
- Preserved native split-item/view identity so a model update does not reset manually adjusted widths or recreate the editor.
- Lowered result/review holding priority so wide-to-narrow window resizing cannot leave the file close button outside the horizontal document extent.
- Invalidated collection delegate metrics before layout on width changes. The final QA session emitted no collection-item width warnings during disclosure and pane/window resizing.
- Opening a regular document clears the previous diff presentation; closing it no longer resurrects that diff.

## Automated Verification

- Final macOS test run: 165 tests in 24 suites, all passed (63.200 seconds), plus protocol-contract and desktop-state checks.
- Focused backend run: 424 tests and 4 subtests passed.
- Remote/release regression run: 136 tests and 11 subtests passed.
- Cleanup/full-context regression run: 23 passed, 82 deselected.
- Final built-wheel import and release/package tests: 18 passed. Verified imports came from the final 0.1.54 wheel, not editable source.
- Full-context tests cover retained patches after workspace cleanup, legacy upgrade only when original compact patches match, symlink rejection, and pre-cleanup upgrades without first opening the preview.
- Native split tests cover repeated close/open, editor identity/focus/selection, manual divider persistence, and wide-to-narrow content extent.
- Review tests cover nested search/grouping, finite/fractional quota values, light/dark syntax colors, and source lines that resemble diff metadata.
- The 1,000-operation layout microbenchmark reported warm-expansion P95 26.472 ms over 30 repetitions. This is not an end-to-end product performance comparison.

## Actual Native Client Walkthrough

Used an isolated native app (`com.parallelcodexrunner.reviewqa`) and actual isolated pcrd, with a synthetic two-Agent fixture. Each candidate contained 50 thought entries and 50 commands, a 301-line Python file, and a Swift source file. No paid Agent was started and no user workspace was modified.

- Bounded process scrolling, both edge fades, independent final-answer position, and persisted internal position.
- Cmd+F finds and reveals a thought inside the nested process region.
- Previous/next Agent controls and disabled end states; no duplicate inline controls.
- Results remain visible while Python/Swift review opens on their right.
- Full 301-line context returned by the actual QA daemon; syntax colors and additions/deletions inspected in dark and light appearances.
- File and diff switching, file closing, result-details closing, and reopening.
- Wide and half-screen windows; all trailing review controls reachable after narrowing.
- Final close-file then close-results walkthrough restores an unclipped conversation.
- QA processes were stopped after acceptance; production app, scheduler, Agents, and web services were not restarted.

## Limits and Activation

- Full-context preservation needs pcrd 0.1.54 to be installed and activated. That activation is intentionally deferred.
- Old history whose original source/candidate trees were already removed cannot gain missing context retroactively. Existing compact patches remain available rather than reconstructing from later user edits.
- Fractional quota formatting is tested with controlled inputs. No claim is made that the current live account supplies fractional precision.
- Final artifacts are described in `release/manifest.json`. The macOS catalog entry is published; the pcrd wheel is a prepared local deployment artifact, not a deployed service.

## Activation Follow-up

The user subsequently authorized restarting the client and scheduler.

- Rechecked both release SHA-256 digests and the extracted application signature before installation.
- Backed up the installed app and pcrd 0.1.53 package under `~/Library/Caches/ParallelCodexMaintenance/ActivateReview-20260920-3LYKst`.
- Gracefully quit the client while its visible result was already adopted. No adoption or workspace synchronization was initiated by the update.
- Preflight found no active Agent execution or run operation. Sent SIGTERM only to scheduler PID 82469, installed the verified 0.1.54 wheel into its existing interpreter, and started the same LaunchAgent without force-killing a process group.
- New scheduler PID 74470 reports healthy, connected to Relay, with no startup warnings. Existing worker PIDs 17597, 25298, 40803, 64588, and 66223 survived unchanged.
- Installed the exact published macOS 0.1.379 archive. Its final installed signature passed deep/strict verification.
- Launched the formal client and verified restoration of the same conversation, adopted candidate, result details, and sidebar divider. The new top-right Agent/results controls are present.
- Existing web service `http://127.0.0.1:9001/` returned HTTP 200 before and after activation.
- Current activated local versions: macOS 0.1.379 (379), pcrd 0.1.54. The new request about quiet, cache-first quota presentation is being clarified separately; it is not part of this activated build.
