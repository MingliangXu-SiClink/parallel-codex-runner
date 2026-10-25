# Operational/UX Logic Bugs Found

This document tracks bugs where code is internally correct but doesn't match user expectations about workflow behavior.

## Fixed Issues (Committed)

### 1. ✅ Agent switching restarts follow-up countdown
**Status**: Fixed in commit `99eca0d`

**User expectation**: When browsing agents with ←/→ while a follow-up countdown is running, the countdown should preserve remaining time.

**Actual behavior**: Every agent switch restarted the full countdown, making it impossible for users to browse their options without pushing back their queued message.

**Fixed in**:
- `src/parallel_codex_runner_core/tui_textual.py` line 2217-2227
- `apps/macos/Sources/PCRRemoteMac/AppModel+RunControls.swift` line 27, 118, 135
- `apps/android/app/src/main/java/com/parallelcodexrunner/remote/managers/RunControlActions.kt` line 116
- Backend `src/parallel_codex_runner_core/remote/follow_up_queue.py` `_record_retry` method

### 2. ✅ Countdown restarts after loading agent details in macOS
**Status**: Fixed in commit `d39aea9`

**User expectation**: When selecting an agent that requires loading details, the countdown should not restart.

**Actual behavior**: Two paths in `selectAgent` were calling `reconcileFollowUpQueue(resetCountdown: true)` — the agent-load-failure path and the successful-commit path — unnecessarily restarting the countdown.

**Fixed in**:
- `apps/macos/Sources/PCRRemoteMac/AppModel+RunControls.swift` lines 118, 135

### 3. ✅ Maintenance pause tests fail due to locale-dependent ps output
**Status**: Fixed in commit `95703eb`

**User expectation**: Process pause/resume should work consistently across locales.

**Actual behavior**: `ps -o lstart=` produces locale-dependent output (6 tokens under Chinese locale vs. 5 under English), which broke the fixed `split(None, 9)` and silently dropped every process, causing tests to fail.

**Fixed in**:
- `src/parallel_codex_runner_core/pause_batch_for_recovery.py` — switched from `lstart` to `etime` (single, locale-independent token)

## Found but Not Yet Fixed

### 4. ⚠️ Queue editor reopening restarts countdown
**Status**: Fix ready but blocked by src/ migration

**User expectation**: When opening `/queue` during an active countdown (say, 7 seconds remaining), editing the queue, and closing the editor, the countdown should resume from ~7 seconds.

**Actual behavior**: Opening the editor pauses the countdown by clearing `follow_up_continue_at`, but the original value is not saved. When the editor closes, `_start_follow_up_countdown()` is called, which restarts from the full delay.

**Fix location**: `src/parallel_codex_runner_core/tui_commands.py` lines 733-761, 822-836
- Need to save remaining time before opening editor
- Restore it when closing (or call a new `_resume_follow_up_countdown(remaining)` helper)

**Blocked**: Cannot commit because file is part of untracked src/ migration content.

### 5. ⚠️ Retrying the continuation source agent doesn't clear countdown
**Status**: Identified, fix design in progress

**User expectation**: When a follow-up countdown is running from AGENT-003, and user runs `/retry 3`, the countdown should immediately stop and show "Retry queued for AGENT-003; queued follow-up will wait for completion."

**Actual behavior**: 
1. Countdown continues running after `/retry 3`
2. When countdown reaches 0, `_dispatch_follow_up` checks `_successful_agent_result(3)`
3. Finds it's `None` (because retry cleared the result)
4. Dispatch fails, sets `follow_up_ready = True`
5. Status changes to "Queued follow-up is waiting: AGENT-003 is not successful"

