<div align="center">

# parallel-codex-runner

**一个任务，多次独立的 Codex 尝试。比较实际结果，只采用你认可的那一份。**

[English](README.md) · 简体中文

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](#运行要求)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[为什么做 PCR？](#为什么做-pcr) · [快速开始](#快速开始) · [Codex App 插件](#codex-app-插件) · [使用 TUI](#使用-tui) · [完整参考](#完整参考)

<code>pcr "修复失败的测试" -n 8</code>

</div>

面对同一个任务，Codex 每次运行的结果可能差别很大。有的运行会认真阅读上下文、完成修改并验证结果；有的却可能只改几行就提前结束。

`parallel-codex-runner`（PCR）会把同一个需求交给多个 Codex Agent，并让它们在互相隔离的工作区中独立完成。你可以实时查看每个 Agent 的对话和操作、检查代码差异、停止或重试候选，最后只把你认可的结果带回原始工作区。

PCR **不会**把多份答案拼成一份，也不会让 Agent 互相投票。每个 Agent 都代表一条完整分支，最终采用其中一条。

```text
                        +--> AGENT-001 --> 对话 + 代码修改
你的需求 --> PCR ------+--> AGENT-002 --> 对话 + 代码修改
                        +--> AGENT-003 --> 对话 + 代码修改
                                      |
                                  检查并选择
                                      |
                                      v
                                  原始工作区
```

> [!IMPORTANT]
> - 运行 `pcr` 会进入 TUI，由你选择最终分支。
> - 运行 `pcr "你的需求"` 会进入一次性模式，并自动回写推荐的成功结果。

## 为什么做 PCR？

Agent 的表现并不完全稳定。重要任务如果靠人工重复运行 Codex、来回翻终端日志，既费时间，也很难比较；如果让多个 Codex 直接在同一个目录修改文件，又容易互相覆盖。

PCR 把这种不确定性变成一个可以审查的流程：

- 每个 Agent 都从相同的项目状态开始；
- Agent 之间不会覆盖彼此的文件；
- 所有对话和命令活动集中显示在一个 TUI 中；
- 使用 `/diff` 直接检查每个 Agent 真正修改了什么；
- 只有你最终采用的分支才会回写。

用户通常把这种突然出现的质量下降称为 Codex“降智”。PCR 的动机也来自相关反馈，包括 [openai/codex#30364](https://github.com/openai/codex/issues/30364)。该 Issue 讨论了 reasoning token 聚集现象，并询问它是否与复杂任务中的表现下降有关。它不能证明 Codex 存在隐藏的推理截断，PCR 也不试图解释模型内部机制。PCR 解决的是更直接的问题：多运行几次，看实际结果，再做选择。

## 快速开始

### 运行要求

- Python 3.10 或更高版本
- 已安装并完成认证、可以通过 `codex` 调用的 [Codex CLI](https://github.com/openai/codex)
- Git，用于通过 Git worktree 隔离工作区
- 推荐使用 macOS 或 Linux，以获得完整的进程控制能力

Textual 已包含在本仓库中，并带有 PCR 针对中文输入和终端宽度适配的修改，不需要额外安装 TUI 依赖。

### 安装

在项目检出目录中运行：

```bash
python3 -m pip install .
```

开发时可以使用可编辑安装：

```bash
python3 -m pip install -e .
```

确认 Codex 和 PCR 都可以正常调用：

```bash
codex --version
pcr --help
```

### 启动 TUI

进入你希望 Codex 修改的项目，然后运行 PCR：

```bash
cd /path/to/project
pcr
```

在底部输入框中直接输入需求并按 `Enter`：

```text
修复失败的测试，说明根本原因，并补充回归测试。
```

提交后，PCR 会依次完成这些事情：

1. 估算本轮需要的临时磁盘空间；
2. 为每个 Agent 创建独立工作区；
3. 在所有工作区中运行同一个需求；
4. 实时显示每个 Agent 的对话和命令活动；
5. 推荐一个成功结果，同时把最终选择权留给你。

需要先调整下一轮数量和并发数时，可以输入：

```text
/numofagents 8
/maxparallel 4
```

TUI 顶部的配置项也可以直接修改。

## Codex App 插件

PCR 现在也可以作为本地 Codex App 插件使用。正常使用流程很简单：把任务交给
Codex，PCR 在后台保留并运行隔离候选；全部结束后，Codex 比较回复和 Patch，说明
理由并推荐一个 Agent；只有你确认后，插件才会回写。Worker、事件记录和留存机制
只是防止 App 或 MCP 重启时丢失正在运行的任务，不会增加用户操作步骤。

依次安装运行程序、检查环境、注册本地 Marketplace，然后安装插件：

```bash
cd /Users/mingliangxu/Desktop/parallel-codex-runner

# 安装 PCR 与 MCP Server
python3 -m pip install -e .

# 检查运行环境
python3 plugins/parallel-codex-runner/scripts/check_runtime.py

# 注册本地插件市场
codex plugin marketplace add /Users/mingliangxu/Desktop/parallel-codex-runner

# 安装插件
codex plugin add parallel-codex-runner@personal
```

环境检查最后应显示 `"ok": true`。安装完成后重启 Codex App，并新建对话，随后可以直接输入：

```text
使用 Parallel Codex Runner，为这个任务运行五个隔离候选。
和我一起比较它们的 Patch，在我选择前不要回写。
```

插件不会把“运行完成”或“推荐结果”当作修改项目的授权。只有明确接受某个成功 Agent 后才会回写。工具说明、重启恢复和常见问题见[插件使用指南](plugins/parallel-codex-runner/README.zh-CN.md)。

## 使用 TUI

### 查看 Agent 正在做什么

Detail 区域只在有实际内容时出现。用户需求、Codex 回复、推理内容以及命令开始/结束状态都会按照真实发生顺序追加显示。为了避免界面卡顿，完整的命令输出不会塞进 Detail，但仍会记录在本轮运行产物中。

输入框为空时：

- 按 `Left` 或 `Right` 切换 Agent；
- 使用鼠标滚轮上下查看内容；
- 拖动鼠标选择文字，再按 `Ctrl-C` 复制。

Agent 完成后，标题会显示运行时间和 reasoning token 信息。当前推荐的 Agent 会带有 `★` 标记和动态彩色边框。

### 选择要保留的分支

一个常用的检查流程是：

1. 在已完成的 Agent 之间切换；
2. 对看起来不错的候选运行 `/diff`；
3. 使用 `/reject` 排除明显不合适的结果；
4. 将界面停留在你希望保留的成功 Agent 上；
5. 输入下一轮需求，或者运行 `/accept`。

`RECOMMEND_BY` 只负责给出建议，不会覆盖你的明确选择。继续对话、主动采用、退出，或者切换工作区和恢复会话时，PCR 会以当前显示的成功 Agent 为准，并最终采用这条分支。

不必等所有 Agent 全部完成。如果某个 Agent 已经给出了满意结果，你可以直接从它继续；PCR 会停止其余任务、同步当前分支，再以它的工作区和 Codex 会话为基础开始下一轮。

### 审查和控制候选

| 命令 | 作用 |
| --- | --- |
| `/diff` | 显示或隐藏当前 Agent 的完整文件差异。 |
| `/accept` | 立即采用当前显示的成功 Agent。 |
| `/reject` | 将当前 Agent 移出推荐范围。 |
| `/kill [agent]` | 停止正在运行的 Agent；排队中的 Agent 仍会正常启动。 |
| `/retry [agent]` | 在全新工作区重跑失败或被停止的 Agent。 |
| `/more <n>` | 为当前问题增加更多候选。 |

### 继续以前的 Codex 对话

打开 Resume 选择列表：

```text
/resume
```

可以按编号选择、加载最近一次对话，或清除选择：

```text
/resume 1
/resume latest
/resume clear
```

选中后，PCR 会先把历史对话加载到 Detail，再为每个 Agent 准备一份互相隔离的会话副本。最终只有被采用分支对应的会话会回到真实 Codex 环境。

### 使用输入历史

光标位于输入框第一行或最后一行时，按 `Up` 或 `Down` 可以浏览当前工作区和会话的历史需求。修改某条历史需求后，修改后的文本会成为最新草稿，行为与常见命令行历史一致。

### 退出

输入 `/exit`，或者在输入框为空时按 `Ctrl-C`。PCR 会立即停止仍在运行的 Agent，并执行与正常结束相同的分支确认和工作区清理。

`Ctrl-C` 会根据当前状态执行不同操作：

1. 有文本选区时复制选区；
2. 否则，输入框非空时清空输入；
3. 否则停止任务、清理并退出。

## 一次性命令行模式

不需要交互检查时，可以直接把需求写在命令行中：

```bash
pcr "修复不稳定测试并补充回归测试"
```

一次性模式默认运行 5 个候选，自动推荐一个成功结果，将它同步到原始工作区，然后清理候选工作区。

常见用法：

```bash
# 运行 10 个候选，但最多同时运行 3 个
pcr "实现数据迁移" -n 10 --max-parallel 3

# 每次只运行一个候选
pcr "重构解析器" -n 4 --serial

# 只检查结果，不修改原始工作区
pcr "调查这个问题" --no-sync-back --keep-workspaces

# 指定其他工作区，或从文件读取较长需求
pcr "更新项目文档" --workspace /path/to/project
pcr --prompt-file /tmp/prompt.txt -n 8
```

> [!NOTE]
> 下文所述的大型任务确认在 TUI 和 Codex App 插件中提供。一次性模式会直接开始运行，因此处理大项目时，请将 `--runs-dir` 放在空间充足的文件系统中。

## 工作区与回写

PCR 会在目标工作区外创建本轮运行目录：

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

每个 Agent 都有自己的工作目录和临时 `CODEX_HOME`。

### 原始工作区是 Git 仓库

PCR 会创建 detached Git worktree，并把原始工作区当前的文件和索引状态镜像进去。因此，已提交文件、暂存和未暂存修改、删除内容以及未跟踪文件都可以保留。

采用某个 Agent 时，PCR 会先做一致性检查，再将该 Agent 的文件、索引和 `HEAD` 应用到原始仓库。如果 Agent 创建了提交，原始工作区当前分支可能会前进到该提交。如果运行期间原始分支发生了不兼容变化，PCR 会拒绝回写，避免覆盖新的工作。

Git worktree 会共享仓库的对象数据库和部分 Git 管理数据。它可以隔离工作树，但不等于独立克隆，更不等于安全沙箱。

### 原始工作区不是 Git 仓库

PCR 会创建保留软链接的完整目录副本。回写能够同步删除操作：如果最终 Agent 删除了一个文件，原始工作区中的对应文件也会删除。

### 清理与保留

正常采用结果或退出后，PCR 会删除候选工作区；启用 `--keep-workspaces` 时除外。运行日志、元数据和可恢复的会话状态会继续保留在本轮目录中。

只想查看运行结果、不希望修改原始工作区时，请使用 `--no-sync-back`。

## 推荐机制

PCR 支持两种推荐方式：

- `reasoning_tokens`（默认）：优先推荐已观测到的 reasoning token 总量最高的成功 Agent；
- `duration`：优先推荐运行时间最长的成功 Agent。

运行时间和 token 数量都只是启发式指标，不是质量分数。数值更大并不代表代码一定更好。推荐结果只适合用作检查起点，真正决定前最好使用 `/diff` 查看修改。

TUI 还会汇总 reasoning token 的正向增量。间隔种类过多时，只保留贡献最大的 4 项，其余合并到 `other`。

## 存储与安全

### 大型任务存储检查

TUI 创建工作区之前，PCR 会估算以下内容的总大小：

- 所有候选工作区副本；
- 复制的 Codex 状态与 Resume 数据；
- 每个 Agent 的元数据和运行预留空间。

估算超过 5 GiB 时，PCR 会询问是否继续。选择继续后，它会先检查目标文件系统的剩余空间；选择取消则不会创建工作区，也不会把本次需求写入历史。

### 安全说明

> [!CAUTION]
> 工作区隔离只能避免 Agent 修改同一个工作树。它不是容器、虚拟机或操作系统级沙箱。

- 当 Codex CLI 支持时，PCR 会请求 Full Access/绕过审批模式。
- Agent 仍然共享宿主机、网络、Codex 账户、配额和 Git 对象数据库。
- 执行所需的配置与凭据会复制到 Agent 的临时目录，并在运行后清除；用于恢复对话的状态会保留在元数据中。
- 回写包括文件删除，也可能包括最终 Agent 的提交和索引状态。
- 普通文件复制会排除 `.git`、`.codex_parallel_runs` 和 `.codex_parallel_meta`。

请只在你信任的仓库中运行可信需求。推送或发布结果前，仍应检查 `git status`、`git diff` 和最终采用的提交。

## 完整参考

<details>
<summary><strong>CLI 参数</strong></summary>

| 参数 | 说明 |
| --- | --- |
| `-n, --num-agents` | 候选数量，默认 `5`。 |
| `--max-parallel` | Codex 最大并发进程数。 |
| `--serial` | 每次只运行一个候选。 |
| `--recommend-by` | 按 `reasoning_tokens` 或 `duration` 推荐。 |
| `--prompt-file` | 从 UTF-8 文件读取需求。 |
| `--workspace` | 目标工作区，默认为当前目录。 |
| `--runs-dir` | 运行数据目录，必须位于工作区之外。 |
| `--codex-bin` | Codex 可执行文件，默认为 `codex`。 |
| `--model` | 指定 Codex 模型。 |
| `--effort` | 选择当前模型支持的推理强度。 |
| `--resume` | 交互选择可恢复的 Codex 会话。 |
| `--resume-session-id` | 恢复指定会话 ID。 |
| `--resume-include-non-interactive` | 在选择器中包含 `codex exec` 会话。 |
| `--no-sync-back` | 不修改原始工作区。 |
| `--keep-workspaces` | 运行后保留候选工作区。 |

</details>

<details>
<summary><strong>全部 TUI 命令</strong></summary>

| 命令 | 说明 |
| --- | --- |
| `/help` | 显示所有 TUI 命令。 |
| `/status`、`/config` | 显示当前运行配置。 |
| `/accept` | 采用当前显示的成功 Agent。 |
| `/reject` | 将当前 Agent 排除在推荐范围外。 |
| `/retry [agent]` | 重跑失败或被停止的 Agent。 |
| `/more <n>` | 为当前问题增加候选。 |
| `/diff` | 显示或隐藏当前 Agent 的完整 Patch。 |
| `/kill [agent]` | 停止正在运行的 Agent。 |
| `/numofagents <n>` | 设置下一轮 Agent 数量。 |
| `/maxparallel <n\|auto>` | 设置或清除并发限制。 |
| `/serial` | 串行运行 Agent。 |
| `/parallel` | 并行运行 Agent。 |
| `/recommendby <duration\|reasoning_tokens>` | 设置推荐方式。 |
| `/model <name\|clear>` | 设置或清除模型。 |
| `/effort <auto\|level>` | 选择模型支持的推理强度。 |
| `/workspace <path>` | 切换目标工作区。 |
| `/runsdir <path\|clear>` | 设置或重置运行数据目录。 |
| `/codexbin <path>` | 设置 Codex 可执行文件。 |
| `/syncback <on\|off>` | 启用或禁用回写。 |
| `/keepworkspaces <on\|off>` | 启用或禁用候选工作区保留。 |
| `/promptfile <path>` | 读取并运行 UTF-8 需求文件。 |
| `/resumeinclude <on\|off>` | 包含或排除非交互会话。 |
| `/resume` | 显示可恢复的会话。 |
| `/resume <n\|session>` | 按列表编号或会话 ID 加载。 |
| `/resume latest` | 加载最近的可恢复会话。 |
| `/resume clear` | 下一轮不使用历史会话。 |
| `/clear` | 在不会丢失运行结果时清空 Detail。 |
| `/exit` | 停止活跃 Agent、清理并退出。 |

</details>

<details>
<summary><strong>键盘与鼠标</strong></summary>

| 输入 | 操作 |
| --- | --- |
| `Enter` | 提交需求或 Slash Command。 |
| `Shift-Enter` 或 `Ctrl-J` | 插入换行。 |
| 输入为空时 `Left` / `Right` | 切换 Agent。 |
| 位于首行/末行时 `Up` / `Down` | 浏览输入历史。 |
| 鼠标滚轮 | 滚动 Detail。 |
| 鼠标拖动 | 选择文字。 |
| `Ctrl-C` | 根据上下文复制、清空输入或退出。 |

</details>

## 运行产物

运行记录默认保存在 `.codex_parallel_runs/<timestamp>/` 中。

| 路径 | 内容 |
| --- | --- |
| `prompt.txt` | 发送给所有候选的需求。 |
| `summary.json` | 配置、结果、推荐、回写与清理状态。 |
| `BEST_AGENT.txt` | 本轮记录的推荐 Agent。 |
| `BEST_CODEX_SESSION.txt` | 可用时记录推荐 Agent 的 Codex 会话 ID。 |
| `FINAL_RESULT_WORKSPACE.txt` | 回写后的原始工作区路径。 |
| `reasoning_tokens.tsv` | 每个 Agent 的 token 总量与增量分布。 |
| `meta/agent_*/stdout.log` | 捕获的 Codex stdout。 |
| `meta/agent_*/stderr.log` | 捕获的 Codex stderr。 |
| `meta/agent_*/final_message.md` | Agent 最终回复。 |
| `meta/agent_*/codex_home/` | 可恢复的私有 Codex 状态。 |
| `retry_history/agent_*/` | 被后续重试替代的历史元数据。 |

## 开发

运行项目检查：

```bash
python3 -m unittest discover -s tests
python3 -m compileall -q parallel_codex_runner.py parallel_codex_runner_core
git diff --check
```

修改内置 Textual 补丁时，还应运行相关上游测试：

```bash
PYTHONPATH=vendor/textual/src python3 -m pytest -m 'not syntax' \
  vendor/textual/tests/input vendor/textual/tests/text_area
```

固定的 Textual 版本与本地补丁清单见 [`vendor/textual/PCR_PATCHES.md`](vendor/textual/PCR_PATCHES.md)。

<details>
<summary><strong>项目结构</strong></summary>

| 路径 | 职责 |
| --- | --- |
| `parallel_codex_runner.py` | 包入口与兼容导入。 |
| `parallel_codex_runner_core/app.py` | CLI 编排、Agent 执行、结果汇总与会话提升。 |
| `parallel_codex_runner_core/tui_textual.py` | 交互式 TUI 与候选审查流程。 |
| `parallel_codex_runner_core/workspace.py` | 空间估算、worktree、复制、清理与回写。 |
| `parallel_codex_runner_core/codex_cli.py` | Codex 能力检测与命令构造。 |
| `parallel_codex_runner_core/codex_models.py` | 模型缓存与兼容 Effort 选择。 |
| `parallel_codex_runner_core/prompt_history.py` | 持久化输入历史。 |
| `parallel_codex_runner_core/diffing.py` | 支持删除操作的工作区 Diff。 |
| `parallel_codex_runner_core/models.py` | 共享的运行和会话数据模型。 |
| `parallel_codex_runner_core/plugin_runtime.py` | 插件运行使用的持久化审查控制器。 |
| `parallel_codex_runner_core/plugin_mcp.py` | Codex App 插件调用的本地 MCP 工具。 |
| `parallel_codex_runner_core/plugin/` | 持久化状态、事件索引、独立 worker 和产物路径校验。 |
| `plugins/parallel-codex-runner/` | 插件清单、Skill、运行检查和插件文档。 |
| `.agents/plugins/marketplace.json` | 仓库内的 Codex 插件 Marketplace。 |
| `vendor/textual/` | 内置 Textual 与 PCR 的终端输入补丁。 |
| `tests/` | 回归测试。 |

</details>

## 参与贡献

欢迎提交 Issue 和 Pull Request。请清楚描述用户可见的问题，避免夹带无关重构，并为修改到的工作流程补充回归测试。

## 许可证

PCR 使用 [MIT License](LICENSE)。内置 Textual 保留其上游 [MIT License](vendor/textual/LICENSE)。
