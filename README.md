<div align="center">

# parallel-codex-runner

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](#requirements)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[Installation](#installation) · [Quick Start](#quick-start) · [How It Works](#how-it-works) · [Safety](#safety-model) · [CLI](#cli-reference)

Run several isolated Codex agents on the same task, keep the evidence, and sync back one winning workspace.

<code>pcr "fix the failing tests" -n 8</code>

</div>

`parallel-codex-runner` is a tiny local CLI for multi-shot Codex work. It is useful when a task is important enough that one Codex attempt is too random, but not important enough to hand-build a queue, a judge, and a merge system.

## Why

Codex can sometimes fall into a low-quality "dumb mode": it stops early, misses nearby context, or returns a shallow patch. The same prompt, run again in a fresh workspace, may produce a much better result.

This project is a practical workaround for that variance. It runs independent Codex attempts, records what happened, picks a winner, and syncs only that candidate back.

Related upstream issue: [openai/codex#30364](https://github.com/openai/codex/issues/30364), which discusses Codex reasoning-token clustering at fixed boundaries and possible degraded performance on complex tasks.

## Highlights

- **Interactive TUI**: run `pcr`, type prompts, use slash commands, and switch agent panes with left/right.
- **Parallel candidates**: run many `codex exec` attempts at once, or serialize them with `--serial`.
- **Real isolation**: every candidate gets its own workspace and copied temporary `CODEX_HOME`.
- **Git-aware copies**: Git workspaces use `git worktree` first, then mirror dirty, deleted, and untracked files.
- **Delete-aware sync**: if the winning agent deletes a file, the original workspace loses it too.
- **Simple selection**: choose the winner by observed reasoning tokens or runtime.
- **Resume support**: continue from an existing Codex session and promote the winning session back.
- **Candidate controls**: accept, reject, retry, or add more candidates without leaving the TUI.
- **Workspace diff review**: inspect every added, modified, and deleted file before accepting an agent.
- **Background bells**: receive a terminal bell on the first success and when all candidates finish while the TUI is unfocused.
- **Hackable TUI stack**: Textual 8.2.8 is vendored in the repository, including local CJK and grapheme-aware editor fixes.

## Installation

Requirements:

- Python 3.10+
- Codex CLI available as `codex`
- Git for the best workspace-copy path
- Textual's runtime dependencies, installed automatically by this package; Textual itself is vendored

Install from a checkout:

```bash
python3 -m pip install .
```

For development and nicer terminal output:

```bash
python3 -m pip install -e '.[pretty]'
```

You can also run the script directly:

```bash
python3 parallel_codex_runner.py "fix the failing tests"
```

## Quick Start

Start the interactive TUI:

```bash
pcr
```

Inside the TUI:

```text
/numofagents 8
/maxparallel 4
/bestby duration
/diff
/reject
/retry
/more 3
/accept
/resume
/resume 1
fix the failing tests
```

Use left/right to switch between agent panes while a run is active.

Use up/down in the prompt editor to browse persistent input history for the active workspace and Codex session. An edited recalled prompt becomes the current draft; multiline prompts retain normal vertical cursor movement until the first or last logical line.

After a run completes, run-setting changes such as agent count, execution mode, and model apply to the next run and do not select a candidate. Submitting the next prompt, exiting, or switching workspace/resume context finalizes the currently displayed successful agent. `best_by` remains a recommendation rather than an automatic TUI selection.

Use `/diff` to review the displayed agent's complete workspace patch. `/reject` removes it from the `best_by` recommendation, while `/accept` finalizes it immediately. Failed or killed agents can be rerun with `/retry`, and `/more <n>` appends fresh candidates for the same question using that run's original settings.

Run the default five candidates in the current directory:

```bash
pcr "implement the requested change"
```

Run more candidates:

```bash
pcr "fix the flaky test and add coverage" -n 10
```

Limit concurrency:

```bash
pcr "refactor the API client" -n 20 --max-parallel 5
```

Keep candidates for manual inspection and do not touch the original workspace:

```bash
pcr "investigate this bug" -n 5 --no-sync-back --keep-workspaces
```

Run against another project:

```bash
pcr "update the docs" --workspace /path/to/project
```

Use a long prompt file:

```bash
pcr --prompt-file /tmp/prompt.txt -n 8
```

## How It Works

```text
your workspace
    |
    | copied into isolated candidates
    v
.codex_parallel_runs/<timestamp>/workspaces/
    agent_001/  -> codex exec -
    agent_002/  -> codex exec -
    agent_003/  -> codex exec -
    ...
    |
    | select one successful run
    v
sync winning workspace back to your workspace
```

The runner:

1. Creates a run directory outside the target workspace.
2. Creates one candidate workspace per agent.
3. Runs `codex exec -` or `codex exec resume <session_id> -` in each candidate.
4. Captures logs, final messages, Codex session ids, and reasoning-token metadata.
5. Selects one successful candidate.
6. Syncs that workspace back, excluding `.git`, `.codex_parallel_runs`, and `.codex_parallel_meta`.

It does not merge candidates. One candidate wins.

## Choosing The Winner

By default, the runner chooses the successful candidate with the highest observed reasoning-token value:

```bash
pcr "fix a tricky bug" --best-by reasoning_tokens
```

You can instead choose the longest successful run:

```bash
pcr "explore possible fixes" --best-by duration
```

Both are heuristics. Review the final diff before committing.

## Resume

Pick a previous Codex session for this workspace:

```bash
pcr --resume "continue the previous task"
```

Use a known session id:

```bash
pcr --resume-session-id 019f2dde-d5ab-7473-856b-ab1b8001f6da "continue the previous task"
```

Candidate runs use isolated Codex homes. Non-state Codex support files are copied into each temporary home rather than linked back to your real `~/.codex`. After sync, the winning session is imported into the real Codex home and rebound to the original workspace when possible.

## Artifacts

Each run writes metadata under `.codex_parallel_runs/<timestamp>/`.

| Path | Description |
| --- | --- |
| `prompt.txt` | Prompt sent to every candidate |
| `summary.json` | Machine-readable run summary |
| `BEST_AGENT.txt` | Selected candidate number |
| `BEST_CODEX_SESSION.txt` | Selected Codex session id, when detected |
| `FINAL_RESULT_WORKSPACE.txt` | Workspace that received the sync |
| `reasoning_tokens.tsv` | Observed reasoning-token values |
| `codex_capabilities.json` | Detected Codex CLI flags |
| `sample_command.json` | Example command used for an agent |
| `meta/agent_*/stdout.log` | Candidate stdout |
| `meta/agent_*/stderr.log` | Candidate stderr |
| `meta/agent_*/final_message.md` | Candidate final response |
| `meta/agent_*/codex_home/` | Candidate Codex state needed for resume; copied auth/config support files are scrubbed after execution |
| `retry_history/agent_*/` | Metadata and logs from superseded retry attempts |

Candidate workspaces are deleted after a normal synced run. Use `--keep-workspaces` to keep them.

## Safety Model

- The original workspace is changed only after a successful candidate is selected.
- `--no-sync-back` leaves the original workspace untouched.
- Sync is delete-aware, so winner deletions are propagated.
- `.git` is never copied back over the original repository metadata.
- Temporary agent `CODEX_HOME` folders are private and scrub copied support files after each agent exits, while preserving session state needed for resume.
- Run `git diff` after every synced run.

## CLI Reference

| Option | Description |
| --- | --- |
| `-n, --num-agents` | Number of candidates, default `5` |
| `--max-parallel` | Maximum concurrent Codex processes |
| `--serial` | Run one candidate at a time |
| `--best-by, --candidate-by` | Selection strategy: `reasoning_tokens` or `duration` |
| `--prompt-file` | Read prompt from a UTF-8 file |
| `--workspace` | Target workspace, default current directory |
| `--runs-dir` | Directory for run records; must be outside the workspace |
| `--codex-bin` | Codex executable, default `codex` |
| `--model` | Optional Codex model name |
| `--resume` | Choose a resumable Codex session interactively |
| `--resume-session-id` | Resume a specific Codex session id |
| `--resume-include-non-interactive` | Include `codex exec` sessions in the resume picker |
| `--no-sync-back` | Do not modify the original workspace |
| `--keep-workspaces` | Keep candidate workspaces after the run |

Interactive slash commands:

| Command | Description |
| --- | --- |
| `/help` | Show all TUI commands |
| `/status` or `/config` | Show current run configuration |
| `/accept` | Immediately finalize the displayed successful agent |
| `/reject` | Exclude the displayed agent from `best_by` recommendations |
| `/retry [agent]` | Rerun a failed or killed agent in a fresh candidate workspace |
| `/more <n>` | Add candidates for the current question |
| `/diff` | Toggle the displayed agent's complete workspace diff |
| `/kill [agent]` | Stop a running agent; queued agents continue normally |
| `/numofagents <n>` | Set agent count for the next run |
| `/maxparallel <n\|auto>` | Set or clear `--max-parallel` |
| `/serial` | Run one agent at a time |
| `/parallel` | Clear serial mode |
| `/bestby <duration\|reasoning_tokens>` | Set `--best-by` |
| `/model <name\|clear>` | Set or clear `--model` |
| `/workspace <path>` | Set `--workspace` |
| `/runsdir <path\|clear>` | Set or clear `--runs-dir` |
| `/codexbin <path>` | Set `--codex-bin` |
| `/syncback <on\|off>` | Toggle `--no-sync-back` |
| `/keepworkspaces <on\|off>` | Toggle `--keep-workspaces` |
| `/promptfile <path>` | Run a prompt from a UTF-8 file |
| `/resumeinclude <on\|off>` | Toggle `--resume-include-non-interactive` |
| `/resume` | Show recent resumable sessions |
| `/resume <n\|session>` | Resume a listed or explicit session id |
| `/resume latest` | Load the latest resumable Codex session |
| `/resume clear` | Disable resume for the next run |
| `/clear` | Clear the current TUI view |
| `/exit` | Quit |

## Project Layout

| Path | Purpose |
| --- | --- |
| `parallel_codex_runner.py` | Compatibility wrapper for `python3 parallel_codex_runner.py` and old imports |
| `parallel_codex_runner_core/app.py` | CLI orchestration, agent execution, summaries, and session promotion flow |
| `parallel_codex_runner_core/codex_cli.py` | Codex CLI capability detection and command construction |
| `parallel_codex_runner_core/workspace.py` | Workspace copy, `git worktree`, cleanup, and sync-back logic |
| `parallel_codex_runner_core/tui_textual.py` | Interactive Textual TUI |
| `parallel_codex_runner_core/prompt_history.py` | Persistent workspace/session prompt history and draft navigation |
| `parallel_codex_runner_core/diffing.py` | Delete-aware full workspace diff generation |
| `parallel_codex_runner_core/paths.py` | Path and run-directory helpers |
| `parallel_codex_runner_core/models.py` | Dataclasses shared across modules |
| `vendor/textual/` | Vendored Textual 8.2.8 source, upstream tests/docs, license, and PCR Unicode patches |
| `tests/` | Regression tests |

## Development

```bash
python3 -m py_compile parallel_codex_runner.py
python3 -m unittest discover
PYTHONPATH=vendor/textual/src python3 -m pytest -m 'not syntax' vendor/textual/tests/input vendor/textual/tests/text_area
python3 parallel_codex_runner.py --help
```

See [`vendor/textual/PCR_PATCHES.md`](vendor/textual/PCR_PATCHES.md) for the pinned upstream revision and local patch inventory.

## License

MIT. See [LICENSE](LICENSE). Vendored Textual retains its upstream [MIT license](vendor/textual/LICENSE).
