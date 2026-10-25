# macOS 0.1.467: Recent Commands in Long Agent Conversations

## Confirmed Cause

The compression conversation did have native command execution. This was not
evidence that its Agent had stopped working, nor were all command records lost.
The running client was 465; the composer-only 466 release was not installed.

Targeted evidence for the original compression run and physical Agent 8 showed:

- Cached full-work cursor **95,630**, with **1,094 command blocks** and 1,156
  structured operation blocks; independent reply cursor **236,191**, with 388
  output items. These cursors are positions in the shared global event stream,
  not a count of missing commands belonging to this Agent.
- A full-work page at 95,630 contained seven command items. A bounded second
  look 215 seconds later found the work cursor at 101,243, an advance of 5,613.
  Backfill was slow and behind, not frozen.
- Native event 235,033 was Agent 8's original `item.started` /
  `command_execution`. Its item ID matched the normalized operation identity;
  subsequent bounded source records included completion. Metadata and hashes
  were retained without saving command arguments or research content.

The client intentionally loads replies through a fast output-only cursor.
However, its formatter appended those newer replies after the much older full
work prefix. Until full work caught up, that tail was therefore a long sequence
of progress text without the corresponding command disclosures. Collapsing or
scrolling the existing work section could not recover records not yet loaded.

## Repair

- Keep original continuous full-history backfill. Add a distinct recent-work
  range through the existing read-only `runs.agent` API when full history trails
  by more than 2,048 global positions and a reply page has already arrived.
- Record the actual start, cursor, has-more state and typed work items. Accept
  command or structured operation records, not arbitrary image fragments or a
  second copy of the output lane. Recent data never advances the full-history
  cursor or its event-ID set and cannot pretend a missing interval is complete.
- Bound each recent-reader burst to two pages and its initial head. Accept the
  whole returned page and its real next cursor, including valid overshoot; do
  not truncate at the head and silently skip returned records.
- Use the existing two live-content I/O slots after replies are primed; full
  work retains its independent two slots. Total I/O concurrency is unchanged.
  Each page returns to the bounded FIFO transport queue; no reply waits for a
  recent-reader task group or for the full historical prefix.
- Merge supplemental replies and recent work by original event ordinal before
  the existing operation reducer and disclosure grouping. Keep one stable
  presentation identity when completion is seen before its earlier start;
  source ordering and the existing reducer prevent a late running snapshot
  from overwriting a known terminal state.
- Persist the separate recent range and retain it through old foreground
  snapshots, restart and reconnect. Existing cache budgets evict whole records,
  containing full and recent streams together, never just full blocks while
  leaving their event-ID set. Recent supplements remain until their parent
  cache record is evicted; display deduplication is not memory reclamation.
- Manual full-work rebuild is a distinct partial-reset case. Immediately before
  its atomic model replacement, reconcile recent operations with the freshest
  visible full-work facts and the rebuilt prefix using the same reducer. This
  prevents an old retained start from resurfacing as running after a completed
  operation's full page is deliberately reloaded. Do not use request-start
  state if a full reader completed the call while the manual read was pending.
  Deduplicate such a retained fact by its actual operation event ordinal, not
  only by its older envelope ID, while an earlier start is being reloaded.

The change is confined to five client source files, two test files and the
version plist. Composer/menu styling from 466 is retained. No pcrd, native
executor, task delivery, answer, queue, adoption or notification code changes.

## Evidence and Limits

Isolated loopback coverage models two workspaces, 16 branches and a 240,000-event
source. Both full-work slots are held while independent replies and recent
completed commands become visible in real, never-ordered AppKit collection
views. Releasing backfill retains one operation, the same disclosure identity
and the readable reply. Further tests cover empty-work-page budgets, durable
coverage, old snapshots, late/duplicate/cross-Agent responses, completion before
start, media-fragment exclusion and terminal facts retained during manual rebuild.

The first native fixture expected English text while inheriting another model
language; its language was made explicit. A 669-test gate passed before adding
the manual-rebuild guard. Two later owned test runs were deliberately interrupted
to refine the fresh-publication and partial-rebuild guards; neither is a passing
release gate, and only their validation helpers were signaled. Final frozen-source
results and delivery evidence are recorded below after completion.

Three real exchanges in the existing ChatGPT Web discussion used verified
Pro/max controls. The investigation followed source receipts, API projection,
cache coverage and native rendering; partner agreement was not acceptance.
No private competition transcript or credentials were sent.

Recent events do not prove that every older long-running operation is visible:
a call starting before the range with no new event cannot be discovered by that
range alone. Older command history still backfills; no complete historical
coverage or per-Agent/remote-computation health claim is made from this repair.
The production command disclosure was not clicked, no research task was used as
a test, and no input or answer was sent, retried or adopted.

Private evidence:
`~/Library/Caches/ParallelCodexMaintenance/CommandVisibility467-20261021/`.

## Verified Delivery

The final unchanged source passed **670 tests in 63 suites** in 397.393 seconds.
The focused command/history, lifecycle, question, queue and updater gate passed
**115 tests in 10 suites** in 17.480 seconds. Contract, app-icon, offline signing
store and no-signing-path gates also passed. These are isolated validations, not
acceptance of an updated production Agent 8 window.

macOS **0.1.467 / build 467** was built with the existing
`Parallel Codex Release Signing` identity and passed deep/strict verification.
Its immutable 14,913,624-byte archive has SHA-256
`8946df8b2b91c34fde88b55ef17d485c84e72124b17cf5ce24919f0433dcf956`.
The existing formal server channel accepted it and returned the same release,
hash, size and signing requirement. Other components and non-macOS manifest
artifacts were unchanged.

An update check through the existing authenticated controller offered 467 to the
installed 465 client. The downloaded archive matched the published bytes; the
installed client's native update verifier accepted and staged the exact signed
bundle. No production bridge consumer, token refresh or task replay was created.

Installation was **not** performed and the client was **not** restarted. The
loaded application remained 0.1.465, PID 76563; its complete installed file set
matched the prior signed publication and `lsof` identified the loaded executable
inode. The observed three AX surfaces represented one actual document and two
auxiliary surfaces, not three separately checked workspace documents. No window
was opened, selected or closed for this repair. A staged bundle is not an
activated fix, and the production command disclosure has not been verified on
467. The user's current app can obtain 467 through Software Update.

Before/after publication observations at 17:12:30 and 17:14:29 UTC retained pcrd
0.1.102 PID 88104, its receipt-store identities and all ten original owner
process identities. All 16 observed native execution receipt identities were
unchanged; the 14 records with an observable live native process retained that
same process identity. The other two records had no observable live native
process at the baseline and are not counted as retained live executions.
Durable answer identities and actual document-state hashes were unchanged. This
proves publication/staging did not replace the observed executions; it does not
certify every Agent or remote research job as healthy.

`final-verification.json` binds these delivery stages, frozen source/gate hashes,
current loaded bytes and this document. No production task command, answer,
retry, adoption or stop was submitted, and no daemon or worker was restarted.