The outcome is correct (doesn't dispatch from a non-successful agent), but the user experience is poor (countdown runs then fails at the last moment instead of failing immediately).

**Fix needed in**:
- TUI: `src/parallel_codex_runner_core/tui_commands.py` `_handle_retry` (line 258-328)
- macOS: `apps/macos/Sources/PCRRemoteMac/AppModel+RunControls.swift` `retrySelectedAgent` (line 408-441)
- Android: Need to find retry handler
- Backend: `src/parallel_codex_runner_core/plugin_runtime.py` `_retry_agent` (line 4549-4604) should notify follow-up coordinator

**Design question**: Should this also apply when retrying a *different* agent (not the continuation source)? Probably not — only clear countdown when retrying the *current* continuation source.

### 6. ⚠️ Rejecting the continuation source agent doesn't clear countdown
**Status**: Investigated, determined to be **correct behavior**

**Initial concern**: When a follow-up countdown is running from AGENT-003, and user runs `/reject 3`, the countdown continues.

**Analysis**: 
- `/reject` only sets a `rejected` flag (recommendation, not success status)
- `_dispatch_follow_up` checks `_successful_agent_result`, which only checks `status == "success"`, not the `rejected` flag
- So dispatch will proceed from a rejected agent if user manually selected it

**Conclusion**: This is **correct behavior**. Rejection means "not recommended", but if the user explicitly selected it as continuation source, they should still be able to continue from it. The countdown should continue.

### 7. 🔍 Storage preflight ERROR leaves follow-up queue stuck
**Status**: Already fixed in current code

**Finding**: Subagent report claimed this was a bug (Bug #6), but inspection shows:
- Line 1456-1457 already consumes follow-up on error path
- Line 1378-1379 consumes follow-up on decline path
- Bug #6 appears to have been fixed before investigation

### 8. 🔍 Settings changes blocked while running
**Status**: Design decision, not a bug

**Subagent claim** (Bug #2): Users expect `/model`, `/effort`, `/subagentslimit` changes to apply to retries.

**Analysis**: 
- Settings changes are blocked while running to prevent confusion
- Running agents use `pending_execution_args` (frozen snapshot)
- Retries use `CandidateBatch`, which only saves `prompt` and `developer_instructions`, not model/effort
- Allowing mid-run settings changes would require propagating to `pending_execution_args` AND adding model/effort to `CandidateBatch`

**Conclusion**: This is a **design tradeoff**, not a clear bug. The current behavior is internally consistent. Would need user input to decide if the complexity is worth the UX improvement.

## Subagent-Reported Bugs Not Yet Investigated

### Bug #4: Agent selection during countdown — status lag
**Files**: `tui_textual.py` lines 2209-2220
**Claim**: When switching agents during countdown, status message may not update with new agent index until next tick.
**Priority**: Low (cosmetic issue)

### Bug #9: Queue editor overrides agent selection
**Files**: `tui_commands.py` lines 822-836
**Claim**: After closing queue editor, `_schedule_follow_up_after_completion()` forcibly sets `selected_agent = recommended_agent`, overriding user's manual selection.
**Priority**: Medium

### Bug #10: Context mismatch clears entire queue
**Files**: `tui_textual.py` lines 3617-3624
**Claim**: When workspace/resume changes, `_clear_follow_up_queue()` discards ALL queued items, even ones that might be valid in the new context.
**Priority**: Medium

### Bug #11: Consume by text matching is fragile
**Files**: `tui_textual.py` lines 3584-3600
**Claim**: `_consume_follow_up` matches by prompt text; duplicate prompts could consume wrong item.
**Priority**: Low (edge case)

### Bug #12: queued_agent not corrected on batch discard
**Files**: `tui_textual.py` lines 3082-3098
**Claim**: `_discard_queued_candidate_batches` corrects `selected_agent` but not `queued_agent`, which could point to a removed agent.
**Priority**: Medium

### Bug #8: Finalization failure has no retry
**Files**: `finalization.py` lines 98-115
**Claim**: If workspace sync fails mid-finalization, there's no retry mechanism and workspace may be in inconsistent state.
**Priority**: Critical (but requires major feature addition: backup/snapshot/retry command)

## Investigation Notes

- **Primary pattern**: Many UX bugs involve state transitions that don't fully clean up or update related state (countdown, agent selection, queue)
- **Root cause**: Separation between immediate state (TUI/UI) and backend state (follow-up coordinator) creates opportunities for desync
- **Test coverage**: Most of these bugs wouldn't be caught by existing tests because they're about *user experience* during state transitions, not correctness of final states

## Next Steps

1. Fix issue #4 (queue editor countdown preservation) after src/ migration is committed
2. Fix issue #5 (retry clears countdown) — needs coordination across TUI/macOS/Android/backend
3. Investigate subagent-reported bugs #9, #10, #11, #12
4. Consider whether issue #8 (finalization retry) is worth the implementation complexity
