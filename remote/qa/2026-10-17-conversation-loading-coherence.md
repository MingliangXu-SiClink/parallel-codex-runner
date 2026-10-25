# macOS Conversation Loading Coherence, 0.1.446

## Observed Failure and Cause

The user observed a false earlier-turn footer, followed by the latest answer without its older conversation, followed later by the missing history. This is not evidence of a stopped Agent or lost server-side messages.

The client had four independently reproduced problems:

- A refreshed index could announce a newer turn in the same conversation without advancing the visible run. The existing reconciliation only handled a changed conversation ID. This temporarily classified the visible previous latest run as historical.
- The composer treated the negation of "known latest turn" as "known earlier turn". An unresolved index therefore displayed the historical footer without positive evidence.
- Foreground navigation and background preparation paged the same immutable run history separately; foreground navigation also ignored the complete warm-history flag.
- Background history publication waited for all pages and all Agent projections, then retried against the whole mutable snapshot. Concurrent live transcript updates could postpone publication. Timeline restoration also finished before missing history anchors were ready.

A bounded read-only production sample found 105 history entries. The initial 100-entry page took 7.253 seconds; a repeated initial page took 7.740 seconds and the final five entries took 3.723 seconds. Duplicate reads therefore amplified real transport latency. These measurements do not independently identify why that backend read took several seconds. No backend code, daemon configuration or production prompt was changed to manufacture a result.

## Changes

- Advance an unchanged conversation's visible latest run and matching cached history together, only when the view was following its previously known latest run. Preserve explicitly selected historical turns and pending historical navigation; do not override an active prompt submission.
- Display the earlier-turn footer only when the visible run is positively found among that conversation's runs and differs from its latest run. Missing index metadata is not a historical-turn state. Submission eligibility is not relaxed.
- Share one keyed history task between foreground and background callers. Reuse complete cached history without another RPC.
- Validate pagination, then publish and persist each cumulative raw history page before formatting. Prepare derived Agent projections after EOF, installing only projections whose current input still matches. Preserve exact machine/workspace/run/Agent identity and late-response fencing.
- Keep timeline-anchor restoration pending while history is genuinely incomplete. Already available latest-answer content stays readable and command readiness does not wait for history formatting.

Exactly 10 of the 535 frozen application/test/build inputs differ from the installed 445 manifest: `Info.plist`, `AppModel.swift`, `AppModel+Conversation.swift`, `AppModel+ConversationPreload.swift`, `AppModel+Conversations.swift`, `AppModel+Presentation.swift`, `ConversationReadProjection.swift`, `PromptComposer.swift`, `WorkspaceConversationNavigationTests.swift` and `WorkspaceReadinessTests.swift`. There are no added or removed frozen inputs. The execution configuration design, app lifecycle, draft persistence, answer outbox and updater source are unchanged.

Cold uncached history still requires a real read. This change does not claim instantaneous cold full-history rendering or suppress loading by pretending missing history is complete.

## Verification

All release gates use the same frozen source and MacOSX26.5 SDK:

- Red reproduction: 5 focused tests failed with 10 issues before the fix, including a visible stale run within the same conversation, repeated warm-history reads, withheld first pages, duplicate foreground/background pagination and premature timeline completion.
- `test-client.sh`: **583 noninteractive native tests in 56 suites passed**, plus protocol contracts and canonical icon checks.
- Strict cooperative-pool focused release gate: **103 tests in 8 suites passed**. Suites cover execution configuration, model options, offscreen composer, workspace readiness, transcript presentation cache, conversation navigation, workspace refresh and conversation usability.
- Multi-page regression proves foreground/background callers make 3 total requests for 3 pages, expose the first page before EOF, and make no additional history requests on a warm reopen. The old implementation made 6 requests, then 9 after reopening.
- Same-conversation latest publication, explicit historical selection, pending historical navigation, unresolved-index state, complete warm-cache reuse, late responses after workspace switches and preservation of a newer draft are covered.
- Slow-history regression keeps the latest answer rendered and command readiness available while timeline restoration waits for the missing history. Existing malformed cursor, retry, late-page, native input, IME, scroll-anchor and identity tests remain covered.
- An exploratory strict run including `ConversationContinuityTests` hit two existing synchronous test-server fixtures using blocking `Darwin.poll`/semaphores on the cooperative executor. No unrelated fixture code was changed. Those tests passed in the full normal release gate; they are not represented as passing under the strict focused gate.
- Optimized build, isolated-home process smoke, isolated resource smoke and no-signing gate passed. Test windows were offscreen/non-key. No production message or task was submitted as a test.
- Five normal-exit safety fixtures and a disposable atomic program-bundle exchange/reverse passed. These checks alone do not authorize quitting a current client with an unverified selection.

