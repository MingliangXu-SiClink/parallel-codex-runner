# macOS Conversation Architecture

The macOS app is a native client for a parallel Codex conversation. It is not a
log viewer, a TUI clone, or a dashboard with chat added underneath.

This document is the source of truth for the message screen. Runtime code and
views must derive from the model below rather than independently guessing what
the current run means.

## Product Model

```text
Computer
  Workspace
    Conversation
      Turn 1
        User prompt
        Candidate branches
        Chosen branch
      Turn 2
        User prompt
        Candidate branches
```

The four levels are deliberately different:

- A **Workspace** is a project directory. It can contain more than one
  conversation over time.
- A **Conversation** is one resumable lineage. It contains ordered turns.
- A **Turn** is one user prompt and every candidate created for that prompt.
- An **Agent branch** is one candidate answer and candidate workspace for the
  current turn. It is not a separate conversation until the user chooses it.

Only the chosen branch joins the conversation trunk. Reasoning, commands,
searches, temporary workspaces, run IDs, cursors, and worker processes remain
attempt details.

## Non-Negotiable Invariants

1. Switching Agent changes only the current turn's candidate branch.
2. Earlier chosen turns never change when the current Agent changes.
3. Selecting an Agent never edits the original Workspace.
4. `Use Result` applies the currently visible successful Agent, not the
   recommended Agent.
5. Sending a follow-up from review applies the currently visible successful
   Agent, promotes its Codex session, preserves the visible conversation, and
   starts the next turn as one recoverable transaction.
6. Sending while the visible Agent is unfinished queues the prompt and does not
   disturb the active turn. If the visible Agent has already succeeded and the
   queue is empty, sending explicitly stops the remaining Agents, applies that
   visible branch, and continues from it immediately.
7. Recommendation chooses what to show first. It never silently accepts a
   branch.
8. Server state is authoritative. Older responses cannot overwrite newer or
   terminal state.
9. A loading or failed request cannot replace visible cached conversation with
   a blank screen.
10. Runtime work is chronological within one branch and never becomes permanent
    history in the next turn. Natural Agent messages are conversation content,
    not runtime work, and remain on the chosen branch.
11. A Workspace with a running, reviewable, or recoverable current turn cannot
    open a blank `New Conversation` surface. The current turn remains visible
    until it is used, discarded, or recovered.
12. Mutation errors belong to the Workspace and turn that issued them. Switching
    context cannot surface a stale operation error in another conversation.

## Window Structure

The standard window has one navigation column and one conversation column.
Candidate comparison is an optional inspector. Workspace, conversation, and
results must never become four permanent columns that squeeze the transcript.

```text
+----------------------+--------------------------------+------------------+
| Parallel Codex  v    | Conversation                   | Results          |
| New conversation     |                                | (optional)       |
| Recent               | [user prompt]                  | Primary          |
|   Topic A            | Codex answer / live work       | Agent 1          |
|   Topic B            |                                | Agent 2          |
| Workspaces         + | [next user prompt]             |                  |
|   Project A          | selected candidate answer      | Synthesis        |
|   Project B          |                                | Agent 7          |
|                      +--------------------------------+------------------+
| Settings             | Composer and queued follow-up                     |
+----------------------+---------------------------------------------------+
```

### Navigation sidebar

The sidebar is conversation-first, not a remote-computer tree. The product name
opens a compact computer picker; computers never consume permanent navigation
rows. Recent conversations from the current Workspace come first, followed by a
flat Workspace list analogous to projects in a chat client. The current
conversation owns the only strong selection surface. The current Workspace uses
an icon and text emphasis rather than a second selected bar.

There is no resizable upper/lower split and no Computer -> Workspace ->
Conversation nesting. Recent conversations collapse after a small useful set so
they cannot push Workspace navigation out of reach. New conversation and search
stay at the top; Settings stays at the bottom. A row shows only a plain-language
actionable state and never exposes run IDs or worker state.

Computer and Workspace switching is transactional from the user's perspective:
the currently visible conversation remains in place while the destination is
validated and its cache is hydrated. The model commits the target, hierarchy,
and cached conversation together. A failed destination leaves the previous
conversation untouched and reports the connection problem in Settings.

