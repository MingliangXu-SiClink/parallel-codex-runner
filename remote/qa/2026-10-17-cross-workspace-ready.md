# Cross-Workspace Running-Agent Readiness

## Scope And Safety

- macOS reading/cache paths only. Global pcrd remains the independently installed 0.1.98, PID 48685.
- No competition turn was used as a test, replayed, stopped, adopted, resumed, or rerun. No research file was changed.
- No signing, Keychain operation, remote-updater trust exception, system setting change, interactive QA window, or simulated input.
- The unrelated model/run-configuration design experiment was shelved, not included in these packages.

## Confirmed Findings

1. Running-Agent preloads advanced only the complete work-event cursor. A reply behind thousands of tool events was not available merely because a preload task existed.
2. Reply reads and long work/history reads shared transport capacity. Active raw/presentation snapshots also competed with disposable historical entries.
3. Conversation aliases could miss a prepared physical run/Agent despite identical complete projection inputs. A new latest run could cancel an older active or still-draining reader.
4. Filtering out tools exposed an old text-equality deduplication rule: distinct output events with equal text disappeared until tool backfill arrived. The same-source interleaving test reproduced it.
5. Cold-cache preparation preceded live reads, cold archives could queue ahead of active branches, and page publication waited for full formatting followed by a whole-transcript CAS. These were additional preparation/publication risks under sustained work updates.
6. Repeated linear ID/operation lookups made complete projection preparation quadratic. The same 4096-event interleaving test took 25.423 seconds before the indexed preparation change and 1.483 seconds afterward in strict focused runs. This is an isolated test comparison, not a production-network speedup.

The two bounded production read probes used different branches and were not a matched speed comparison: a full-work page returned no body after 6.213 seconds; a conversation-only page on another running branch returned nine body items after 7.068 seconds.

## Implementation

- Preserve separate work and conversation cursors/EOF. The existing 0.1.98 filtered `runs.agent` contract supplies the body without waiting for complete tools.
- Merge by source run/Agent, contiguous page progress, event identity and item identity. Preserve independent final-artifact byte/receipt guards. Cursor and data persist in the same atomic cache record.
- Three bounded background lanes, two requests each, separate metadata/history, complete work, and current body. Foreground commands are not moved into those queues.
- Keep one current raw/prepared working-set snapshot per active or draining branch; historical LRU limits remain. Alias reuse still requires complete projection-input equality.
- Keep readers independent of navigation and latest-run aliases. Publish raw pages before expensive preparation; publish a prepared projection only when its content still matches.
- Prepare old cached views concurrently with current reads, prime active bodies before unselected cold archives, and use local ID/operation indexes for batch projection.

## Evidence And Intermediate Deployment

Private evidence roots:

- `~/Library/Caches/ParallelCodexMaintenance/CrossWorkspaceReady-20261017/`
- `~/Library/Caches/ParallelCodexMaintenance/CrossWorkspaceReady-20261017-441/`

0.1.440 passed 558 noninteractive native tests, 94 strict focused tests, protocol/state contracts, icon checks, build/resource smoke tests and the no-signing regression. It was immutably published to the local-development channel and normally installed at 02:18:55 UTC, without restarting pcrd or workers. Source-verified local packages are not signed remote-updater releases.

Real acceptance did not stop at that installation: the first post-install cache-file observation still had no independent body fields. That observer does not inspect in-memory state; coalesced cache persistence can lag live publication, so absence on disk alone does not prove absence in memory. Sampling confirmed both new transport lanes were actually running; socket waiting alone did not establish a backend defect. Later exact-file observation at 02:29:02 UTC showed body data for all 27 branches in the three active workspaces, with 26 full work logs still incomplete. No branch was clicked to warm it. The cold preparation/publication risks above were addressed in 0.1.441 rather than assigning a production delay percentage to untraced stages.

The 440 installation preserved all eight worker owners and all 36 original Node/Codex process identities, the daemon definition/PID, receipt-store identity, draft/settings/question-answer entries and the original local queue. Four short-lived SSH/SCP descendants ended between observations; no signal or restart was sent to them. Frontmost PID stayed 8466.