Private evidence: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/LoadingCoherence-20261017/`. The frozen source/log hashes are recorded in `release-*.json`; red/green logs and bounded read timings are retained without production prompt text or secrets.

The requested embedded-browser consultation could not be performed because this turn had no available `iab`/computer-use browser tools. No alternate browser, lower-capability model or unverifiable web consultation was substituted.

## Publication

- Version/build: **0.1.446 / 446**.
- Immutable archive: `release/clients/macos/local/Parallel-Codex-macos-0.1.446-local.zip`.
- Size: **14760525 bytes**.
- SHA-256: `8a67ccb9b93eb331f7645ef956694367ef7b7734a74357ba5afb4ee78421b5e8`.
- All extracted filenames, bytes, SHA-256 values, file modes and embedded version/build match the frozen bundle manifest. No `__MACOSX` or `._` Finder metadata is included.
- This is a source-verified local-development package, not a signed remote-updater release. The signed channel and its trust rules remain unchanged. No signing or Keychain operation was performed.

## Installation Boundary

After the user's first ready reply, fresh native Accessibility reads still returned success with zero application windows for the existing 445 process, PID 8027. Window-server records likewise showed no onscreen PCR window. This was not treated as permission to quit: the old app can retain a model after its window closes, so absence of windows cannot prove that normal quit will avoid adopting a completed result.

Those preflights did not quit, replace or relaunch the production client. No adoption setting was changed; no task was sent, stopped, adopted or retried; no older draft was restored. The canceled hourly PCR automation was not recreated.

## Installation and Live Acceptance, 2026-10-17 23:09 Asia/Shanghai

After the user's subsequent ready reply, a fresh native read found exactly one real main window and no dialog. The current run has one already adopted Agent, so its Agent switcher is intentionally absent. Absence of a switcher was not itself accepted as proof of safe exit.

The narrow single-Agent guard additionally required the actual header's adjacent **Saved** status, no previous/next-Agent controls, an enabled title with a hash matching the saved conversation, exact workspace/conversation/latest-turn/run identities, a single finalized non-accept-able Agent, no finalization in progress and no historical footer. The current `conversations.get` snapshot, workspace registry and `runs.get` all agreed. The header's native AXGroup was inspected separately from transcript scroll/list/text content; an arbitrary Saved string inside an answer cannot satisfy this check. The observer did not fabricate a selected branch label. The same proof, native editor hash, durable draft, messages, answers and queue checks were repeated before the normal quit.

- The guard's accepted single-finalized-Agent fixture and **12 unsafe identity/native-state rejection cases** passed. These are private installer checks, separate from the 583/103 application release gates. They reject missing/truncated headers, a merely completed result, historical state, multiple Agents, mismatched conversation/workspace/run, switcher controls, finalizing state and accept-able/unadopted results.
- One normal program-only replacement exited **0.1.445 / PID 8027** and launched **0.1.446 / PID 32899** in the background. No force quit, adoption-setting change, UI activation or simulated keyboard input was used. Foreground PID remained **20915** across the launch.
- Every installed file matches the frozen 446 manifest. `lsof` confirms the actual loaded binary inode **186872930**, SHA-256 **`107d9e714f9761b03edf77a0ae281e55ef554b13db171ddf3af9afb37aa3e3dd`**. Version/build read back as **0.1.446 / 446**.
- The reopened native conversation header hash and adjacent Saved state match the immediate pre-quit view. Native input was empty immediately before this update and remains empty with the same full-text hash. All saved draft/settings/queue/answer maps and the command receipt-store bytes/identity are unchanged. No historical draft snapshot was restored.
- **pcrd 0.1.98 / PID 48685** and all **4 baseline persistent owners** retain their original process identities/start times. There were no active runs at the actual replacement boundary; no claim of continued active computation or new native output is made. No task was sent, stopped, retried or adopted by the installer.
- The restored current run has an exact matching machine/workspace/run cache with **90 history entries**, `historyLoaded = true` and one Agent transcript. The native historical footer is absent. This is current-view/cache acceptance, not a claim that every cold conversation was navigated or that a new production prompt was exercised.
- The external summary remains an **AXButton**, **207.5 by 28 points**, outside/above the input. Its trailing blank space still does not hit the button. No extra real window or modal was introduced.
- Backend health and pairing remain OK. These client checks do not independently revalidate the public relay. No pcrd, Windows/WSL guard, security setting, updater trust rule or periodic monitor was changed.
- The previous 445 program bundle only is retained at `LoadingCoherence-20261017/previous-0.1.445.app`. Do not roll back user data to an earlier accepted-message state when restoring a program.

Private deployment evidence includes `single-agent-exit-guard-tests.json`, fresh `ui-observation-*.json`, `ui-before.json`, `native-install-before.json`, `install-before.json`, `installation.json`, `post-install-verification.json` and `installed-current-view-cache.json`.

**Status: source fixed, regression gates passed, immutable local package published, client installed and actual loaded bytes/current-view preservation verified.**
