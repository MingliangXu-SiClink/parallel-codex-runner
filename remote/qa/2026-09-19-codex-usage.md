# Codex subscription usage: 2026-09-19

## Product behavior

- The macOS sidebar shows a gauge and weekly remaining percentage beside Settings.
  Clicking it opens a native popover with the selected computer, plan, available
  quota windows, remaining percentages, reset times, last update, and refresh.
- These are account limits from Codex, not estimates based on conversation tokens.
  A remote computer uses its own Codex account through the existing encrypted
  client-to-pcrd route. Switching computers/workspaces cannot reuse a late reply
  from the previous selection.
- Missing or unavailable limits never mean zero usage or 100% remaining.
  Temporary failures retain the last snapshot with an explicit stale label;
  sign-out and unsupported accounts clear it. Old services explain that an update
  is required. Reaching a reset time does not fabricate a replenished allowance.
- Refreshes share a 60-second server cache, with a five-second minimum for manual
  refresh. Background polling pauses when the app is not active; opening the
  popover, returning to the app, and reconnecting request a refresh. Focus changes
  do not cancel an already-running quota read.

## Source and privacy

The bounded, read-only app-server exchange is:

1. `initialize` / `initialized`.
2. `account/read` with `refreshToken: false`.
3. `account/rateLimits/read` for supported ChatGPT accounts.

The implementation was checked against the locally installed Codex and the
[official app-server documentation](https://developers.openai.com/zh-Hans/docs/app-server).
It prefers `rateLimitsByLimitId`, retaining independent buckets, and falls back
to the legacy `rateLimits` response. Weekly and five-hour windows are identified
by duration, not by assuming that primary means five hours. Remaining percentage
is clamped `100 - usedPercent`.

No thread, model turn, login, purchase, or quota reset is performed. Account email,
IDs, credentials, and raw CLI errors are not returned to clients. The server cache
is in memory. Authentication/configuration file metadata invalidates that cache
without reading or copying credentials. Each subprocess has a 15-second request
deadline and bounded shutdown; `wait_for` retains Python 3.10 compatibility.

## Verification

- Full backend suite: 728 tests and 36 subtests passed in 427.31 seconds.
- Final usage-specific suite, rerun after the timeout compatibility adjustment:
  14 tests passed in 1.98 seconds.
- Release-bundle and plugin-package checks after publication: 14 tests passed.
- Final macOS suite: 154 tests in 23 suites passed in 56.364 seconds, plus protocol
  and desktop-state checks. Final signed release build succeeded in 36.36 seconds.
- Automated cases cover read-only JSON-RPC traffic, timeout/process cleanup,
  credential-data exclusion, cache invalidation/throttling, unknown values,
  quota-window order, independent buckets, controller/Relay routing, stale/reset
  behavior, old servers, sign-out, and late responses after machine changes.
- Started a real native release client in a separate bundle and fixture home.
  Its fixture provides no production conversations and blocks run mutations.
  The final fixture imported the built pcrd 0.1.53 wheel, not editable source.
- The quota endpoint used the real local Codex account. During acceptance it
  returned a Pro weekly window in `primary` with 10,080 minutes. The UI correctly
  displayed remaining percentages and the September 25, 14:17 reset in local time.
  Observed percentages changed during testing; they are not a current balance.
- Clicked the footer and manual refresh. Checked dark and light appearance,
  extra-large text, and a sidebar narrowed to 228 points. Settings and usage
  remained separate, readable controls; the details stayed within the popover.
- Injected an unavailable response into the isolated fixture, verified retention
  of the last quota with a stale message, then restored the real endpoint and
  verified recovery through the refresh button.
- Quit the isolated client and stopped its fixture server. No production client,
  pcrd, Agent, or web service was restarted/stopped. No Android/device acceptance
  or live remote-computer quota acceptance was performed in this round.

## Publication without activation

The prior instruction to publish without restarting remains in effect.

- macOS 0.1.378 (378) is signed and published on the private Relay update channel.
- pcrd 0.1.53 is built in the local release bundle, not installed on live services.
- Authenticated update check and one-use-ticket download succeeded. Downloaded
  archive version, size, SHA-256, and signing requirement match the final build.
- Installed macOS remains 0.1.376; the running pcrd reports 0.1.51. The new usage
  feature requires activating both the client and the corresponding device's
  updated pcrd. Android 0.1.266 (267) and Relay 0.1.18 were not changed.

Release ID: `macos-release-2fd2f18b2ef647abb8be7c9d9a63f8e7`.

macOS archive: `release/clients/macos/Parallel-Codex-macos-0.1.378.zip`

- Size: 8,780,512 bytes.
- SHA-256: `9d22adee1adb7fa2ac9da15a4ebfb2f859d005ca2bdda476667f92d266022f35`.

pcrd wheel: `release/pcrd/parallel_codex_runner-0.1.53-py3-none-any.whl`

- Size: 1,098,885 bytes.
- SHA-256: `b94fa4d1ef2d59897df6dd02f2807f4c2549e3995cc9951cb9ca88cc31cba1f6`.

Builds, isolated fixtures, sanitized usage replies, and authenticated publication
verification are retained outside the repository:

`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/CodexUsage-20260919-MnuN7C`

The temporary remote upload was removed. `release/manifest.json` records the
final artifacts and preserves the unchanged Android/Relay entries.

## Activation follow-up

The user subsequently authorized installation and activation on September 19.

- Installed the verified 0.1.53 wheel into the interpreter used by the existing
  LaunchAgent. Gracefully stopped only scheduler PID 24294 and started PID 82469,
  without signaling worker process groups. Relay reconnected; no startup warnings.
- All six pre-existing worker PIDs survived the scheduler restart. A later check
  found one corresponding run finalized without error and its worker stopped;
  the other five remained present. No Agent execution was active at preflight.
- Installed the exact published, signed 0.1.378 archive and relaunched the formal
  client. The old client bundle and service package are backed up. Staging was
  verified before replacement; no workspace synchronization or adoption was
  requested by the installation procedure.
- Verified in the formal client that the same conversation, sidebar proportions,
  result inspector, and unsent draft were restored. The draft was not submitted.
- Opened the new footer control and verified a real weekly remaining balance,
  reset date, and successful refresh. Closed the popover and restored the input
  focus. The service independently returned `available` for `codex.account_usage`.
- Existing web services on ports 8891, 8920, and 8950 returned HTTP 200 both before
  and after activation. Android and Relay were not changed.

Activation backups and verification records:

`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/ActivateUsage-20260919-pusHvI`

Current verified local versions after activation: macOS 0.1.378, pcrd 0.1.53.