### Conversation header

The conversation header is one compact row. It identifies the conversation and
its current state without becoming a second dashboard:

- Conversation title as the primary label.
- Current turn state, such as `Agents working 3/4` or `Ready to review`.
- Run settings and low-frequency conversation actions at the trailing edge.

It does not repeat the Workspace name, Turn number, status, or selected Agent in
a second row. It contains neither the Agent selector nor `Use Result`.

### Current-turn context

The current prompt appears once in the timeline. A compact candidate switcher
sits immediately below it. Switching Agent replaces only the candidate branch
below this boundary; accepted historical turns above it remain byte-for-byte
stable. The switcher exposes previous/next, a candidate menu, recommendation or
used state, and a `Results` control. It contains no metrics or workspace-changing
actions.

`Use Result`, Retry, Stop Agent, Changes, Work, reasoning tokens, duration, and
exclusion remain inside Results, where their target is visible. The only
turn-level destructive action in the conversation menu is `Stop Turn`.

### Conversation surface

Conversation owns the largest width and height. It renders a stable trunk plus
one replaceable branch for the current turn.

Opening a conversation always shows its continuous timeline and its latest
turn. Earlier turns remain in the transcript as ordinary user/answer pairs;
they are not alternate full-screen modes selected from a global Turn menu.
Candidate navigation changes only the latest turn's visible branch.

| Content | Presentation | Lifetime |
| --- | --- | --- |
| User prompt | Trailing tinted message | Permanent turn history |
| Chosen Codex answer | Leading Markdown answer | Permanent turn history |
| Live Codex update | Natural Markdown prose in the body | Permanent after its branch is chosen |
| Reasoning | Muted italic work row | Current attempt |
| Command | Compact monospace row and terminal state | Current attempt |
| Search/file/plan | Compact semantic work row | Current attempt |
| Final candidate answer | Leading Markdown answer, visually strongest, without a repeated Agent heading | Current branch |
| Warning/error | Local inline notice next to affected content | While actionable |

Connection health is not conversation content. Routine connection details live
in Settings; only an actionable outage adds a small warning beside Settings.
An Agent failure stays with that Agent and a send failure stays with the
composer. A successful background refresh must never erase an unrelated
actionable message.

Conversation and run-operation notices appear above the transcript, not inside
the composer. They therefore remain visible without resizing the editor or
making an unrelated operation look like a failed outgoing message.

If only the selected turn's live stream is temporarily unavailable, a compact
reconnecting notice belongs above the transcript. It must not replace the
composer's send or mutation error, and disappears as soon as polling recovers.

The app never repeats an avatar and heading for every streamed fragment.
Adjacent work fragments are grouped in chronological order. While running, the
group stays expanded. After completion it collapses to a single `Work` summary
and the final answer remains visible.

"While running" refers to the visible Agent, not the whole turn. If Agent 3 has
finished while Agent 4 is still working, selecting Agent 3 immediately presents
its clean final answer; its commands and reasoning remain available through
`Work` rather than filling the conversation by default.

### Results inspector

The trailing inspector owns candidate comparison. It is opened on demand and
collapsible at any width.

- Separate Primary and Synthesis sections.
- One compact row per Agent: status, name, recommendation, elapsed time, and
  rejection state.
- Selecting a row switches the current branch without changing the trunk.
- The selected Agent exposes Changes, Work, Retry/Stop, Exclude, and Use Result.
- Metrics and technical details are secondary disclosure content.

The inspector does not repeat a second candidate toolbar above the transcript.
Its selected row is the source of truth for the visible branch. The footer
contains only actions and secondary metrics for that row.

At wide widths, Results is a normal trailing inspector. At compact widths it
temporarily overlays the trailing part of the conversation instead of
compressing the transcript and composer below their usable width. Closing it
restores the uninterrupted reading surface.

When Results is hidden, the current-turn candidate switcher remains available
directly below the current prompt.

`Use Result` adapts to its consequence:

- During active work: `Stop Others & Use Result`, followed by explicit
  confirmation.
- After all Agents finish: `Use Result`.
- After finalization: a non-interactive `Used Result` state.

