# Agent Preparation Pipeline

Date: 2026-10-05 CST

## Scope And Authorization

The user requested pipelined, bounded preparation instead of preparing every
Agent before any Agent can start, followed by a release. The user separately
approved installation and activation after background validation, with pcrd
activation permitted only when all candidates, synthesis Agents, finalization
and other operations are idle. No foreground QA, QA app, focus activation,
physical input, Android operation, task stop, retry, adoption or resubmission
is authorized by this work.

Target: macOS 0.1.413/build 413 and pcrd 0.1.71. The backend includes and
supersedes the previously staged 0.1.70 receipt/recovery changes. Installing
the client alone does not activate the preparation pipeline.

## Observed Root Cause

The running 0.1.69 initial path prepared Agents in a synchronous loop, then
called `run_all_agents` only after the entire loop finished. The separate
`run_additional_agents` path used the same serial preparation barrier,
including synthesis. Live-added candidates already used individual threaded
preparation inside the execution coordinator, but the initial batch did not.

Read-only events from `pcr-20261004T140156Z-3d27870f` establish the order:

| Event | UTC On 2026-10-04 |
| --- | --- |
| Agent 1 copying | 14:01:58.855906 |
| Agent 1 prepared/queued | 14:02:29.990827 |
| Agent 2 copying | 14:02:29.991326 |
| Agent 2 prepared/queued | 14:02:46.671192 |
| Agent 3 copying | 14:02:46.671990 |
| Agent 3 prepared/queued | 14:03:05.469139 |
| Agent 4 copying | 14:03:05.469575 |
| Agent 4 prepared/queued | 14:03:23.245649 |
| Agent 5 copying | 14:03:23.246640 |
| Agent 5 prepared/queued | 14:03:42.494780 |
| All five `agent_started` events | 14:04:08.184905 to 14:04:08.260360 |

Preparation took about 104 seconds. Agent 1 waited about 72 additional
seconds for the remaining preparation. The subsequent approximately 26-second
shared model-cache warmup is a separate prerequisite; this fix does not claim
to eliminate it or measure real model response latency. An `agent_started`
event alone is not proof that the child or model has performed work.

There is also an independent client classification defect: `canStop` was used
as a lifecycle test. Queued/preparing Agents cannot be stopped yet and have no
result directory, so `workspaceAvailable=false` turned a normal queued Agent
into an orange "unavailable" result. The presentation and result navigator now
use the existing `isRunning` lifecycle predicate, without changing stop/accept
capabilities or hiding terminal errors.

## Implementation

- Initial, live-added, retried and synthesis Agents reuse the existing per-Agent
  isolation/preparation helper and the same coordinator rules. No "prepare all,
  then gather" barrier remains.
- Each run permits at most two expensive preparations at once. Each ready Agent
  enters its existing subprocess lifecycle immediately, independent of other
  preparation completion.
- Existing `max_parallel` lifecycle admission is retained, including preparation
  and execution. `serial` and `max_parallel=1` remain strict. There is no unbounded
  prefetch queue and no claim of a machine-wide disk concurrency limit.
- Shared developer guidance is resolved once under a thread lock. Workspace
  isolation instructions are still constructed separately for every Agent.
- A local preparation failure becomes an explicit failed Agent result; other
  candidates continue. A known candidate whose preparation failed can be retried
  through the existing coordinator, without pretending its command was prepared.
- Cancellation, including repeated coroutine cancellation, asynchronously waits
  for the real preparation writer before releasing ownership or allowing cleanup.
  No late subprocess launch is permitted.
- All candidate tasks are registered before preparation; synthesis still waits
  for the complete candidate stage, including added/retried tasks. Sync-back and
  finalization remain after the full stage, not after the first Agent finishes.
- Source copies preserve the existing live-workspace semantics, uncommitted and
  untracked files, and separate destinations. They are not advertised as atomic
  snapshots. No extra full-workspace common copy or research-source modification
  was introduced.

## Background Verification