The real installed 0.1.98 controller/EventLog was also exercised only in a private temporary namespace: 20008 events, eight branches, body identity/content equal to complete work pagination, maximum filtered read 0.248 seconds. No production run was submitted or modified.

## Final 441 Gate

Status: 0.1.441 is immutably published in the source-verified local-development channel, installed, and actually running. 560 complete noninteractive native tests and 96 strict focused tests passed; protocol/state contracts, icon checks, no-signing regression, build, resource smoke and process smoke all passed against matching frozen source hashes. Interactive/foreground QA suites remain excluded by the user's no-interference requirement.

New strict cases include legacy 3000-tool-block caches (all eight independent body commits in 0.144 seconds), active-versus-cold-archive scheduling, empty advancing pages, EOF followed by new output, late/duplicate pages, restart persistence, cache pressure and final-answer draining. In the offscreen native fixture, 24 branches across three workspaces painted their own body while all work backfill was held; maximum warm switch was 65.527 milliseconds in strict testing and 70.297 milliseconds in the complete cohort. AppKit/TextKit first initialization is not counted as a warm switch, and no production click latency is claimed.

## Actual 441 Acceptance

- Installed at 02:37:16 UTC by normal client termination, atomic same-volume program exchange and background launch. No force quit, backend restart, data rollback or foreground activation. Frontmost PID remained 8466.
- Actual client PID 4826, loaded executable inode 186467629, SHA-256 `4046a295d95533e7b49ef10f08c6254a8334f5ed063d0c6e1869555497102045`. Loaded inode and all installed bundle files match the frozen build.
- Local ZIP SHA-256 `d009fe6bbc7508a2644eb4e267649108c4922a32fec2e6fd64fffa217e542e30`, 14831719 bytes. This is not a signed remote-updater publication.
- Daemon 0.1.98 PID 48685 and LaunchAgent bytes unchanged; all eight original worker owners and all 36 original PCR-owned Node/Codex identities preserved. Short-lived SSH tools continued their normal lifecycle; no stop/restart action targeted them.
- Receipt-store identity unchanged. Draft/settings/local-queue hashes and per-Agent answer entries unchanged across normal exit and restart. Outbox and accepted-handoff remained empty. No original message was resubmitted.
- At 02:38:13 UTC, exact-cache inspection found all 27 branches in the three active workspaces with their own body, independently confirmed conversation EOF and matching physical run/Agent identities. All 27 conversation cursors advanced from the earlier checkpoint. Complete work remained unfinished on 25 branches; this did not gate their body.
- Additional retained/completed workspaces also had prepared conversation pages. No workspace or Agent was clicked to produce these cache observations.
- Fresh `system.capabilities`, `system.health`, `controller.status` and `codex.model_options`: daemon 0.1.98, health `ok`, paired, effective `gpt-6.1-sol`. The historical relay 502 is not a new failure; link continuity is recorded separately in the private observation.
- Read-only native UI acceptance retained the memory workspace and Agent 1, showed actual body text and continuing native output at 10:37 local, kept the composer empty, and showed no internal loading/opening banner. No production switching latency was measured by clicking the user's interface.

Final read-only acceptance additionally compared the original queued-message ID, destination and complete prompt hash with the pre-repair baseline: unchanged, still one local queued message, with no outbox or accepted handoff and no extra unchecked window. All 36 original Node/Codex identities still matched the original pre-440 process baseline. Bounded native-event reads found 16, 27 and 61 post-441-install Agent events in the original memory, compression and fine-hand runs respectively; no new turn was sent to obtain them. The existing relay connection still reported the original 2026-10-16 17:08:23 UTC `since` value. The monitor snapshot at 02:39:12 UTC showed client 441, backend 98, three active groups and all eight owners alive.

Evidence: `installation.json`, `process-continuity.json`, `observation-20261017T023813.916923Z.json`, `readiness-acceptance.json`, `final-acceptance.json`, frozen test/build reports and offscreen native PNGs in the 441 private evidence root. Program-only recovery copies of 439 and 440 remain in their respective private maintenance directories; restoring them must never restore old user message state.
