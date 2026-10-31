# PCR Working Instructions

## Execution Mode

- Do not create, enable, or resume Goal mode or scheduled automation, including heartbeat follow-ups, reminders, and cron jobs.
- Continue work in the ordinary conversation. A request to keep optimizing until the user accepts does not authorize a different execution mode.
- Do not reactivate old paused follow-ups or substitute another background scheduler. Existing prohibited modes may only be inspected, disabled, or removed to honor this rule.

## Current Performance Work

- User acceptance limits: a question or follow-up in any workspace must produce a real conversation response within 10 seconds; switching to any workspace's Agent must display its history and current conversation within 1 second. Measure the actual default user paths. A spinner, receipt, queued acknowledgement, incomplete history, or stale cache is not a passing result. Report external-provider delays and unverified paths honestly instead of weakening these limits.
- For a queued follow-up, preserve the existing queue rules. Its 10-second response clock starts only after all Agents have finished and the existing countdown has expired; time spent waiting for those two conditions does not count. Do not bypass the queue, shorten the configured countdown, interrupt Agents, or replay accepted messages to meet the limit.
- Performance is a primary requirement: reduce latency when switching workspaces or Agents, refreshing current output, submitting questions and follow-ups, and dispatching queued follow-ups. Investigate recurring disconnection indications as part of the same end-to-end request path.
- Diagnose and measure the actual bottlenecks. Do not substitute cosmetic changes, longer timeouts, hidden errors, or cached content for fresh data and responsive execution.
- Preserve the user's source changes, original message identities and receipts, and healthy background Agent computation. Do not replay accepted input or revive user-stopped Agents.
- Keep implementation, regression validation, signed release, installation, and actual-client verification distinct in reports. Do not claim this work complete while any required step remains unverified.
