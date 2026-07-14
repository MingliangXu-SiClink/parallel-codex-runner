# Parallel Codex Runner 插件

[English](README.md) | 简体中文

这个目录把仓库根目录中的 [Parallel Codex Runner](../../README.zh-CN.md) 接入 Codex App。安装后，Codex 可以启动多个隔离候选、查看实时进展和完整代码差异、停止或重试 Agent，并且只在你确认后采用其中一条分支。

插件只是接入层，不会复制一套 PCR 实现。Agent 执行、Git worktree、会话提升、回写与清理仍由仓库根目录的 `parallel_codex_runner_core` 统一负责。

## 安装

运行要求：

- Python 3.10 或更高版本
- 已安装并认证的 `codex` CLI
- 本地已检出 PCR 仓库

在仓库根目录安装 PCR 与 MCP Server：

```bash
python3 -m pip install -e .
python3 plugins/parallel-codex-runner/scripts/check_runtime.py
```

把当前仓库注册为本地插件市场：

```bash
codex plugin marketplace add /absolute/path/to/parallel-codex-runner
```

重启 Codex App，进入 **Plugins**，在 **Personal** Marketplace 中安装 **Parallel Codex Runner**。也可以通过命令安装：

```bash
codex plugin add parallel-codex-runner@personal
```

首次安装或更新后，请新建一个 Codex 对话，使 Skill 和 MCP 工具从干净的上下文加载。

## 使用

打开准备修改的项目，然后直接告诉 Codex：

```text
使用 Parallel Codex Runner，让五个 Agent 分别修复这个测试失败。
先向我展示成功候选的 Patch，在我选择前不要回写。
```

需要延续旧对话时，可以输入：

```text
列出这个项目可恢复的 Codex 会话，等我选择后，
基于该会话运行三个隔离候选。
```

完整流程如下：

1. PCR 估算所需空间并启动隔离候选。
2. Codex 持续查看候选进度，直到这一批任务结束。
3. Codex 比较最终回复与完整工作区 Diff，说明理由并推荐一个候选。
4. 你确认采用哪个 Agent，也可以要求排除、重试或增加候选。
5. 只有经过你确认的成功 Agent 才会被回写到原始工作区。

等待你审查期间，候选工作区会自动保留。只有希望采用结果后仍保留这些目录时，
才需要设置 `keep_workspaces=true`；Codex 比较候选时不需要开启它。

下一项需求需要沿用某个 Agent 的工作区和 Codex 对话时，可以使用
`pcr_continue_from_agent` 一次完成采用和继续运行。

预计占用超过 5 GiB 的任务或追加候选批次会单独征求确认。磁盘剩余空间小于估算值时，PCR 会在创建候选工作区前直接失败。

## 工具

| MCP 工具 | 作用 |
| --- | --- |
| `pcr_start_run` | 以审查模式启动候选，不立即回写。 |
| `pcr_wait_for_run` | 等待一小段时间并返回状态和新增事件。 |
| `pcr_get_run` / `pcr_get_agent` | 查看运行、Agent、回复、日志与 Token 状态。 |
| `pcr_get_diff` | 以无损分页方式读取支持删除操作的完整 Patch。 |
| `pcr_kill_agent` / `pcr_stop_run` | 停止一个正在运行的候选，或停止整批任务。 |
| `pcr_reject_agent` | 从推荐范围排除候选，或恢复候选。 |
| `pcr_retry_agent` / `pcr_add_agents` | 重试未成功的候选，或增加候选。 |
| `pcr_accept_agent` | 采用成功 Agent，按配置回写并清理。 |
| `pcr_continue_from_agent` | 采用 Agent，并从其提升后的会话开始下一项需求。 |
| `pcr_recover_finalization` | 处理进程异常后无法确认是否完成的回写。 |
| `pcr_discard_run` | 放弃尚未采用的运行并清理候选。 |
| `pcr_cleanup_expired_runs` | 执行 TTL、保留期限与存储清理。 |
| `pcr_list_resume_sessions` | 查找可以作为新运行起点的 Codex 会话。 |
| `pcr_get_resume_history` | 分页读取所选会话中可见的历史对话。 |
| `pcr_list_models` | 列出缓存中的模型及其兼容推理强度。 |
| `pcr_list_runs` | App 重启后恢复查看保留或中断的运行。 |

任务运行在独立 Worker 进程中。Codex 即使重启 MCP Server，也不会中断仍在执行的
Agent；多个 Codex 窗口也可以共用插件状态目录。短时状态锁和按运行划分的操作锁会
阻止相互冲突的状态写入、采用或清理操作。

## 数据与安全说明

插件控制状态默认保存在当前平台的用户状态目录；设置 `PCR_PLUGIN_DATA` 后会改用该目录。候选工作区和 Agent 元数据仍使用 PCR 位于目标工作区之外的 `.codex_parallel_runs` 目录。

插件会在每个运行目录写入内部标记。执行 Diff、回写或删除前，会重新核对原工作区、运行目录、候选路径和标记。候选清理前还会把已完成 Agent 的 Diff 保存到磁盘，因此重启或删除候选工作区后仍可查看。

默认的任务运行上限为 6 小时，运行产物保留 7 天，插件存储配额为 20 GiB。
可以分别通过 `PCR_PLUGIN_RUN_TTL_SECONDS`、`PCR_PLUGIN_RETENTION_SECONDS` 和
`PCR_PLUGIN_STORAGE_QUOTA_BYTES` 调整。主动停止或放弃仍是最快的资源回收方式。
`keep_workspaces=true` 会同时保留候选工作区和对应的
`meta/agent_*/codex_home`；关闭后则会一起删除。

采用过程使用持久化日志记录回写阶段。如果 PCR 无法确认回写是否已经完成，它不会
自动再次回写，而是要求检查原工作区后通过 `pcr_recover_finalization` 明确处理。

PCR 隔离的是工作树，不是宿主机权限。Agent 仍会共享机器、网络、Codex 账户、配额，并可能共享 Git 对象数据库。接受候选前请检查实际 Patch。

## 开发

在仓库根目录验证插件、Skill 和项目：

```bash
python3 -m unittest tests.test_plugin_package tests.test_plugin_runtime
python3 -m compileall -q parallel_codex_runner_core plugins/parallel-codex-runner/scripts
git diff --check
```

仓库 Marketplace 位于 [`.agents/plugins/marketplace.json`](../../.agents/plugins/marketplace.json)。完整项目测试仍使用 `python3 -m unittest discover -s tests`。不要在插件目录中再创建一个嵌套 Git 仓库。

## 常见问题

- **MCP Server 无法启动：**重新运行 `python3 -m pip install -e .`。插件自带的启动器会寻找能够导入 PCR 与 FastMCP 的绝对 Python 路径；需要固定环境时设置 `PCR_PYTHON`。
- **MCP Server 重启后任务仍在运行：**这是预期行为。使用 `pcr_stop_run` 或 `pcr_discard_run` 主动停止，遗留任务还会受到 TTL 限制。
- **找不到 Codex CLI：**确认同一运行环境能够执行 `codex --version`。
- **重启后显示运行中断：**使用 `pcr_list_runs` 找到记录，然后重试符合条件的 Agent，或放弃并清理该运行。
- **原始工作区没有变化：**这是接受候选前的正常行为；需要通过 `pcr_accept_agent` 采用成功 Agent，并启用回写。
