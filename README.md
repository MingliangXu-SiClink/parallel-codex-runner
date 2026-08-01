<div align="center">

# parallel-codex-runner

**One task, several independent Codex attempts. Compare the work and keep the result you trust.**

English · [简体中文](README.zh-CN.md)

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](#requirements)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[Why PCR?](#why-pcr) · [Quick Start](#quick-start) · [Codex App Plugin](#codex-app-plugin) · [Using the TUI](#using-the-tui) · [Reference](#reference)

<code>pcr "fix the failing tests" -n 8</code>

</div>

Codex can solve the same task very differently from one run to the next. A strong run may read the surrounding code, test its changes, and finish the job; another may stop after a shallow patch.

`parallel-codex-runner` (PCR) runs the same prompt in several isolated workspaces. You can watch every Agent as it works, inspect its patch, stop or retry candidates, and choose the one that should reach your real workspace.

By default, PCR starts four candidate Agents, then two isolated synthesis Agents review every successful candidate and combine compatible strengths in their own workspaces. Every result remains a complete branch: you can inspect and adopt any successful candidate or synthesis branch as a whole. Set `--synthesis-agents 0` to skip the second stage.

```text
                         +--> AGENT-001 --> conversation + patch
your prompt --> PCR -----+--> AGENT-002 --> conversation + patch
                         +--> AGENT-003 --> conversation + patch
                                      |
                    synthesis Agents (default: 2)
                                      |
                      inspect any successful branch
                                      |
                                      v
                              original workspace
```

> [!IMPORTANT]
> - `pcr` opens the interactive TUI, where you choose the branch.
> - `pcr "your prompt"` runs in one-shot mode and automatically syncs the recommended successful branch.

## Why PCR?

The quality of an agent run is not perfectly repeatable. When a task matters, rerunning it manually and comparing terminal logs is slow, while letting several runs edit the same directory is unsafe.

PCR turns that uncertainty into a reviewable workflow:

- every Agent starts from the same project state;
- Agents work independently and cannot overwrite one another's files;
- output and command activity appear live in one TUI;
- `/diff` shows what an Agent actually changed;
- only the branch you accept is synced back.

PCR is motivated by reports of Codex degradation between runs, including [openai/codex#30364](https://github.com/openai/codex/issues/30364). That issue discusses reasoning-token clustering and asks whether it may be related to weaker performance on complex tasks. It does not prove hidden truncation, and PCR does not claim to diagnose model internals. PCR offers a practical response: run multiple attempts, compare their real work, and decide with evidence.

## Quick Start

### Requirements

- Python 3.10 or newer
- An installed and authenticated [Codex CLI](https://github.com/openai/codex), available as `codex`
- Git, which PCR uses to create isolated local repositories
- macOS or Linux is recommended for process control

Textual is included in this repository, including PCR's Chinese input and terminal-width fixes. No separate TUI extra is required.

### Install

From a checkout, install PCR as a regular wheel package:

```bash
python3 -m pip install --force-reinstall .
```

`pip` builds the wheel and installs it into the active Python environment.
This replaces an existing editable installation, so the installed `pcr`
launcher is independent of the checkout until you install again. In a Conda
environment, the launcher is normally under `$CONDA_PREFIX/bin/pcr`.

For development:

```bash
python3 -m pip install -e .
```

Check that both commands are available:

```bash
codex --version
command -v pcr
pcr --help
```

After pulling a new PCR commit, run the regular installation command again to
replace the installed wheel.

### Start the TUI

Run PCR inside the project you want Codex to edit:

```bash
cd /path/to/project
pcr
```

Type a normal prompt and press `Enter`:

```text
Fix the failing tests, explain the root cause, and add regression coverage.
```

PCR will:

1. estimate how much temporary storage the run needs;
2. create one isolated workspace per Agent;
3. run the same prompt in every workspace;
4. stream each Agent's conversation and command activity;
5. run the configured synthesis Agents to review and combine the candidates;
6. recommend a successful result while leaving the final choice to you.

To change the next run before submitting:

```text
/numofagents 8
/maxparallel 4
/synthesis 3
```

The settings at the top of the TUI are editable too.

PCR remembers these TUI settings separately for each workspace, including the
Agent counts, execution mode, nested-Agent limits, recommendation strategy,
model, effort, Fast mode, queued follow-up delay, sync-back, workspace retention,
and selected resume session. They are stored in the user's PCR state directory
rather than inside the project. Explicit command-line options take precedence
over the remembered workspace values.

## Codex App Plugin

PCR also ships as a local Codex App plugin. The normal interaction is deliberately
short: give Codex a task, let PCR keep the isolated candidates while they run,
then review Codex's comparison and recommendation. Nothing is synced until you
confirm an Agent. The worker, event, and retention machinery stays in the
background so an App or MCP restart does not silently lose an active run.

From the repository root, install the runtime, verify it, register this
repository as a local marketplace, and install the plugin:

```bash
# Install PCR and its MCP server as a regular wheel
python3 -m pip install --force-reinstall .

# Verify the runtime
python3 plugins/parallel-codex-runner/scripts/check_runtime.py

# Register the local plugin marketplace
codex plugin marketplace add "$PWD"

# Install the plugin
codex plugin add parallel-codex-runner@personal
```

The runtime check should report `"ok": true`. Restart the Codex App after
installation, then open a new thread and try:

```text
Use Parallel Codex Runner to run five isolated fixes for this task.
Compare their patches with me and do not sync anything until I choose.
```

The plugin never treats completion or its recommendation as permission to modify the project. Sync-back happens only after a successful Agent is explicitly accepted. See the [plugin guide](plugins/parallel-codex-runner/README.md) for its tools, recovery behavior, and troubleshooting.

## Using the TUI

### Follow the work

The Detail pane appears when there is something useful to show. It keeps the user's prompts, Codex messages, reasoning, and command start/finish events in chronological order. Full command output is left out of the live pane to keep the interface responsive; it is still recorded in the run artifacts.

When the input is empty:

- press `Left` or `Right` to switch Agents;
- use the mouse wheel to scroll;
- drag to select text and press `Ctrl-C` to copy it.

Completed Agent titles show duration and reasoning-token information. PCR marks the currently recommended Agent with `★` and a colored animated border.

### Choose a branch

A useful review loop is:

1. switch between completed Agents;
2. run `/diff` on promising candidates;
3. use `/reject` on results that should not be recommended;
4. leave the TUI on the successful Agent you want;
5. enter a follow-up prompt or run `/accept`.

`RECOMMEND_BY` only controls the suggestion. It does not override the successful Agent you are viewing. When you continue the conversation, accept, exit, or change workspace/resume context, PCR finalizes the displayed successful Agent and uses that branch.

This also works before every Agent has finished. If one result is already good enough, continue from that Agent; PCR stops the remaining work, syncs the chosen branch, and starts the next round from its workspace and Codex session.

### Add a synthesis stage

PCR starts two synthesis Agents by default. Set `SYNTHESIS_AGENTS` in the top panel, or change it before submitting the next prompt:

```text
/synthesis 3
/synthesis off
/synthesismodel gpt-5.6-sol
/synthesiseffort ultra
/synthesisfast off
```

After all first-stage candidates finish, PCR starts three independent synthesis Agents in clean copies of the original workspace. Each one receives references to every successful candidate workspace and final response, with explicit instructions to leave those sources unchanged. When the run resumes an existing Codex conversation, synthesis Agents inherit the same pre-turn session used by first-stage candidates and `/more`. PCR keeps the original request as the Codex user message and appends the review workflow to the effective developer instructions, preserving the guidance already configured for that workspace. For code tasks, each synthesis Agent compares the implementations, integrates compatible strengths in its own workspace, and validates the result. For answer-only tasks, it reconciles the candidate responses into one complete answer.

While first-stage candidates are still running, `SYNTHESIS_AGENTS`, `SYNTHESIS_MODEL`, `SYNTHESIS_EFFORT`, and `SYNTHESIS_FAST` remain editable and their latest values apply to the current run. PCR locks them atomically when synthesis starts, so a late edit cannot appear successful while the background runner uses an older value. The model, effort, and Fast controls use the same choices as `MODEL`, `EFFORT`, and `FAST`. The synthesis model defaults to the Codex configuration, while effort and Fast default to `auto`; these settings do not implicitly copy the candidate-stage overrides.

Successful synthesis Agents are preferred by `RECOMMEND_BY`. If none succeeds, PCR falls back to the successful first-stage candidates. This affects only the recommendation: you can still switch to, continue from, or finalize any successful Agent from either stage. `/more <n>` shares the same pre-turn conversation baseline but remains functionally different: it adds ordinary candidates instead of reviewing existing results. Candidates added with `/more`, and candidate retries requested while the first stage is still open, join that stage before synthesis. If one arrives after synthesis has already started, PCR stops the now-stale synthesis Agents, runs the added candidate work first, then refreshes synthesis from the complete successful candidate set.

If the installed Codex CLI cannot resolve or inject developer instructions safely, PCR marks the synthesis stage as failed and keeps the successful first-stage candidates available instead of silently changing prompt roles.

### Control nested Codex Agents

PCR's candidate Agents and Codex's own subagents are two separate levels of parallelism. Nested Codex Agents are disabled by default so four PCR candidates cannot silently expand into many more active model threads.

Advanced users can enable them from the top panel or with:

```text
/subagents on
/subagentslimit 8
```

`SUBAGENTS_LIMIT` applies independently to every PCR Agent and counts nested subagents, not that Agent's root thread. Nested subagents inside one candidate share its workspace. Larger values may improve delegation on broad tasks, but they can multiply token, CPU, memory, and rate-limit usage quickly.

### Review and control candidates

| Command | What it does |
| --- | --- |
| `/diff` | Show or hide the displayed Agent's complete file patch. |
| `/accept` | Finalize the displayed successful Agent immediately. |
| `/reject` | Remove the displayed Agent from the recommendation pool. |
| `/kill [agent]` | Stop a running Agent. Queued Agents still start normally. |
| `/retry [agent]` | Rerun a failed or killed Agent in a fresh workspace. |
| `/more <n>` | Add more candidates for the current question. |
| `/queue [n]` | Open the follow-up queue editor, optionally at item `n`. |
| `/followupdelay <seconds>` | Set the review delay before a queued follow-up starts. |
| `/synthesis <n\|off>` | Set the pending synthesis stage's Agent count. |

### Manage queued follow-ups

When a follow-up is waiting, run `/queue` to open its editor. Choose an item
from the list, edit its full prompt, use the arrow buttons to change execution
order, or mark it for removal. `APPLY` commits all changes; `CANCEL` or `Esc`
discards them. Opening the editor pauses automatic continuation, and closing it
starts a fresh review period when the queue is ready to run.

`FOLLOW_UP_DELAY` controls that review period in seconds and defaults to `60`.
You can edit it in the top panel at any time, including while Agents are running
or while a countdown is active. The command `/followupdelay 15` and startup
option `--follow-up-delay 15` set the same value; `0` starts the queued question
without a review delay. Switching Agents during the review restarts the full
configured countdown from the newly displayed Agent.

### Continue an earlier Codex conversation

Open the resume picker:

```text
/resume
```

Then choose an entry, load the latest session, or clear the selection:

```text
/resume 1
/resume latest
/resume clear
```

PCR loads the earlier conversation into Detail, gives every candidate an isolated copy of that session, and promotes only the session belonging to the branch you later choose.

### Reuse previous prompts

At the first or last logical line of the input, press `Up` or `Down` to browse prompt history for the current workspace and session. Editing a recalled prompt makes the edited text the newest draft, which matches normal shell-style history behavior.

### Exit

Run `/exit`, or press `Ctrl-C` while the input is empty. PCR stops active Agents and follows the same finalization and workspace-cleanup path used by a completed run.

`Ctrl-C` has context-sensitive behavior:

1. copy selected text when a selection exists;
2. otherwise clear a non-empty input;
3. otherwise stop, clean up, and exit.

## One-shot CLI

Provide a prompt on the command line when you do not need interactive review:

```bash
pcr "fix the flaky test and add regression coverage"
```

One-shot mode runs four candidates and two synthesis Agents by default, recommends one successful result, syncs it to the original workspace, and cleans up the temporary workspaces.

Common examples:

```bash
# Ten candidates, with at most three running at once
pcr "implement the migration" -n 10 --max-parallel 3

# Run candidates one at a time
pcr "refactor the parser" -n 4 --serial

# Run six candidates, then two isolated synthesis Agents
pcr "implement the migration" -n 6 --synthesis-agents 2

# Allow up to eight nested Codex subagents inside each PCR Agent
pcr "audit the whole service" --subagents --subagents-limit 8

# Inspect results without changing the original workspace
pcr "investigate this bug" --no-sync-back --keep-workspaces

# Work on another directory or read a long prompt from a file
pcr "update the documentation" --workspace /path/to/project
pcr --prompt-file /tmp/prompt.txt -n 8
```

> [!NOTE]
> The large-run confirmation described below is available in the TUI and Codex App plugin. One-shot mode starts directly, so use `--runs-dir` on a filesystem with enough free space for large projects.

## Workspaces and Sync-back

PCR creates run data outside the target workspace. Projects on the system
filesystem use `~/.codex_parallel_runs`; projects on another filesystem use
`.codex_parallel_runs` at that filesystem's mount root. `--runs-dir` overrides
this default.

```text
.codex_parallel_runs/<timestamp>/
    workspaces/
        agent_001/
        agent_002/
        ...
    meta/
        agent_001/
        agent_002/
        ...
```

Each Agent receives its own working directory and temporary `CODEX_HOME`.

### When the workspace is a Git repository

PCR creates a detached local clone for each Agent and mirrors the source workspace's current files and index into it. The clone has its own Git config, refs, branches, tags, stash, index, object directory, and reflogs, and PCR removes the source repository as a Git remote. On the same filesystem, Git hard-links immutable committed objects to avoid duplicating their storage; if that is unavailable, PCR falls back to a full local clone. This preserves committed files, staged and unstaged changes, deletions, and untracked files without exposing the original repository's Git administration data to ordinary Agent Git commands.

When an Agent is finalized, PCR performs consistency checks, imports the objects required by that Agent's selected `HEAD` and index, then applies its files, index, and `HEAD` to the original repository. If the Agent created commits, the original checked-out branch may advance to the selected commit. Agent-only branches, tags, stashes, reflogs, and local config are not copied back. PCR refuses the sync when the original branch changed incompatibly during the run.

### When the workspace is not a Git repository

PCR creates full directory copies while preserving symlinks. Sync-back is delete-aware: if the selected Agent removed a file, PCR removes it from the original workspace too.

### Cleanup and retention

Candidate and synthesis workspaces are removed after finalization or exit unless `--keep-workspaces` is enabled. Metadata, logs, and resumable session state remain in the run directory.

Use `--no-sync-back` when you want to inspect a run without changing the original workspace.

## Recommendations

PCR can recommend successful candidates in two ways:

- `reasoning_tokens` (default): prefer the highest observed reasoning-token total;
- `duration`: prefer the longest successful run.

Duration and token count are heuristics, not quality scores. A larger number does not mean a better patch. The recommendation is a starting point for review, and `/diff` is usually the more useful evidence.

The TUI also summarizes positive reasoning-token increments. When there are many interval sizes, it keeps the four largest contributors and groups the rest under `other`.

## Storage and Safety

### Large-run storage check

Before a TUI run creates any workspace, PCR estimates the combined size of:

- all candidate workspace copies;
- copied Codex state and resume data;
- per-Agent metadata and runtime reserve.

If the estimate is over 5 GiB, PCR asks whether to continue. If you continue, it checks the target filesystem's free space first. Declining leaves the workspace untouched and does not add the prompt to history.

### Safety notes

> [!CAUTION]
> Workspace isolation prevents Agents from editing the same working tree. It is not a container, virtual machine, or operating-system sandbox.

- PCR requests Codex's full-access/approval-bypass mode when the installed CLI supports it.
- Agents still share the host, network, Codex account, and quota. Git clones may hard-link immutable committed objects on the same filesystem, while writable objects and all Git administration data remain per Agent.
- Support credentials and configuration are copied into temporary Agent homes for execution, then scrubbed; resumable state remains in metadata.
- Nested Codex Agents are disabled by default. When enabled, subagents within one candidate share that candidate's workspace and can multiply resource usage.
- Sync-back includes deletions and may include the selected Agent's commits and index state.
- `.git`, `.codex_parallel_runs`, and `.codex_parallel_meta` are excluded from ordinary file copying.

Run PCR only on prompts and repositories you trust. Before pushing or releasing a result, inspect `git status`, `git diff`, and the selected commit.

## Reference

<details>
<summary><strong>CLI options</strong></summary>

| Option | Description |
| --- | --- |
| `-n, --num-agents` | Number of candidates; default `4`. |
| `--synthesis-agents` | Isolated review-and-synthesis Agents started after candidates finish; default `2`; use `0` to disable. |
| `--follow-up-delay` | TUI review delay before a queued question starts from the selected Agent; default `60` seconds; `0` starts immediately. |
| `--max-parallel` | Maximum number of concurrent Codex processes. |
| `--subagents`, `--no-subagents` | Enable or disable nested Codex Agents; disabled by default. |
| `--subagents-limit` | Maximum nested subagents per PCR Agent when enabled; default `8`. |
| `--serial` | Run one candidate at a time. |
| `--recommend-by` | Recommend by `reasoning_tokens` or `duration`. |
| `--prompt-file` | Read a UTF-8 prompt file. |
| `--workspace` | Target workspace; default is the current directory. |
| `--runs-dir` | Run-data directory; it must be outside the workspace. |
| `--codex-bin` | Codex executable; default is `codex`. |
| `--model` | Codex model name. |
| `--effort` | Reasoning effort supported by the selected model. |
| `--fast`, `--no-fast` | Force Fast or Standard mode; omitted inherits Codex configuration and is shown as `AUTO (FAST)`, `AUTO (STANDARD)`, or the configured tier. Fast mode is model-dependent and consumes credits faster. |
| `--synthesis-model` | Model used only by synthesis Agents; omitted or `default` uses the Codex configuration. |
| `--synthesis-effort` | Effort used only by synthesis Agents; omitted or `auto` uses that model's default. |
| `--synthesis-fast`, `--no-synthesis-fast` | Force Fast or Standard mode for synthesis Agents; omitted uses the configured service tier. |
| `--resume` | Choose a resumable Codex session interactively. |
| `--resume-session-id` | Resume a specific session ID. |
| `--resume-include-non-interactive` | Include `codex exec` sessions in the picker. |
| `--no-sync-back` | Do not modify the original workspace. |
| `--keep-workspaces` | Keep candidate workspaces after the run. |

</details>

<details>
<summary><strong>All TUI commands</strong></summary>

| Command | Description |
| --- | --- |
| `/help` | Show all TUI commands. |
| `/status`, `/config` | Show the current run configuration. |
| `/accept` | Finalize the displayed successful Agent. |
| `/reject` | Exclude the displayed Agent from recommendations. |
| `/retry [agent]` | Rerun a failed or killed Agent. |
| `/more <n>` | Add candidates for the current question. |
| `/queue [n]` | Edit, remove, or reorder queued follow-ups; optionally select item `n`. |
| `/followupdelay <seconds>` | Set the queued follow-up review delay; `0` starts immediately. |
| `/synthesis <n\|off>` | Set the pending synthesis stage's Agent count. |
| `/diff` | Toggle the displayed Agent's complete patch. |
| `/kill [agent]` | Stop a running Agent. |
| `/numofagents <n>` | Set the Agent count for the next run. |
| `/maxparallel <n\|auto>` | Set or clear the concurrency limit. |
| `/subagents <on\|off>` | Enable or disable nested Codex Agents for the next run. |
| `/subagentslimit <n>` | Set the nested Agent limit for each PCR Agent. |
| `/serial` | Run Agents one at a time. |
| `/parallel` | Run Agents concurrently. |
| `/recommendby <duration\|reasoning_tokens>` | Set the recommendation heuristic. |
| `/model <name\|clear>` | Set or clear the model. |
| `/effort <auto\|level>` | Select a supported reasoning effort. |
| `/fast <on\|off\|auto>` | Select Fast, Standard, or inherited Codex speed; `auto` shows the inherited tier in parentheses. |
| `/synthesismodel <name\|default>` | Set the model before the pending synthesis stage starts. |
| `/synthesiseffort <auto\|level>` | Set reasoning effort before the pending synthesis stage starts. |
| `/synthesisfast <auto\|on\|off>` | Set Fast mode before the pending synthesis stage starts. |
| `/workspace <path>` | Change the target workspace. |
| `/runsdir <path\|clear>` | Set or reset the run-data directory. |
| `/codexbin <path>` | Set the Codex executable. |
| `/syncback <on\|off>` | Enable or disable sync-back. |
| `/keepworkspaces <on\|off>` | Enable or disable workspace retention. |
| `/promptfile <path>` | Read and run a UTF-8 prompt file. |
| `/resumeinclude <on\|off>` | Include or exclude non-interactive sessions. |
| `/resume` | Show resumable sessions. |
| `/resume <n\|session>` | Load a listed or explicit session. |
| `/resume latest` | Load the latest resumable session. |
| `/resume clear` | Start without a resumed session. |
| `/clear` | Clear Detail when no run result would be lost. |
| `/exit` | Stop active Agents, clean up, and quit. |

</details>

<details>
<summary><strong>Keyboard and mouse</strong></summary>

| Input | Action |
| --- | --- |
| `Enter` | Submit the prompt or slash command. |
| `Shift-Enter` or `Ctrl-J` | Insert a newline. |
| `Left` / `Right` with empty input | Switch Agents. |
| `Up` / `Down` at the first/last logical line | Browse prompt history. |
| Mouse wheel | Scroll Detail. |
| Mouse drag | Select text. |
| `Ctrl-C` | Copy, clear input, or exit according to context. |

</details>

## Run Artifacts

Run records stay under `.codex_parallel_runs/<timestamp>/` by default.

| Path | Contents |
| --- | --- |
| `prompt.txt` | Original user prompt sent to candidate and synthesis Agents. |
| `synthesis_context.md` | Original request and paths to successful candidate workspaces and responses. |
| `synthesis_instructions.txt` | Internal developer instructions used by second-stage synthesis Agents. |
| `summary.json` | Settings, results, recommendation, sync, and cleanup state. |
| `BEST_AGENT.txt` | Recommended Agent for the recorded run. |
| `BEST_CODEX_SESSION.txt` | Recommended Codex session ID, when available. |
| `FINAL_RESULT_WORKSPACE.txt` | Original workspace path after sync-back. |
| `reasoning_tokens.tsv` | Per-Agent token totals and increment distribution. |
| `meta/agent_*/stdout.log` | Captured Codex stdout. |
| `meta/agent_*/stderr.log` | Captured Codex stderr. |
| `meta/agent_*/final_message.md` | Agent's final response. |
| `meta/agent_*/codex_home/` | Private resumable Codex state. |
| `retry_history/agent_*/` | Metadata retained from superseded retries. |

## Development

Use a regular wheel installation for normal use
(`python3 -m pip install --force-reinstall .`). PCR does not copy its own source
into temporary runtime snapshots. Python keeps
already imported modules in memory, so an active CLI process continues with the
code it loaded at startup. The TUI eagerly imports its PCR and vendored Textual
dependencies and rejects explicit reloads of those modules.

An editable installation (`python3 -m pip install -e .`) follows the same
process-lifetime rule: restart PCR to pick up source changes. Each plugin run
owns one persistent worker. Initial candidates, additional candidates, retries,
and finalization are sent to that worker over filesystem IPC, so later source
edits cannot replace the implementation halfway through a run. If that worker
crashes, PCR reports that the run can no longer continue instead of starting a
replacement from a different source version.

Run the project checks:

```bash
python3 -m unittest discover -s tests
python3 -m compileall -q parallel_codex_runner.py parallel_codex_runner_core
git diff --check
```

Vendored Textual behavior is covered by the focused compatibility tests:

```bash
python3 -m unittest tests.test_vendored_textual
```

See [`vendor/textual/PCR_PATCHES.md`](vendor/textual/PCR_PATCHES.md) for the pinned revision, retained source layout, and local patch inventory.

<details>
<summary><strong>Project layout</strong></summary>

| Path | Responsibility |
| --- | --- |
| `parallel_codex_runner.py` | Package entry point and compatibility imports. |
| `parallel_codex_runner_core/app.py` | CLI orchestration, Agent execution, summaries, and session promotion. |
| `parallel_codex_runner_core/runtime_pinning.py` | TUI and plugin-worker module preloading and reload protection. |
| `parallel_codex_runner_core/synthesis.py` | Second-stage context generation and recommendation priority. |
| `parallel_codex_runner_core/tui_textual.py` | Interactive TUI and review workflow. |
| `parallel_codex_runner_core/workspace.py` | Workspace estimation, isolated Git clones, copying, cleanup, and sync-back. |
| `parallel_codex_runner_core/codex_cli.py` | Codex capability detection and command construction. |
| `parallel_codex_runner_core/codex_models.py` | Model cache and compatible effort selection. |
| `parallel_codex_runner_core/prompt_history.py` | Persistent prompt history. |
| `parallel_codex_runner_core/workspace_config.py` | Per-workspace TUI configuration persistence. |
| `parallel_codex_runner_core/diffing.py` | Delete-aware workspace diff generation. |
| `parallel_codex_runner_core/models.py` | Shared run and session data models. |
| `parallel_codex_runner_core/plugin_runtime.py` | Persistent review-mode controller for plugin runs. |
| `parallel_codex_runner_core/plugin_mcp.py` | Local MCP tools used by the Codex App plugin. |
| `parallel_codex_runner_core/plugin/` | Durable state, indexed events, persistent worker IPC, and artifact validation. |
| `plugins/parallel-codex-runner/` | Plugin manifest, Skill, runtime check, and plugin documentation. |
| `.agents/plugins/marketplace.json` | Repository-local Codex plugin marketplace. |
| `vendor/textual/` | Vendored Textual and PCR's terminal-input patches. |
| `tests/` | Regression tests. |

</details>

## Contributing

Issues and pull requests are welcome. Describe the user-visible problem, keep unrelated refactors out of the change, and add regression tests for the workflow you touched.

## License

PCR is available under the [MIT License](LICENSE). Vendored Textual retains its upstream [MIT license](vendor/textual/LICENSE).
