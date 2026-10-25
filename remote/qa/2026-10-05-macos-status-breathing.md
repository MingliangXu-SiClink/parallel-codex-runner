# macOS 0.1.415: Agent and sidebar activity dots

## Scope

User requested the same subtle breathing treatment for working Agent tabs and
the existing yellow workspace status dot. This is a macOS-only release. pcrd
0.1.72, Android, relay, research workspaces and task configuration are unchanged.

## Implementation

- Working Agent tabs use blue dots in both the inline and sticky selectors.
  Selection, preparation, queuing, completion and errors retain their distinct
  existing text and semantics. A loading spinner is not used for execution.
- Workspace and recent-conversation activity dots keep their existing accent
  color and size, using the same pulse as the overall header status.
- All dots share a Core Animation epoch. Opacity moves from 0.65 to 1 and back
  over approximately three seconds; geometry, text and transcript state do not
  animate. Routine refreshes do not restart the pulse.
- Disconnected, historical, unavailable and pending-message states do not claim
  live execution. Warning/error indicators stay static. The workspace indicator
  can pulse during confirmed preparation, execution or finalization.
- Reduced motion disables animation. Hidden/occluded/detached indicators stop
  animating. Decorative native dots cannot intercept Agent-tab clicks.

## Verification

- Full non-interactive client suite: 336 tests in 39 suites passed (193.397 s).
- New native tests mount all eight Agent-tab dots, verify unchanged geometry,
  remove motion when connection confirmation is lost, mount sidebar dots and
  verify color/size retention when work stops. Layer tests cover phase sharing,
  appearance, reduced motion, visibility and click pass-through.
- App icon tests passed: eight PNG slots, ICNS sizes, seven native rendering
  sizes. The production build passed; its signature and designated requirement
  match the existing trusted updater identity. Packaged math/syntax rendering
  passed using the signed app's headless resource-smoke entry point.
- Only headless/offscreen tests ran, with foreground interaction suites disabled.
  No QA application was activated, no desktop screenshot was taken and no
  keyboard/mouse input was injected. Build/test jobs use low priority and one
  Swift build worker.
- Two initial test-compilation errors were corrected before the passing run;
  their logs are retained separately. No failed run is counted as acceptance.

Evidence directory:
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/StatusBreathing-20261005`.

## Deployment

Before publication, the installed client was 0.1.414 (build 414), pcrd 0.1.72 was
healthy and paired, scheduler PID 41934 was unchanged, and run
`pcr-20261005T020818Z-01105da7` remained active. The client outbox was empty.

macOS 0.1.415 (build 415) has been published to the authenticated release catalog.
The official built-in update check from build 414 returns this version, and its
archive hash matches the local signed artifact. All non-macOS components and
artifacts in the release manifest are unchanged.

- Archive SHA-256:
  `d9b4736481d6aac9881e27c07a4a7818b29ebc5c50565869029bb325ec9c1786`.
- Client binary SHA-256:
  `a4759176a0ac7aa246e2c8c3debcb0c71f7b72033cbc4c2dc54020c3b04fb7fb`.
- Post-publication read-only check: pcrd remains healthy and paired at 0.1.72,
  scheduler PID 41934 and service-file hash are unchanged; the same research run
  is active, with no active finalization operation and an empty client outbox.
- The installed client remains 0.1.414, PID 41835. No client installation or
  restart was performed in this turn, because separate approval has not yet
  arrived. The new visuals become active after the built-in update is installed.

Deployment evidence is in `staged-release.json`, `relay-publish.json`,
`relay-catalog.json`, `update-check.json` and `after-publication.json` in the
evidence directory. Publication is not being reported as installation.