The app never labels the first case simply `Apply`, because that hides the fact
that other Agents will be stopped.

### Composer

The composer is fixed to the bottom of the conversation column.

- Empty conversation: start a parallel turn.
- Active turn: queue the prompt.
- Active turn with a visible successful result: clearly disclose that Send will
  stop the remaining Agents and continue from that result. This context uses a
  warning tint rather than the same neutral hint used for ordinary queueing.
- Review: continue from the visible candidate and show which candidate the next
  message will continue from;
  keep the editor placeholder conversational rather than encoding Agent state
  inside the placeholder.
- Failed submission: preserve the draft and show the recovery action locally.
- Existing queue: show one compact, clickable queue summary above the editor.

Sending from a successful candidate while other Agents are still working is a
destructive branch decision. The send control uses a warning tint and asks for
confirmation before stopping the unfinished Agents.

The screen does not continuously explain finalize, session promotion, or sync
back. The send button tooltip and a confirmation only when recovery is
ambiguous are sufficient.

## Turn State Machine

One pure presentation reducer maps the server snapshot to exactly one state:

```text
idle
  -> submitting
  -> preparing
  -> primaryRunning
  -> synthesisRunning
  -> review
  -> applying
  -> applied
```

Terminal alternatives are:

```text
stopped
failed
syncNeedsConfirmation
cleanupNeedsRetry
discarded
```

The same state drives toolbar status, candidate actions, composer behavior,
polling lifetime, and notifications. Views must not contain their own competing
interpretations of raw status strings.

## Action Semantics

### Start

Create a new turn. A selected resume session supplies earlier trunk history but
does not become a visible technical object.

As soon as Send is pressed, the optimistic user message becomes the next Turn
and the header follows that submission through Sending, Retrying, Opening, or
Needs Confirmation. The previous Turn must not remain labelled Ready to Review
while the next message is already in the durable outbox. Candidate navigation
is frozen until that submission either opens its Run or is edited/cancelled.

### Switch Agent

Replace only the current turn's branch projection. Keep the continuous
conversation's current semantic anchor; do not restore a different full-page
offset that moves the accepted history. If a queued follow-up countdown is
active, restart it from the configured duration.

### Use Result

Apply the visible successful Agent to the original Workspace, promote its Codex
session, and clean temporary data according to the saved settings. The UI keeps
the chosen answer visible while the transaction runs.

### Continue

Sending from review performs an idempotent handoff:

1. Freeze the visible trunk and selected candidate as the outgoing turn.
2. Persist the prompt submission before sending it.
3. Apply the visible candidate.
4. Promote that candidate's Codex session.
5. Append the new user prompt optimistically.
6. Activate the new run without blanking the conversation.

Any failure restores a recoverable outbox state with the prompt and previous
conversation intact.

The latest turn of an older finalized conversation may also continue. It
resumes that turn's promoted Codex session against the Workspace as it exists
now. Earlier turns inside that conversation remain read-only. If the Workspace's
current run is still working, reviewable, or awaiting finalization recovery,
the older conversation waits until that run is applied or discarded.

### Queue

Sending during work appends a prompt bound to `(machine, workspace,
conversation, source run)` when the visible branch is not successful or a queue
already exists. When all Agents are terminal, select the recommended successful
branch and begin the countdown. Every manual branch change restarts the
countdown. At expiry, continue from the branch visible at that moment.

When no queue exists and the visible branch is already successful, Send is an
explicit immediate decision instead: stop the remaining work, apply that branch,
and start the next turn. This mirrors the TUI and prevents a user who has already
chosen a usable result from being forced through the recommendation countdown.

### Leave Conversation

Workspace changes, Resume changes, and app exit inspect only the branch visible
at that moment. If it is a successful result from the latest turn and Sync Back
is enabled, apply that exact Agent before leaving. Never substitute the
recommended Agent. Resume changes are blocked while an unfinished or unresolved
turn cannot be safely applied; Workspace changes may leave unfinished work
running in the background. Pending submissions and in-flight mutations block a
context change rather than moving their result into the wrong conversation.

### More, Retry, and Synthesis

