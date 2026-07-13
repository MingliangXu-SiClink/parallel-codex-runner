<div align="center">

# parallel-codex-runner

**Run multiple isolated Codex candidates. Watch them live, review their diffs, and continue from the result you trust.**

English · [简体中文](README.zh-CN.md)

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](#requirements)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[Quick Start](#quick-start) · [TUI Workflow](#tui-workflow) · [How It Works](#how-it-works) · [Safety](#safety-boundaries) · [Reference](#cli-reference)

<code>pcr "fix the failing tests" -n 8</code>

</div>

`parallel-codex-runner` (PCR) is a local orchestration CLI for running the same task through several independent Codex candidates. Each candidate works in its own workspace and temporary `CODEX_HOME`; PCR keeps the evidence, recommends a result, and lets you decide what reaches the original workspace.

Use it in either of two ways:

- Run `pcr` for an interactive TUI with live agent output, review controls, resume, and follow-up conversations.
- Run `pcr "your prompt"` for a one-shot CLI run that automatically syncs the recommended successful candidate.

> [!IMPORTANT]
> PCR requests Codex's full-access mode when the installed CLI supports it. Candidate workspaces isolate agents from one another, but they are **not security sandboxes**. Only run prompts and repositories you trust.

## Motivation

In practice, two Codex runs with the same prompt can differ substantially: one may inspect the surrounding code and finish the workflow, while another may stop early or return a shallow patch. PCR is designed to make that variance inspectable rather than forcing you to bet everything on one run.

The motivation is related to [openai/codex#30364](https://github.com/openai/codex/issues/30364), which reports GPT-5.5 reasoning-token clustering around fixed values such as 516, 1034, and 1552 and asks whether reasoning budgets, routing, truncation, or scheduler behavior may contribute to degraded performance on complex tasks. The issue does not prove hidden truncation, and PCR does not attempt to diagnose the model. It provides a practical response: run several candidates, compare their actual work, and keep the branch you trust.

## Features

| Capability | What it provides |
| --- | --- |
| Parallel candidates | Run multiple `codex exec` processes concurrently, with an optional concurrency limit or serial mode. |
| Interactive TUI | Edit run settings, submit multiline prompts, follow every Agent, and switch panes with left/right. |
| Live chronological Detail | See Codex reasoning/messages and command start/completion events in their original order without flooding the pane with full command output. |
| Candidate review | Inspect a complete patch, reject weak candidates, retry failed or killed Agents, add more candidates, or accept immediately. |
| Branch continuation | Continue the conversation from the successful Agent currently displayed in the TUI. |
| Recommendation heuristics | Recommend by observed reasoning tokens or duration without overriding the Agent you explicitly select in the TUI. |
| Git-aware isolation | Use detached Git worktrees and preserve staged, unstaged, deleted, untracked, and committed Agent state. |
| Resume support | Load prior Codex conversation history, fan it out to candidates, and promote the selected session back. |
| Model-aware effort | Select a model and only choose reasoning-effort levels that model supports. |
| Storage safeguards | Estimate aggregate workspace and metadata size before TUI runs, confirm estimates over 5 GiB, and check free disk space. |
| Persistent prompt history | Browse editable input history scoped to the current workspace and Codex session. |
| Background completion alerts | Ring the terminal bell on the first successful Agent and when all Agents finish while the TUI is unfocused. |
| CJK-friendly terminal UI | Ship a patched Textual 8.2.8 with local Chinese input, grapheme, selection, and reflow fixes. |

## Requirements

- Python 3.10 or newer
- An installed and authenticated Codex CLI available as `codex`
- Git for the preferred worktree-based copy path
- A terminal environment supported by Textual; PCR's process-control path is primarily designed for macOS and Linux

Textual itself is vendored in this repository. A separate TUI extra is not required.

## Installation

Install from a checkout:

```bash
python3 -m pip install .
```

For development:

```bash
python3 -m pip install -e .
```

Optional richer non-TUI CLI output adds Loguru and tqdm:

```bash
python3 -m pip install -e '.[pretty]'
```

Verify the installation:

```bash
pcr --help
```

You can also run the compatibility script directly:

```bash
python3 parallel_codex_runner.py "fix the failing tests"
```

## Quick Start

### Interactive TUI

Start PCR without a prompt:

```bash
pcr
```

Set the next run to eight Agents with four running at once, then enter a normal prompt:

```text
/numofagents 8
/maxparallel 4
fix the failing tests and add regression coverage
```

The configuration panel also lets you edit `AGENTS`, `EXECUTION`, `MAX_PARALLEL`, `RECOMMEND_BY`, `MODEL`, `EFFORT`, `SYNC_BACK`, `KEEP_WORKSPACES`, and `RESUME` directly.

### One-shot CLI

Run the default five candidates in the current directory:

```bash
pcr "implement the requested change"
```

Run ten candidates but at most three concurrently:

```bash
pcr "fix the flaky test and add coverage" -n 10 --max-parallel 3
```

Run Agents serially:

```bash
pcr "refactor the migration" -n 4 --serial
```

Inspect candidates without modifying the original workspace:

```bash
pcr "investigate this bug" -n 5 --no-sync-back --keep-workspaces
```

Run against another project or a long prompt file:

```bash
pcr "update the documentation" --workspace /path/to/project
pcr --prompt-file /tmp/prompt.txt -n 8
```

## TUI Workflow

1. Run `pcr` in the target workspace.
2. Adjust the configuration panel or use slash commands.
3. Submit a prompt. PCR estimates storage before creating any candidate workspace.
4. Watch live Agent activity and switch Agents with left/right when the input is empty.
5. Use `/diff`, `/reject`, `/retry`, `/more`, or `/kill` as needed.
6. Use `/accept` to adopt the displayed successful Agent immediately, or submit a follow-up prompt to continue from it.
7. Exit with `/exit` or `Ctrl-C` while the input is empty. Active Agents are stopped and workspace cleanup follows the normal finalization path.

> [!NOTE]
> `RECOMMEND_BY` is a recommendation, not a forced TUI selection. Changing next-run settings does not finalize the current run. Submitting a follow-up prompt, accepting, exiting, or changing workspace/resume context uses the successful Agent currently displayed. In one-shot CLI mode, PCR automatically syncs the recommended candidate.

### Reading the Detail pane

- The pane stays hidden until there is conversation or Agent activity to show.
- User prompts and Codex content use distinct markers and colors.
- Codex thoughts/messages and command lifecycle events are shown chronologically.
- Full command stdout is intentionally omitted from the live pane to keep it responsive; complete run artifacts remain under the run directory.
- Completed Agent titles include elapsed time and reasoning-token information. Increment distributions are summarized by contribution, with lower-frequency intervals grouped as `other`.
- The recommended Agent is marked with `★` and an animated rainbow border.
- Loading a resume session restores its readable conversation history before the next prompt.

### Keyboard and mouse

| Input | Action |
| --- | --- |
| `Enter` | Submit the current prompt or slash command |
| `Shift-Enter` or `Ctrl-J` | Insert a newline |
| `←` / `→` with an empty input | Switch the displayed Agent |
| `↑` / `↓` at the first/last logical line | Browse workspace/session prompt history |
| Mouse wheel | Scroll the Detail pane |
| Mouse drag | Select and copy TUI text |
| `Ctrl-C` | Copy a selection, otherwise clear a non-empty input, otherwise stop/clean up/exit |

### Review commands

| Command | Purpose |
| --- | --- |
| `/accept` | Finalize the displayed successful Agent immediately. |
| `/reject` | Exclude the displayed Agent from recommendations. |
| `/diff` | Toggle the displayed Agent's complete added/modified/deleted file patch. |
| `/kill [agent]` | Stop a running Agent; queued Agents continue normally. |
| `/retry [agent]` | Rerun a failed or killed Agent in a fresh workspace. |
| `/more <n>` | Add fresh candidates for the current question using that run's settings. |

## How It Works

```text
original workspace
    |
    | create isolated candidates outside the workspace
    v
.codex_parallel_runs/<timestamp>/
    workspaces/
        agent_001/  -> codex exec -
        agent_002/  -> codex exec -
        agent_003/  -> codex exec -
    meta/
        agent_001/  -> logs, final message, private CODEX_HOME
        agent_002/
        agent_003/
    |
    | inspect + recommend + select one successful candidate
    v
delete-aware sync to the original workspace
```

For each run, PCR:

1. Chooses a run root outside the target workspace.
2. Creates one isolated workspace and private temporary `CODEX_HOME` per Agent.
3. Runs `codex exec -` or `codex exec resume <session_id> -` in every candidate.
4. Streams structured events into the TUI and records logs, final messages, session ids, duration, and reasoning-token metadata.
5. Recommends one successful candidate according to `RECOMMEND_BY`.
6. Finalizes the selected candidate, syncs it back when enabled, promotes its Codex session, and removes candidate workspaces unless retention is enabled.

PCR does not merge candidates or ask one model to judge another. One candidate is adopted as a whole.

## Workspace and Git Behavior

### Git workspaces

PCR creates detached worktrees with `--no-checkout`, then mirrors the source workspace's index and files. This preserves staged changes, unstaged changes, deletions, and untracked files while avoiding a redundant full checkout.

When a Git candidate is finalized, PCR syncs its files, index, and `HEAD` back with consistency checks. If the selected Agent created commits, the original checked-out branch may advance to that Agent's commit. PCR refuses the operation if the original branch or `HEAD` changed incompatibly while Agents were running.

Git worktrees share the repository's Git object database and administrative metadata. They isolate working trees and per-worktree state, not the entire repository at the storage or security level.

### Non-Git workspaces

PCR creates full copies while preserving symlinks. Sync-back is delete-aware, so a file removed by the selected Agent is also removed from the original workspace.

For both paths, `.git`, `.codex_parallel_runs`, and `.codex_parallel_meta` are excluded from normal file sync.

## Recommendations and Reasoning Tokens

The default strategy is `reasoning_tokens`:

```bash
pcr "fix a difficult bug" --recommend-by reasoning_tokens
```

PCR selects the successful candidate with the highest observed reasoning-token total, using duration and Agent number as deterministic tie-breakers. If every successful candidate reports `N/A`, it falls back to duration.

To recommend the longest successful run instead:

```bash
pcr "explore several approaches" --recommend-by duration
```

Reasoning tokens and duration are heuristics, not quality scores. Use `/diff` and the conversation history before accepting a result. In the TUI, `/reject` removes a candidate from the recommendation pool without deleting its result.

## Resume and Follow-up Conversations

Choose a prior session interactively in one-shot mode:

```bash
pcr --resume "continue the previous task"
```

Or resume a known session id:

```bash
pcr --resume-session-id 019f2dde-d5ab-7473-856b-ab1b8001f6da "continue the task"
```

In the TUI:

```text
/resume
/resume 1
/resume latest
/resume clear
```

PCR copies the selected session state and rollout into each Agent's private `CODEX_HOME` and rebinds its working directory to that Agent workspace. When a candidate is finalized, PCR imports/promotes its session back to the real Codex home and rebinds it to the original workspace when possible.

## Storage Preflight

Before a TUI prompt starts a run, PCR asynchronously estimates:

- all candidate workspace copies;
- copied Codex state and resume rollout data;
- per-Agent metadata and a runtime reserve.

If the aggregate estimate exceeds 5 GiB, PCR asks for confirmation before creating a run directory or workspace copy. Declining ignores the prompt and does not add it to prompt history. Continuing checks the target filesystem's free space; an insufficient disk fails the run before copying. Space from an old candidate workspace that will be cleaned is included as reclaimable capacity.

> [!CAUTION]
> Storage confirmation currently belongs to the interactive TUI path. A one-shot `pcr "prompt"` run starts directly, so place `--runs-dir` on a filesystem with enough capacity and avoid `--keep-workspaces` unless retention is intentional.

## Artifacts

Run records live under `.codex_parallel_runs/<timestamp>/` outside the target workspace by default.

| Path | Contents |
| --- | --- |
| `prompt.txt` | Prompt sent to the candidates |
| `summary.json` | Machine-readable settings, results, recommendation, sync, and cleanup state |
| `BEST_AGENT.txt` | Recommended candidate for the recorded run |
| `BEST_CODEX_SESSION.txt` | Recommended Codex session id, when detected |
| `FINAL_RESULT_WORKSPACE.txt` | Original workspace path when sync-back occurred |
| `reasoning_tokens.tsv` | Per-Agent totals, observed values, and increment distributions |
| `codex_capabilities.json` | Codex CLI flags detected for the run |
| `sample_command.json` | Representative Agent command |
| `meta/agent_*/stdout.log` | Captured candidate stdout |
| `meta/agent_*/stderr.log` | Captured candidate stderr |
| `meta/agent_*/final_message.md` | Candidate final response |
| `meta/agent_*/codex_home/` | Private resumable state; copied support credentials/config are scrubbed after execution |
| `retry_history/agent_*/` | Metadata from superseded retry attempts |

Candidate workspaces are removed after a one-shot run or TUI finalization/exit unless `--keep-workspaces` or `KEEP_WORKSPACES` is enabled. Metadata remains available for inspection.

## Safety Boundaries

- Codex is launched with approval bypass/full workspace access when the installed CLI exposes those flags.
- Workspace isolation is not a container, VM, or operating-system sandbox. Agents still share the host, network access, Codex account, quota, and credentials copied into their private homes for execution.
- Git worktrees share the repository's object database. The selected Agent's commit and index state can be applied during finalization.
- The original working tree is not synced until a successful candidate is finalized. `--no-sync-back` disables that sync.
- Sync-back is delete-aware. Review deletions as carefully as additions.
- PCR never overwrites the original `.git` directory through file sync.
- Temporary Agent support credentials/config are copied rather than symlinked and are scrubbed after execution; resumable state is retained in metadata.
- `/exit` and `Ctrl-C` stop active Agents and run the same workspace cleanup path as a normal run unless workspaces are explicitly retained.
- Review `git status`, `git diff`, and the selected commit before pushing or releasing anything.

## CLI Reference

| Option | Description |
| --- | --- |
| `-n, --num-agents` | Number of candidates; default `5` |
| `--max-parallel` | Maximum concurrent Codex processes |
| `--serial` | Run one candidate at a time |
| `--recommend-by` | Recommendation strategy: `reasoning_tokens` or `duration` |
| `--prompt-file` | Read a UTF-8 prompt file |
| `--workspace` | Target workspace; default current directory |
| `--runs-dir` | Run-record directory; must be outside the workspace |
| `--codex-bin` | Codex executable; default `codex` |
| `--model` | Optional Codex model name |
| `--effort` | Optional model-supported reasoning effort |
| `--resume` | Select a resumable Codex session interactively |
| `--resume-session-id` | Resume a specific Codex session id |
| `--resume-include-non-interactive` | Include `codex exec` sessions in the picker |
| `--no-sync-back` | Do not modify the original workspace |
| `--keep-workspaces` | Keep candidate workspaces after the run |

## TUI Command Reference

| Command | Description |
| --- | --- |
| `/help` | Show every TUI command |
| `/status`, `/config` | Show the current run configuration |
| `/accept` | Finalize the displayed successful Agent |
| `/reject` | Exclude the displayed Agent from recommendations |
| `/retry [agent]` | Rerun a failed or killed Agent |
| `/more <n>` | Add candidates for the current question |
| `/diff` | Toggle the displayed Agent's complete patch |
| `/kill [agent]` | Stop a running Agent |
| `/numofagents <n>` | Set Agent count for the next run |
| `/maxparallel <n\|auto>` | Set or clear the concurrency limit |
| `/serial` | Run Agents one at a time |
| `/parallel` | Run Agents concurrently |
| `/recommendby <duration\|reasoning_tokens>` | Set the recommendation heuristic |
| `/model <name\|clear>` | Set or clear the model |
| `/effort <auto\|level>` | Select a supported reasoning effort |
| `/workspace <path>` | Change the target workspace |
| `/runsdir <path\|clear>` | Set or reset the run-record directory |
| `/codexbin <path>` | Set the Codex executable |
| `/syncback <on\|off>` | Enable or disable sync-back |
| `/keepworkspaces <on\|off>` | Enable or disable workspace retention |
| `/promptfile <path>` | Read and run a UTF-8 prompt file |
| `/resumeinclude <on\|off>` | Include or exclude non-interactive sessions |
| `/resume` | Show resumable sessions |
| `/resume <n\|session>` | Load a listed or explicit session |
| `/resume latest` | Load the latest resumable session |
| `/resume clear` | Start without resume |
| `/clear` | Clear the current view when safe |
| `/exit` | Stop, clean up, and quit |

## Project Layout

| Path | Responsibility |
| --- | --- |
| `parallel_codex_runner.py` | Compatibility entry point for direct execution and older imports |
| `parallel_codex_runner_core/app.py` | CLI orchestration, Agent execution, storage estimation, summaries, and session promotion |
| `parallel_codex_runner_core/codex_cli.py` | Codex capability detection and command construction |
| `parallel_codex_runner_core/codex_models.py` | Model cache and compatible effort selection |
| `parallel_codex_runner_core/workspace.py` | Workspace estimation, worktrees, copy, cleanup, and sync-back |
| `parallel_codex_runner_core/tui_textual.py` | Interactive Textual TUI and review workflow |
| `parallel_codex_runner_core/prompt_history.py` | Persistent workspace/session prompt history |
| `parallel_codex_runner_core/diffing.py` | Full delete-aware workspace diff generation |
| `parallel_codex_runner_core/paths.py` | Path and run-root helpers |
| `parallel_codex_runner_core/models.py` | Shared result and session dataclasses |
| `vendor/textual/` | Vendored Textual source, license, tests/docs, and PCR Unicode patches |
| `tests/` | PCR regression tests |

## Development

Run the PCR test suite:

```bash
python3 -m unittest discover -s tests
python3 -m compileall -q parallel_codex_runner.py parallel_codex_runner_core
git diff --check
```

When changing the vendored Textual patches, also run the focused upstream tests (requires pytest):

```bash
PYTHONPATH=vendor/textual/src python3 -m pytest -m 'not syntax' \
  vendor/textual/tests/input vendor/textual/tests/text_area
```

See [`vendor/textual/PCR_PATCHES.md`](vendor/textual/PCR_PATCHES.md) for the pinned upstream revision and local patch inventory.

## Contributing

Focused issues and pull requests are welcome. Please describe the user-visible behavior, keep unrelated refactors out of the change, and add regression coverage proportional to the affected workflow. Run the test commands above before submitting.

## License

PCR is released under the [MIT License](LICENSE). Vendored Textual retains its upstream [MIT license](vendor/textual/LICENSE).
