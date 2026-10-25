# macOS Last Known Codex Quota

## Scope

macOS 0.1.400 / build 400. Client-only change. Do not restart pcrd, Agents,
web services, or other processes. Preserve the separately staged idle-only
pcrd 0.1.67 deployment and all unrelated repository changes.

## Diagnosis And Changes

The successful usage snapshot survived transient failures, but the sidebar
replaced its number with `Refresh needed` whenever it was older than 120 seconds,
the latest attempt failed, or its reset time passed. The details also described
ordinary staleness as an inability to update.

The sidebar now displays the last available weekly percentage independently of
age and refresh state. A pending request or a transient unavailable response
does not hide the value. A reset boundary changes the label to `Last ... left`
(`上次剩余`), retaining the observed percentage without inventing a new-cycle
100% balance. Window rows and accessibility values use the same rule.

The details display the last successful observation's date and time. Only an
actual completed refresh failure shows the cached-data failure notice. Merely
old data or a pending retry does not claim a failure. Explicit sign-out and
account-unavailable responses still clear old data; machine/workspace isolation
and stale-response rejection remain unchanged. Missing/invalid values are not
treated as unused quota. Integer provider values retain integer precision;
fractional values retain the existing two-decimal display.

This patch does not add a persistent account cache, infer account identity, or
change the service API. It corrects the hiding of the successful in-memory
snapshot. No real Codex turn is needed for usage acceptance.

## Verification

Full client regression passed: 282 tests in 38 suites, client contracts and
bootstrap checks, packaged math/syntax smoke test, and native icon checks.
Eight usage tests cover stale data, pending retries, failures, reset equality,
zero and fractional percentages, missing values, explicit sign-out, workspace
isolation, and rejection of a late response from another machine.

An isolated native client and controlled IPC fixture, using a private HOME,
verified the actual sidebar and popover:

- A one-hour-old successful snapshot showed `剩余 98.76%`, not a placeholder,
  and had no false failure notice.
- An unavailable response kept `98.76%` and the original successful timestamp.
- A pending retry kept the same percentage, suppressed the previous failure
  notice, and disabled only the refresh control.
- After the reset boundary, the sidebar and details showed `上次剩余 98.76%`
  and explained that the value came from the previous fetch.
- A later successful response replaced the cached value with `剩余 0%`.
- Explicit sign-out removed the previous account's number.

Acceptance includes native screenshots and accessibility observations; no
production account, task, configuration, or research file was changed by the
fixture. The fixture refuses Agent mutations and intercepts all quota queries.

Built with MacOSX26.5 SDK, matching the installed SwiftUI macro toolchain.
Binary SHA-256:
`221c624e1f257c90dd1aedc7d19969600b2818ee2b954791da6c19300c9b45a0`.
Evidence:
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/QuotaDisplay-20261003`.

## Publication And Deployment

Published macOS 0.1.400 / build 400 to the existing relay release catalog at
2026-10-03T11:30:08Z. Release ID:
`macos-release-58e7837cadba48ba8b5e142cf0883bbf`.
Archive: `release/clients/macos/Parallel-Codex-macos-0.1.400.zip`.
SHA-256: `a1398cb5330cea417dcc0cc2df74d2625413ca84767c8c22d71a0e2fbc750676`.
The catalog version, build, signing identity, archive hash, and non-revoked state
were verified. All non-macOS manifest components and artifact hashes are unchanged.

The new client is not installed or restarted yet: the per-task installation and
restart question is awaiting the user's reply. The formal installed client
remains 0.1.399 / build 399, PID 25451, started at 2026-10-03 13:04:21. pcrd remains
0.1.66, healthy and paired; `pcr-20261003T102522Z-8391bfb2` remains active and no
active operation is reported. This task did not restart a service or Agent.

The private QA client (PID 79240) and fixture server (PID 78846) exited. Calling
the native observation API immediately after quitting reopened the QA app as
PID 80010 with its default HOME; that post-quit observation is not part of the
isolated acceptance. No input or task/file operation was performed in the
reopened copy. It was closed without another auto-launching observation. Final
process checks confirm both QA copies and the fixture are gone, while the formal
client's process identity is unchanged.

## Design Consultation

https://chatgpt.com/c/6ac0e43e-549c-83ec-89ae-2a9a0d1c09bf

Two completed exchanges informed the reset-boundary label and the distinction
between staleness and failure. Suggestions to replace known numbers with a
placeholder were rejected to respect the user's request; additional refresh
process narration and unverified cross-account persistent caching were not added.
Local tests and native observations, rather than the dialogue alone, establish
the implemented behavior.