These operations mutate the current turn's candidate set. They cannot create a
new conversation or disappear behind a stale candidate snapshot. A candidate
added before Synthesis closes must be included in the Synthesis input or be
explicitly marked as outside that completed synthesis pass.

A Retry keeps the previous failed attempt visible until the daemon accepts the
new attempt. A transient request failure must not erase the evidence the user
needs to understand or retry the failure.

## State Ownership

The client state key is:

```text
(machine, workspace, conversation, turn, agent)
```

- Workspace state owns the selected conversation, settings, draft, and latest
  task summary.
- Conversation state owns ordered turn summaries and accepted branch lineage.
- Turn state owns run phase, candidates, queue, and finalization transaction.
- Agent state owns transcript pages, final answer, and diff.
- Conversation view state owns one semantic scroll anchor for the accepted
  trunk and current-turn branch boundary.
- Composer state owns one immutable persisted submission after Send.

The current implementation's `resume history + current prompt + one transcript`
projection is a compatibility source, not the long-term domain model. The
backend must expose conversation lineage explicitly so a client can restore
turns without guessing from a Codex rollout file.

## Loading And Polling

Run summaries and selected-Agent detail are independent streams:

- Summary polling updates phase and every Agent status.
- Detail polling updates only the visible Agent transcript.
- Once a Run owns the visible surface, polling merges forward from memory and
  never replays an older disk snapshot over the live transcript.
- Identical snapshots are not published to the disk cache again; idle polling
  must not create continuous writes or presentation work.
- Switching Agent cancels or supersedes only the obsolete detail request.
- Terminal summary polling stops after every Agent is terminal.
- Detail polling stops after the selected transcript and final message reach
  their authoritative cursors.
- Background Workspaces continue lightweight summary monitoring.

Completion selects the recommended successful Agent once. It does not alter the
selection again after the user manually chooses another branch.

## Scrolling And Rendering

- Use a virtualized native collection view.
- Persist one anchor item and intra-item offset per conversation run, not one
  unrelated page position per Agent.
- Switching branch preserves any historical anchor. If the old branch-only
  anchor does not exist in the new branch, preserve its offset relative to the
  current-turn candidate switcher.
- Follow new content only while the reader is genuinely near the bottom.
- `Jump to Latest` depends on measured distance from real content bottom.
- The collection document height must equal layout content height; stale item
  estimates must never create a blank scroll tail.
- Markdown parsing and height measurement are cached by content revision,
  presentation role, and exact text width.
- Resizing reflows text while preserving the visible semantic anchor.
- Text remains selectable and copyable across user, answer, code, and work rows.

## Visual Language

- Native sidebar, toolbar, inspector, materials, controls, and SF Symbols.
- One uninterrupted conversation background; no nested decorative cards.
- User messages use alignment and a restrained tint.
- Codex answers use strong typography and whitespace, not a large card.
- Work uses secondary typography and compact semantic rows.
- Recommendation uses a star and selection state, never animation or a thick
  side rail.
- Errors appear beside the failed operation, not as unrelated global noise.
- A completed Agent whose candidate workspace no longer exists is labelled
  `Unavailable`; the turn is never presented as ready to use.
- Labels use plain language. Protocol IDs and filesystem internals live only in
  diagnostics.

## Acceptance Scenarios

Every packaged build must be exercised with real pcrd data in these states:

- New Workspace and new conversation.
- Resume with several long turns.
- Primary Agents queued, running, partially complete, failed, and killed.
- Synthesis pending, running, and partially failed.
- Review with dozens of candidates and a manually selected non-recommended
  branch.
- More and live Retry before Synthesis closes.
- Queue editing, countdown, manual branch switch, and automatic dispatch.
- Use Result, continuation, ambiguous sync recovery, and cleanup retry.
- Workspace and Computer switching during foreground and background work.
- App quit and relaunch during submission, running, review, and finalization.
- Narrow, default, and wide windows.
- Long Chinese and English Markdown, code, links, tables, formulas, and
  selectable multiline text.
- Scroll at top, middle, near bottom, and exact bottom while live content grows.

Passing unit tests is necessary but not sufficient. The signed app in
`/Applications` must be used against the live daemon, and the visible state must
match the authoritative run and transcript responses.
