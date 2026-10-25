# Pending Fixes (Blocked by src/ migration)

## Fix #6: Queue editor countdown preservation

**Status**: Code fixed, tested (all 1240 tests pass), but cannot commit because changes are in src/ migration files.

**Files modified**:
- `src/parallel_codex_runner_core/tui_commands.py` lines 744-856
- `tests/test_parallel_codex_runner.py` line 4337

**Description**:
User opens `/queue` during countdown (e.g., 5s remaining), edits or just browses the queue, then closes the editor. Expected: countdown resumes from ~5s. Actual (before fix): countdown restarted from full delay (e.g., 30s or 60s).

**Root cause**: 
`_handle_queue` saved `resume_after` flag but discarded the `follow_up_continue_at` timestamp. `_finish_follow_up_queue_editor` then called `_start_follow_up_countdown()`, which always set a fresh delay.

**Fix applied**:
1. When opening editor, save remaining time in new field `follow_up_queue_editor_saved_remaining`:
   - `0.0` if `follow_up_ready=True`
   - `max(0, continue_at - monotonic())` otherwise
2. When closing editor with `resume_after=True`, restore countdown from `saved_remaining`:
   - Set `follow_up_continue_at`, `_follow_up_countdown_second`
   - Call `_set_follow_up_countdown_status()` to reconstruct state
3. Updated test expectations to verify correct (preserved) countdown

**Commit message** (when ready):
```
fix(tui): preserve follow-up countdown when closing queue editor

User opens /queue during countdown (e.g., 5s remaining), edits or just
browses the queue, then closes the editor. Expected: countdown resumes
from ~5s. Actual (before this fix): countdown restarted from full delay
(e.g., 30s or 60s).

Root cause: _handle_queue saved resume_after flag but discarded the
follow_up_continue_at timestamp. _finish_follow_up_queue_editor then
called _start_follow_up_countdown(), which always set a fresh delay.

Fix:
- When opening editor, save remaining time in new field
  follow_up_queue_editor_saved_remaining (0.0 if follow_up_ready=True,
  max(0, continue_at - monotonic()) otherwise).
- When closing editor with resume_after=True, restore the countdown
  from saved_remaining instead of calling _start_follow_up_countdown().
  Set follow_up_continue_at, _follow_up_countdown_second, and call
  _set_follow_up_countdown_status() to fully reconstruct the state.

Updated test expectations to verify correct (preserved) countdown
rather than incorrect (restarted) countdown.

Closes operational UX bug #3 from subagent survey.
```

**Next steps**:
Once src/ migration is committed, apply these changes and commit with above message.
