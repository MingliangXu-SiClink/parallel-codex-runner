---
name: parallel-codex-runner
description: Run one coding task through multiple isolated Codex CLI candidates, follow their progress, compare final responses and complete workspace diffs, stop or retry weak attempts, resume prior Codex sessions, and finalize one chosen branch. Use when the user asks for PCR, Parallel Codex Runner, parallel independent solutions, candidate comparison, or review-before-sync workflows in a local project.
---

# Parallel Codex Runner

Use the PCR MCP tools rather than built-in sub-agents when the user asks for this plugin. PCR gives every candidate an isolated workspace and leaves the original workspace unchanged until finalization.

## Start A Run

1. Resolve the target project to an absolute workspace path. Ask for it only when the active project is ambiguous.
2. Call `pcr_list_models` when the user requests a model or effort and compatibility is unclear.
3. To resume a conversation, call `pcr_list_resume_sessions`, let the user choose a session, and use `pcr_get_resume_history` when they need to review its earlier conversation before starting.
4. Call `pcr_start_run` with the user's complete task and requested settings. It performs the storage estimate itself.
5. If it returns `confirmation_required`, show the estimated usage and free space. Call it again with `confirm_large_run=true` only after explicit user approval.
6. Keep the returned `run_id` and event cursor. Detached workers survive MCP server recycling; do not assume a disconnected tool call stopped them.

Use `pcr_estimate_run` separately when the user asks about storage before submitting a task.

## Follow And Compare

- Call `pcr_wait_for_run` in bounded intervals and continue from `event_page.next_cursor`. Do not repeatedly request events from cursor zero.
- Use `pcr_get_agent` for a candidate's final response, status, token data, and agent-specific events.
- Use `pcr_get_diff` before recommending a candidate. Continue from `next_cursor` until `has_more` is false when the full patch matters.
- Treat `recommended_agent` as a review hint, not a quality verdict. Compare successful candidates by correctness, scope, tests, and actual patch content.
- Report failures, killed candidates, and meaningful progress without dumping repetitive raw events.

The runtime preserves chronological events without truncating them. Diff pages are lossless; pagination is only a transport limit.

## Control Candidates

- Use `pcr_kill_agent` to stop one running or queued candidate while allowing the others to continue.
- Use `pcr_stop_run` to stop the whole batch without syncing a result.
- Use `pcr_reject_agent` to remove a candidate from recommendation, or set `rejected=false` to restore it.
- Use `pcr_retry_agent` only for failed, killed, cancelled, or interrupted candidates.
- Use `pcr_add_agents` after the active batch stops to add more independent candidates for the same prompt. If it requests storage confirmation, obtain explicit approval before calling it again with `confirm_large_run=true`.
- Use `pcr_list_runs` after an app or MCP restart to find retained or interrupted runs.
- Use `pcr_cleanup_expired_runs` when the user asks to release expired plugin storage immediately.

## Finalize Safely

Call `pcr_accept_agent` only after the user explicitly selects a successful Agent or explicitly authorizes you to choose and finalize one. Completion and recommendation alone are not consent to change the original workspace.

Acceptance stops remaining work, syncs the selected workspace when `sync_back=true`, promotes its Codex session, and cleans candidate workspaces unless retention was requested. If `sync_back=false`, state clearly that the original workspace will not be modified.

Use `pcr_continue_from_agent` when the user explicitly wants the next prompt to
continue from one successful Agent. It performs storage confirmation before
accepting, then promotes that Agent's Codex session and starts the next run.

If a run reports `sync_ambiguous`, do not accept or sync it again. Ask the user
to inspect the original workspace, then call `pcr_recover_finalization` with
their explicit conclusion about whether the sync was applied.

Use `pcr_discard_run` only when the user wants to abandon the run. It stops active work and removes candidate workspaces by default. Do not discard a potentially wanted branch merely to tidy status.

## Safety

PCR isolates working trees, not operating-system access. Candidates still share the host, network, account, quota, and possibly Git object storage. Do not describe PCR as a sandbox, and do not run untrusted prompts or repositories without warning the user.
