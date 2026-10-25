# macOS Typing Latency: Read-Only Diagnosis

## Scope

Production app PID 30884 is macOS 0.1.418 (418), launched at
2026-10-07 14:08:04 +0800. Production pcrd is 0.1.79.

This investigation did not edit product source, restart the client or daemon,
send keyboard input, open a QA window, alter research files, or mutate user
tasks. Profiling sampled the existing app without taking focus. One standalone
Foundation benchmark compiled at low priority and did not construct a window.
The automatically permitted ChatGPT consultation was attempted in a hidden
embedded browser, but navigation and CDP focus-emulation commands timed out.
No question was submitted, no response was received, and no alternate browser
or account was used. All conclusions below are based on local evidence.

## Live Evidence

Evidence directory:
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/TypingLatency-20261007`.

- `app-live-sample.txt`: 3-second sample, 10 ms interval, beginning
  2026-10-07 17:09:27 +0800. The main thread has 266 observations.
  120 observations include `AgentActivityPresentation.clock` beneath native
  SwiftUI/AttributeGraph view-state equality. This is about 45% of main-thread
  observations in this interval, not a whole-process CPU percentage or an
  end-to-end keystroke-latency measurement. 109 observations reach the main
  event loop's `mach_msg2_trap` wait. Inclusive symbol totals can overlap.
- The same live stack directly identifies
  `ConversationAgentSwitchState.agentPresentationMatches` at
  `Transcript.swift:244-245` and ISO8601 formatter initialization/parsing/ICU
  calls. The stack is not waiting for a model response or synchronous socket
  traffic on the main thread.
- `top` reported 59.4% app CPU in its second interval while system CPU remained
  about 85% idle. Physical app footprint was about 310 MiB, peak 563 MiB.
- `app-confirmatory-sample.txt`: a later 5-second sample has 456 main-thread
  observations, 447 reaching the event-loop wait and none in the timestamp
  comparison. A separate later `ps` snapshot reported 0.3% app CPU. The problem
  is bursty UI-update work, not proof of a permanently deadlocked main thread.
- A health read found no active runs or active operation and empty admission
  and delivery counts. A separate later latest-run read showed five successful
  Agents and a stored `finalizing` run status; all five retained non-null output
  and observation timestamps. These reads are not one atomic snapshot and do
  not prove that every finalization record has resolved.
- `memory_pressure -Q` reported 47% system-wide memory free. Swap usage was
  about 11 GiB, but the two `top` intervals reported no new swap-in/out activity.
  Cumulative swap usage alone does not establish memory pressure as the cause
  of this input delay, nor does this brief check rule out every system issue.

## Confirmed Implementation Problems

1. The 0.1.418 equality hot path performs presentation work. Each equal Agent
   compares both old/new output and observation times by calling `clock`, rather
   than comparing a cheap precomputed value. Five Agents with both fields set
   perform 20 parse-and-format operations per equal state comparison, even when
   the underlying timestamp strings have not changed.
2. `clock` calls `parseInternetDate`. That function creates an ISO8601 formatter
   on every call and may create a second formatter for its fallback. These
   allocations and ICU parsing appear directly in the production main-thread
   sample. The previous cached 0.1.417 binary has no
   `AgentActivityPresentation` symbol; the current binary does.
3. Input and the conversation share the same broad ObservableObject.
   `textDidChange` updates the composer binding, which invokes `updatePrompt`.
   Both `prompt` and `promptHistoryIndex` are `@Published` in `AppModel`.
   The workbench/reader also observe this model. Ordinary text changes therefore
   invalidate observers outside the editor. The timeline projection cache
   itself tests a key containing `ConversationAgentSwitchState`, so even a cache
   hit can pay the expensive comparison. This establishes a costly dependency
   path; it does not mean every native transcript cell is physically redrawn
   for every character.
4. Periodic refresh also assigns `connectionLoading = false` and
   `connectionErrorMessage = nil` without change guards. Other refresh fields
   such as relay state legitimately change. These share the same publication
   domain as the composer and can provoke the same work. The present idle
   sample does not establish periodic polling as the dominant hotspot.

## Isolated Cost Probe

`ClockComparison.swift` was compiled with optimization and the unchanged
production `InternetDate.swift`. It compares two timestamp fields for five
synthetic Agents using the current four-clock-calls-per-Agent equality pattern.

- First run: cold comparison 9.32 ms; warm median 1.74 ms.
- Repeated run: cold comparison 15.16 ms; warm median 1.76 ms.
- Each median uses five batches of 50 comparisons.

This only measures the Foundation portion of one equality comparison. It
excludes SwiftUI graph comparison, layout, editor/IME work, scheduling, and the
number of comparisons per frame. It is not a measured per-key latency and not
an accepted/shipped performance fix. No speed-up claim is made for a proposed
alternative.

## Correct Fix Direction

Move timestamp parsing/display projection out of equality and render-diff hot
paths. Recompute an immutable, cheap presentation key only when its source
data, locale or time-zone context changes. Keep terminal/active display
semantics and exact raw history intact.

Separate editor draft publication from transcript/sidebar publication; reduce
the scope of background snapshot publication and skip unchanged assignments.
Keep original command identity, coherent send admission and draft persistence.
Do not merely slow polling, freeze all background updates, or disable the
compositor-only breathing indicator. The indicator was not the prominent
sampled hotspot.

Acceptance needs main-thread time and actual input/IME latency budgets on a
realistic long conversation while updates occur. Existing functional test
counts do not demonstrate these performance properties. This request was for
analysis; no product fix or new release has been made in this turn.