Evidence directory:
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/AgentPipeline-20261005`.

The original implementation failed 6 of the initial 9 targeted pipeline tests.
The current 13 targeted cases pass. They block one preparation until a different
Agent's real local child has read stdin and written its own workspace file,
cover initial/additional/synthesis paths, slow-first independence, bounded and
serial admission, preparation failure, same-stage retry, source preservation,
shared-guidance isolation, and single/repeated cancellation writer joining.
These are private local child processes, not user tasks or real model calls.

329 client tests in 39 suites pass in the headless test entry point. The new
parameterized UI test checks six live lifecycle states before a workspace
exists, while existing terminal and navigation tests remain in the suite.
Foreground-only tests were not run; no QA window was opened. The signed build,
packaged math/syntax resource smoke test and icon test passed in the background.

Full source regression: 921 tests and 104 subtests passed. The independently
installed 0.1.71 runtime passed 920 applicable tests and 104 subtests; only the
repository-relative vendored-source location check was excluded, since an
installed wheel correctly loads the vendored module from its own runtime.
All packaged Python files were checked byte-for-byte against their source.
The installed pipeline/cleanup/additional-contract subset separately passed
20 tests. An earlier run exposed a legacy strict `[3,4,3]` start-order assertion:
the new valid order was `[4,3,3]`. The assertion now checks both initial identities
and the later retry separately, while retaining workspace, prompt, isolation,
summary and cleanup checks. Model warmup in these unrelated lifecycle fixtures
is mocked rather than invoking a real Codex app-server.

Three-trial private benchmark uses five isolated local children with fixed
120-ms fixture copy and 150-ms fixture work delays. Median first actual child
work: approximately 0.65 seconds with the old barrier, approximately 0.15 seconds
with either one or two preparation slots. Two preparation slots reduce the
median last-child start to approximately 0.41 seconds, versus approximately
0.65 seconds with one. This is causal scheduling evidence, not a real-workspace
throughput or model-latency promise. Exact observations are preserved in the
three `benchmark-*.json` files, and the source fixture remained unchanged.

## Deployment Status

macOS 0.1.413/build 413 is published in the authenticated relay release catalog,
with its archive SHA-256 verified after publication and through the built-in
download/update verifier. It is installed and background-restarted using the
built-in installer with `activateOnRelaunch=false`; no rollback occurred.
Formal binary SHA-256:
`96548665c5f285aeab4d9824ddae6cc17ad97983dd5e6c1060f2b7bbcdb13fdd`.
The signed archive SHA-256 is
`150c3822f8d97c57dcebda706b7819c0bf1465eccdeb2820382d6974c000f070`.

The formal client PID changed from 36205 to 48174; foreground PID stayed 787
across the actual installation. All baseline Node processes were preserved.
At this client acceptance checkpoint, pcrd remained healthy/paired 0.1.69
(PID 9818), with the same service definition and no active runs/operation.
GPT-6.1 Sol was available. Scheduler activation is a separate, repeatedly gated
step, and is not inferred from the client upgrade.

The 0.1.71 wheel is installed in an independent runtime, SHA-256
`afab697445b50f319ed25945f4daa971c099f58256d8eb486db0ccfd5d91062d`.
The release manifest points to this backend instead of the previously staged
0.1.70, without changing Android, relay or installer artifacts. Actual scheduler
activation results are appended separately below.

## Scheduler Activation 0.1.71

```json
{
  "pcrd_version": "0.1.71",
  "healthy": true,
  "active_runs_before": [],
  "active_runs_after": [],
  "supports_prompt_receipts": true,
  "supports_visual_file_comparison": true,
  "gpt_6_1_sol_available": true,
  "controller_paired": true,
  "baseline_client_and_node_processes_preserved": true,
  "changed_baseline_pids": [],
  "scheduler_restarted": true,
  "client_restarted": false,
  "foreground_before": 787,
  "foreground_after": 787,
  "supersedes_staged_runtime": "0.1.70",
  "bounded_agent_preparation_limit": 2
}
```

## Installed Acceptance And Original Message Recovery

Final read-only acceptance confirms formal macOS 0.1.413 (PID 48174) and actual
healthy/paired pcrd 0.1.71 (PID 48316), using the independent 0.1.71 interpreter
in the LaunchAgent. GPT-6.1 Sol, prompt receipts and visual file comparison are
available. The scheduler update preserved the current client and Node baselines
and foreground PID 787. Source/tests are not being substituted for these live
version, process, health and signature checks.

At 00:28:16 CST, the existing formal client recovered the original retained
submission into `pcr-20261004T162708Z-9104f9b6`. The read-only verifier confirmed
the original command identity, full prompt fingerprint and Agent 7/source Run;
the outbox had been durably handed off, and a target candidate was actually
running. The verifier submitted or retried no task. Its historical baseline
contains processes that exited before this deployment; those changes are
recorded, not attributed to this upgrade. This original recovery satisfies the
old follow-up's closure condition, so paused automation `pcrd-0-1-70` was deleted
after verification rather than resumed or left with obsolete activation steps.

The resulting five-candidate run also gives read-only live pipeline evidence:
Agents 1 and 2 entered copying at 00:27:11 CST; Agent 2 entered its subprocess
lifecycle at 00:28:13 and preparation of Agent 3 began immediately. Agent 1
started at 00:28:26, Agent 3 at 00:28:31, and Agents 4/5 at 00:29:26. Thus the
earlier ready Agent did not wait for the remaining three preparations. These
event times are not claimed as model-response times or as an apples-to-apples
performance comparison with the older run. The stronger actual-child-before-
other-copy-completion condition is separately covered by the private regression
tests and the installed-runtime fixture benchmark.

The normalized live transcript subsequently contains actual command operations
for all five candidates: first at 00:30:04 CST for Agent 1, then 00:30:07/08 for
Agents 3/2 and 00:30:17 for Agents 5/4. This confirms that the recovered run is
doing work, without conflating subprocess startup with first model output. The
observer saved only event metadata, not the user's message or command content.

No real task was stopped, adopted, discarded, manually retried or resent, and
no research directory or running task's private configuration was modified by
the deployment/verifier tools. All debugging remained background/headless;
no QA window, screen automation or physical-device operation occurred.
