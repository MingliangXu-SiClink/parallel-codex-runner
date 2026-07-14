# Parallel Codex Runner Plugin

English | [简体中文](README.zh-CN.md)

This directory turns [Parallel Codex Runner](../../README.md) into a local Codex App plugin. It lets Codex start several isolated candidates, follow their work, inspect complete patches, stop or retry attempts, and finalize only the branch you approve.

The plugin is an adapter, not a second PCR implementation. Agent execution, Git worktrees, session promotion, sync-back, and cleanup remain in `parallel_codex_runner_core` at the repository root.

## Install

Requirements:

- Python 3.10 or newer
- an installed and authenticated `codex` CLI
- the PCR repository checked out locally

From the repository root, install PCR and its MCP server:

```bash
python3 -m pip install -e .
python3 plugins/parallel-codex-runner/scripts/check_runtime.py
```

Register this repository as a local marketplace:

```bash
codex plugin marketplace add "$PWD"
```

Restart the Codex App, open **Plugins**, choose the **Personal** marketplace, and install **Parallel Codex Runner**. A CLI installation is also available:

```bash
codex plugin add parallel-codex-runner@personal
```

Start a new Codex thread after installing or updating the plugin so its Skill and MCP tools are loaded cleanly.

## Use

Open the project you want to change and ask Codex naturally:

```text
Use Parallel Codex Runner to let five Agents fix this test failure.
Show me the successful patches and do not sync anything until I choose.
```

For a resumed conversation:

```text
List resumable sessions for this project, then run three isolated candidates
from the session I select.
```

The normal workflow is:

1. PCR estimates the storage needed and starts isolated candidates.
2. Codex follows the candidates until the batch settles.
3. Codex compares their final responses and complete workspace diffs, then recommends one candidate with reasons.
4. You confirm which Agent to accept, or ask to reject, retry, or add candidates.
5. Only the successful Agent you confirm is synced to the original workspace.

Candidate workspaces stay available throughout this review automatically.
`keep_workspaces=true` is only needed when you also want them retained after
finalization; it is not required for Codex to compare candidates.

Use `pcr_continue_from_agent` when the next request should begin from a selected
Agent's workspace and promoted Codex conversation in one operation.

Runs and additional candidate batches estimated above 5 GiB require a separate confirmation. If available disk space is below the estimate, the operation fails before creating candidate workspaces.

## Tools

| MCP tool | Purpose |
| --- | --- |
| `pcr_start_run` | Start candidates in review mode without immediate sync-back. |
| `pcr_wait_for_run` | Wait briefly and return status plus new chronological events. |
| `pcr_get_run` / `pcr_get_agent` | Inspect run and Agent state, messages, logs, and tokens. |
| `pcr_get_diff` | Read the complete delete-aware patch in lossless pages. |
| `pcr_kill_agent` / `pcr_stop_run` | Stop one running candidate or the whole active batch. |
| `pcr_reject_agent` | Exclude or restore a candidate in the recommendation pool. |
| `pcr_retry_agent` / `pcr_add_agents` | Retry an unsuccessful candidate or add more candidates. |
| `pcr_accept_agent` | Finalize one successful Agent, sync if enabled, and clean up. |
| `pcr_continue_from_agent` | Accept an Agent and start the next prompt from its promoted session. |
| `pcr_recover_finalization` | Resolve a sync whose completion became ambiguous after a crash. |
| `pcr_discard_run` | Abandon an unaccepted run and clean its candidates. |
| `pcr_cleanup_expired_runs` | Apply configured TTL, retention, and storage cleanup. |
| `pcr_list_resume_sessions` | Find Codex sessions that can seed a new run. |
| `pcr_get_resume_history` | Read a selected session's visible conversation in pages. |
| `pcr_list_models` | List cached models and compatible reasoning efforts. |
| `pcr_list_runs` | Recover retained or interrupted plugin runs after restart. |

Runs execute in detached worker processes. Codex may recycle the MCP server
without interrupting active Agents, and multiple Codex windows can share the
plugin state directory. Short state locks and per-run operation locks prevent
conflicting writes and finalization races.

## Data And Safety

Plugin control state is stored in the platform user-state directory, or under `PCR_PLUGIN_DATA` when that environment variable is set. Candidate workspaces and Agent metadata use PCR's normal `.codex_parallel_runs` location outside the target workspace.

The plugin writes an internal marker into each run directory and validates the workspace, run root, candidate path, and marker before diffing, syncing, or deleting data. Completed diffs are persisted before candidate cleanup, so they remain reviewable after a restart.

The defaults are a six-hour active-run TTL, seven-day retained-artifact period,
and 20 GiB plugin storage quota. Override them with
`PCR_PLUGIN_RUN_TTL_SECONDS`, `PCR_PLUGIN_RETENTION_SECONDS`, and
`PCR_PLUGIN_STORAGE_QUOTA_BYTES`. Explicit stop and discard actions remain the
fastest way to release resources. `keep_workspaces=true` retains both candidate
workspaces and their isolated `meta/agent_*/codex_home` directories; disabling
it removes both.

Finalization uses a durable journal. If PCR cannot prove whether a sync finished,
it will not sync a second time. Inspect the original workspace and resolve the
run with `pcr_recover_finalization`.

PCR isolates working trees, not host access. Agents still share the machine, network, Codex account, quota, and possibly the Git object database. Review a candidate's patch before accepting it.

## Development

Validate the plugin and its Skill from the repository root:

```bash
python3 -m unittest tests.test_plugin_package tests.test_plugin_runtime
python3 -m compileall -q parallel_codex_runner_core plugins/parallel-codex-runner/scripts
git diff --check
```

The repository marketplace is defined in [`.agents/plugins/marketplace.json`](../../.agents/plugins/marketplace.json). The full project suite remains `python3 -m unittest discover -s tests`. Do not create another Git repository inside this plugin directory.

## Troubleshooting

- **MCP server cannot start:** run `python3 -m pip install -e .` again. The plugin-local launcher discovers a compatible absolute Python interpreter; set `PCR_PYTHON` when the App must use a specific environment.
- **Run continues after the MCP server restarts:** this is intentional. Use `pcr_stop_run` or `pcr_discard_run`; TTL cleanup handles abandoned workers.
- **Codex CLI not found:** ensure the same environment can run `codex --version`.
- **Run interrupted after restart:** inspect it with `pcr_list_runs`, then retry eligible Agents or discard the retained run.
- **Workspace was not changed:** this is expected until `pcr_accept_agent` finalizes a successful candidate with sync-back enabled.
