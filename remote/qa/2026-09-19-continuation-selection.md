# Continuation selection: 2026-09-19

## Root cause

Both clients treated a viewed Agent as a continuation decision. The daemon
then ranked the queue's saved `selected_agent` above its recommendation, so a
choice made during execution could override the completion-time recommendation.
macOS also preserved the viewed Agent after clearing its working-phase marker.

## Implemented contract

- Viewing an Agent during execution does not select a continuation branch.
- Completion chooses an eligible successful synthesis Agent with the most
  reasoning tokens, falling back to ordinary Agents when necessary. Missing
  token statistics retain the existing duration fallback.
- `continuation_agent` is separate from the configurable `recommended_agent`;
  a duration-based recommendation does not change this queue policy.
- A deliberate post-completion click uses `followups.select_agent` and persists
  a manual choice on the daemon. It wins over the automatic choice.
- An explicitly adopted Agent always wins. Starting a new execution or turn
  clears the previous selection. Clearing messages on the same run does not.
- Legacy enqueue/replace `selected_agent` values cannot overwrite a server-owned
  automatic or manual choice. Reopening a client cannot pin an old viewed Agent.
- Automatic fallback excludes rejected/stale candidates. An explicit manual
  choice may use a rejected candidate if it is otherwise eligible to continue.
- The new action is included in the encrypted Relay command allowlist.

Native acceptance also exposed a separate problem: quitting macOS implicitly
adopted the visible result. Quit now leaves the result undecided, with or without
a queue, rather than stopping other Agents or dispatching prematurely. Existing
pending-operation guards and resume-session handling remain intact.

## Automated verification

- Backend full suite: 700 tests and 33 subtests passed.
- After final queue and Relay refinements: 165 tests and 11 subtests passed,
  then passed again against the final built wheel.
- Targeted continuation/ranking coverage: 50 tests passed.
- Final macOS suite: 139 tests in 20 suites, plus transport/static checks.
- Final Android unit suite: 263 tests, no failures, errors, or skips.
- Final release/packaging checks: 14 tests passed.
- Signed macOS archive and Android APK built; published metadata and checksums
  match the release manifest.

## Native acceptance

macOS used the real signed native client with an isolated synthetic daemon/run,
not a production conversation or real model execution:

1. During execution, viewed ordinary Agent 1; no manual selection was saved.
2. Completion selected synthesis Agent 3 (500 reasoning tokens), ahead of
   synthesis Agent 2 (200) and ordinary Agent 1 (10000), even though the separate
   duration-based recommendation preferred Agent 2.
3. Clicked Agent 2 after completion; the daemon persisted manual Agent 2.
4. Quit and relaunched; Agent 2 was restored without adopting or dispatching.
5. Quit again and expired only the synthetic queue delay; the background
   coordinator dispatched exactly once from Agent 2 and emptied the queue.
6. Separately verified that quitting without a queue does not adopt a result.

The first synthetic fixture incorrectly combined manual scheduler processing
with its background thread. It was corrected and the sequence rerun in a fresh
fixture home; the single-dispatch result above is from that final run.

Android: installed the signed release on the authorized emulator, cold-launched
successfully (375 ms), and observed paired devices/workspaces. No physical phone
was used. Full queue interaction was not repeated in the Android GUI; selection
coverage there is unit/model testing plus native launch acceptance.

## Deployment

- macOS 0.1.375 (375): published, installed, signature checked, and restarted.
- pcrd 0.1.50: installed and gracefully restarted; Relay connection recovered.
- Android 0.1.266 (267): signed update published and emulator-tested.
- Relay 0.1.18: unchanged; no server binary update required.
- Live encrypted capabilities advertise `followups.select_agent` on pcrd 0.1.50.
- Authenticated macOS update check and actual package download verified build
  375, size 8617313, and SHA-256
  `65d5b342e5193b5b7f1ea0680b5dc009d3664e54d6b4f614203410e1e2119ab6`.

All six pre-existing worker processes survived the daemon restart. The last
active run completed naturally before the restart; no user run was adopted,
cancelled, or submitted for this QA. Web services on ports 8920 and 8950 remained
HTTP 200. The installed client restored the existing conversation, transcript,
result inspector, and empty composer. A pre-upgrade completed conversation's
viewed tab was left untouched; it is not a server-owned manual choice.

## Evidence

Logs, prior-install backups, signed builds, emulator screenshot, and safe release
verification metadata are in:

`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/ContinuationSelection-20260919-4XVkRd`

Final synthetic dispatch evidence: `/tmp/pcr-select-rxRvu0/phase.json`.
Artifact hashes and component versions: `release/manifest.json`.
