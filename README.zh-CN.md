<div align="center">

# parallel-codex-runner

**并行运行多个相互隔离的 Codex 候选，实时观察、审查差异，并从你信任的结果继续工作。**

[English](README.md) · 简体中文

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](#运行要求)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[快速开始](#快速开始) · [TUI 工作流](#tui-工作流) · [工作原理](#工作原理) · [安全边界](#安全边界) · [命令参考](#cli-命令参考)

<code>pcr "修复失败的测试" -n 8</code>

</div>

`parallel-codex-runner`（PCR）是一个本地 Codex 编排工具。它让多个独立候选处理同一个任务：每个候选拥有自己的工作区和临时 `CODEX_HOME`，PCR 保留运行证据、给出推荐，并由你决定哪个结果能够进入原始工作区。

PCR 提供两种使用方式：

- 直接运行 `pcr`，进入支持实时输出、候选审查、Resume 和连续对话的交互式 TUI。
- 运行 `pcr "你的需求"`，执行一次性 CLI 任务，并自动将推荐的成功候选回写到原始工作区。

> [!IMPORTANT]
> 当已安装的 Codex CLI 支持时，PCR 会请求 Codex 使用 Full Access 模式。候选工作区能够隔离不同 Agent 的文件修改，但它们**不是安全沙箱**。请只运行你信任的需求与仓库。

## 动机

在真实使用中，Codex 偶尔会出现用户所说的“降智”：面对完全相同的需求，一次运行可能会充分阅读上下文、完成实现并验证结果，另一次却可能提前结束或只给出很浅的修改。PCR 希望让这种波动变得可观察、可比较，而不是让用户把整个任务押在一次运行上。

本项目的动机与 [openai/codex#30364](https://github.com/openai/codex/issues/30364) 有关。该 Issue 报告了 GPT-5.5 的 reasoning token 在 516、1034、1552 等固定值附近聚集的现象，并询问 reasoning budget、路由、截断或调度机制是否可能导致复杂任务中的性能下降。这个 Issue 并不能证明存在隐藏的推理截断，PCR 也不试图诊断模型内部机制；它提供的是一个务实方案：运行多个候选，比较它们真实完成的工作，然后采用你信任的分支。

## 功能概览

| 能力 | 作用 |
| --- | --- |
| 并行候选 | 并发运行多个 `codex exec`，也可以限制并发数或切换为串行执行。 |
| 交互式 TUI | 直接修改运行配置、输入多行需求、观察每个 Agent，并通过左右键切换。 |
| 实时时序 Detail | 按真实发生顺序显示 Codex 的思考/回复和命令开始/结束事件，同时避免完整命令输出拖慢界面。 |
| 候选审查 | 查看完整 Patch、排除较差候选、重试失败或被 Kill 的 Agent、追加候选或立即采用。 |
| 分支连续对话 | 从 TUI 当前显示的成功 Agent 分支继续下一轮对话。 |
| 推荐策略 | 按 reasoning token 或运行时长推荐结果，但不会覆盖用户在 TUI 中的明确选择。 |
| Git 感知隔离 | 使用 detached Git worktree，并保留暂存、未暂存、删除、未跟踪及 Agent 提交状态。 |
| Resume 支持 | 加载历史 Codex 对话、分发到多个候选，并将最终采用的 Session 提升回原环境。 |
| Model-aware Effort | 根据模型能力提供其真正支持的推理强度选项。 |
| 存储保护 | TUI 运行前估算所有工作区与 Meta 占用，超过 5 GiB 时确认，并检查磁盘剩余空间。 |
| 持久输入历史 | 按 Workspace 与 Codex Session 保存和切换可编辑的输入历史。 |
| 后台完成提醒 | TUI 不在前台时，在首个 Agent 成功及全部 Agent 完成时触发终端铃声。 |
| 中文友好的终端 UI | 内置经过 PCR 修补的 Textual 8.2.8，改善中文输入、字素、文本选择与宽度重排。 |

## 运行要求

- Python 3.10 或更高版本
- 已安装并完成认证、可通过 `codex` 调用的 Codex CLI
- Git，用于优先采用的 worktree 工作区复制路径
- Textual 支持的终端环境；PCR 的进程控制主要面向 macOS 与 Linux

Textual 已经包含在本仓库中，不需要额外安装 TUI extra。

## 安装

在项目检出目录中安装：

```bash
python3 -m pip install .
```

开发模式安装：

```bash
python3 -m pip install -e .
```

如果希望一次性 CLI 模式拥有更丰富的输出，可以安装 Loguru 与 tqdm：

```bash
python3 -m pip install -e '.[pretty]'
```

确认安装成功：

```bash
pcr --help
```

也可以直接运行兼容入口脚本：

```bash
python3 parallel_codex_runner.py "修复失败的测试"
```

## 快速开始

### 交互式 TUI

不提供 Prompt 即可启动 TUI：

```bash
pcr
```

将下一轮设置为 8 个 Agent、最多同时运行 4 个，然后输入普通需求：

```text
/numofagents 8
/maxparallel 4
修复失败的测试并补充回归测试
```

你也可以直接在配置栏目中修改 `AGENTS`、`EXECUTION`、`MAX_PARALLEL`、`RECOMMEND_BY`、`MODEL`、`EFFORT`、`SYNC_BACK`、`KEEP_WORKSPACES` 和 `RESUME`。

### 一次性 CLI

在当前目录运行默认的 5 个候选：

```bash
pcr "实现当前需求"
```

运行 10 个候选，但最多同时执行 3 个：

```bash
pcr "修复不稳定测试并补充覆盖" -n 10 --max-parallel 3
```

串行运行 Agent：

```bash
pcr "重构迁移逻辑" -n 4 --serial
```

只检查候选，不修改原始工作区：

```bash
pcr "调查这个问题" -n 5 --no-sync-back --keep-workspaces
```

指定其他项目或从长 Prompt 文件运行：

```bash
pcr "更新项目文档" --workspace /path/to/project
pcr --prompt-file /tmp/prompt.txt -n 8
```

## TUI 工作流

1. 在目标工作区运行 `pcr`。
2. 通过配置栏目或 Slash Command 调整下一轮设置。
3. 提交需求；PCR 会在创建任何候选工作区之前估算存储占用。
4. 观察实时 Agent 活动；输入框为空时使用左右键切换 Agent。
5. 根据需要使用 `/diff`、`/reject`、`/retry`、`/more` 或 `/kill`。
6. 使用 `/accept` 立即采用当前显示的成功 Agent，或直接提交后续需求从该分支继续。
7. 使用 `/exit` 退出；输入框为空时也可以按 `Ctrl-C`。正在运行的 Agent 会被停止，并进入与正常完成一致的清理流程。

> [!NOTE]
> `RECOMMEND_BY` 只负责推荐，不会强制替用户选择 TUI 分支。修改下一轮配置不会自动采用当前 Agent；提交后续需求、主动 Accept、退出或切换 Workspace/Resume 上下文时，PCR 使用当前显示的成功 Agent。一次性 CLI 模式则会自动回写推荐候选。

### 阅读 Detail 栏目

- 没有对话或 Agent 活动时，Detail 栏目保持隐藏。
- 用户需求与 Codex 内容使用不同的前缀符号和颜色。
- Codex 的思考/回复与命令生命周期事件按真实时序显示。
- 为保证界面流畅，实时 Detail 不展示完整命令 stdout；完整运行产物仍保存在运行目录中。
- Agent 完成后，标题会显示耗时和 reasoning token 信息；Token 间隔按照贡献度汇总，低频项目合并为 `other`。
- 推荐 Agent 使用 `★` 和动态彩虹边框标识。
- 加载 Resume Session 时，会在下一次输入前恢复可读取的历史对话。

### 键盘与鼠标

| 输入 | 操作 |
| --- | --- |
| `Enter` | 提交当前 Prompt 或 Slash Command |
| `Shift-Enter` 或 `Ctrl-J` | 插入换行 |
| 输入为空时 `←` / `→` | 切换当前显示的 Agent |
| 位于首个/最后逻辑行时 `↑` / `↓` | 浏览当前 Workspace/Session 的输入历史 |
| 鼠标滚轮 | 上下滚动 Detail |
| 鼠标拖动 | 选择并复制 TUI 文本 |
| `Ctrl-C` | 优先复制选区；否则清空非空输入；输入为空时停止、清理并退出 |

### 候选审查命令

| 命令 | 作用 |
| --- | --- |
| `/accept` | 立即采用当前显示的成功 Agent。 |
| `/reject` | 将当前 Agent 排除在推荐范围外。 |
| `/diff` | 切换显示当前 Agent 完整的新增、修改、删除文件 Patch。 |
| `/kill [agent]` | 停止正在运行的 Agent；排队 Agent 仍会正常执行。 |
| `/retry [agent]` | 在全新工作区重跑失败或被 Kill 的 Agent。 |
| `/more <n>` | 使用本轮设置为当前问题追加新的候选。 |

## 工作原理

```text
原始工作区
    |
    | 在工作区外创建隔离候选
    v
.codex_parallel_runs/<timestamp>/
    workspaces/
        agent_001/  -> codex exec -
        agent_002/  -> codex exec -
        agent_003/  -> codex exec -
    meta/
        agent_001/  -> 日志、最终回复、独立 CODEX_HOME
        agent_002/
        agent_003/
    |
    | 检查 + 推荐 + 选择一个成功候选
    v
以支持删除同步的方式回写原始工作区
```

每轮运行中，PCR 会：

1. 在目标工作区之外选择 Run Root。
2. 为每个 Agent 创建独立工作区和私有临时 `CODEX_HOME`。
3. 在每个候选中执行 `codex exec -` 或 `codex exec resume <session_id> -`。
4. 将结构化事件流入 TUI，并记录日志、最终回复、Session ID、耗时与 reasoning token 元数据。
5. 按照 `RECOMMEND_BY` 推荐一个成功候选。
6. 最终采用选中的候选；启用回写时同步到原始工作区，提升其 Codex Session，并在未要求保留时删除候选工作区。

PCR 不会合并多个候选，也不会再让一个模型裁判其他模型。最终会完整采用其中一个候选。

## Workspace 与 Git 行为

### Git 工作区

PCR 使用 `--no-checkout` 创建 detached worktree，然后镜像原始工作区的 Index 与文件。这样既能保留暂存修改、未暂存修改、删除和未跟踪文件，也能避免一次多余的完整 Checkout。

最终采用 Git 候选时，PCR 会在一致性检查后同步其文件、Index 与 `HEAD`。如果选中的 Agent 创建了 Commit，原始工作区当前检出的分支可能会前进到该 Commit。如果 Agent 运行期间原始分支或 `HEAD` 发生了不兼容变化，PCR 会拒绝回写。

Git worktree 会共享仓库的 Git Object Database 和管理元数据。它隔离的是 Working Tree 和 worktree 独立状态，而不是存储层或安全层面的整个 Git 仓库。

### 非 Git 工作区

PCR 会创建保留软链接的完整副本。回写支持删除同步，因此被最终候选删除的文件也会从原始工作区删除。

两种路径都会在普通文件同步中排除 `.git`、`.codex_parallel_runs` 和 `.codex_parallel_meta`。

## 推荐策略与 Reasoning Tokens

默认策略为 `reasoning_tokens`：

```bash
pcr "修复一个复杂问题" --recommend-by reasoning_tokens
```

PCR 会推荐 observed reasoning token 总量最高的成功候选，并使用运行时长和 Agent 编号进行确定性的平局处理。如果所有成功候选都没有可用 Token 数据（`N/A`），则回退为按运行时长推荐。

也可以推荐运行时间最长的成功候选：

```bash
pcr "探索多种实现方案" --recommend-by duration
```

Reasoning token 与运行时长都是启发式指标，不是质量分数。采用结果前请检查 `/diff` 与完整对话。在 TUI 中，`/reject` 只会将候选移出推荐池，不会删除其结果。

## Resume 与连续对话

在一次性模式中交互选择历史 Session：

```bash
pcr --resume "继续之前的任务"
```

或指定已知 Session ID：

```bash
pcr --resume-session-id 019f2dde-d5ab-7473-856b-ab1b8001f6da "继续这个任务"
```

在 TUI 中：

```text
/resume
/resume 1
/resume latest
/resume clear
```

PCR 会将选中的 Session State 与 Rollout 复制到每个 Agent 的私有 `CODEX_HOME`，并把工作目录重新绑定到对应 Agent 工作区。最终采用某个候选时，PCR 会将其 Session 导入/提升回真实 Codex Home，并在可能的情况下重新绑定到原始工作区。

## 存储预检

TUI 中的需求开始运行前，PCR 会异步估算：

- 所有候选工作区副本；
- 复制的 Codex State 与 Resume Rollout；
- 每个 Agent 的 Meta 信息与运行预留空间。

聚合估算超过 5 GiB 时，PCR 会在创建 Run Root 或工作区副本前请求确认。选择取消会忽略本次需求，并且不会写入输入历史；选择继续后会检查目标文件系统的剩余空间，空间不足时在复制前将本轮标记为失败。上一轮即将被清理的候选工作区会作为可回收空间计入判断。

> [!CAUTION]
> 存储确认目前只属于交互式 TUI 路径。一次性 `pcr "prompt"` 会直接开始运行；处理超大项目时，请将 `--runs-dir` 放在容量充足的文件系统中，并仅在确实需要保留候选时使用 `--keep-workspaces`。

## 运行产物

默认情况下，运行记录保存在目标工作区外的 `.codex_parallel_runs/<timestamp>/` 中。

| 路径 | 内容 |
| --- | --- |
| `prompt.txt` | 发送给候选的需求 |
| `summary.json` | 可机读的配置、结果、推荐、回写与清理状态 |
| `BEST_AGENT.txt` | 该次记录中的推荐候选 |
| `BEST_CODEX_SESSION.txt` | 检测到时记录推荐候选的 Codex Session ID |
| `FINAL_RESULT_WORKSPACE.txt` | 发生回写时记录原始工作区路径 |
| `reasoning_tokens.tsv` | 每个 Agent 的总量、观测值与间隔分布 |
| `codex_capabilities.json` | 本轮检测到的 Codex CLI 能力 |
| `sample_command.json` | 代表性的 Agent 命令 |
| `meta/agent_*/stdout.log` | 候选 stdout |
| `meta/agent_*/stderr.log` | 候选 stderr |
| `meta/agent_*/final_message.md` | 候选最终回复 |
| `meta/agent_*/codex_home/` | 可 Resume 的私有状态；执行后会清除复制的凭据/配置支持文件 |
| `retry_history/agent_*/` | 被后续 Retry 替代的历史 Meta |

除非启用 `--keep-workspaces` 或 `KEEP_WORKSPACES`，候选工作区会在一次性运行结束后，或 TUI 完成最终采用/退出时删除；Meta 信息仍会保留以供检查。

## 安全边界

- 当 Codex CLI 提供对应参数时，PCR 会使用审批绕过/完整工作区访问模式启动 Codex。
- 工作区隔离不是容器、虚拟机或操作系统沙箱。Agent 仍共享宿主机、网络、Codex 账户与配额，并在执行期间使用复制到私有 Home 的凭据。
- Git worktree 共享仓库 Object Database；最终采用时可能同步所选 Agent 的 Commit 与 Index 状态。
- 只有成功候选被最终采用时，PCR 才会把其 Working Tree 回写到原始工作区；`--no-sync-back` 会禁用该回写。
- 回写支持删除同步，检查删除内容与检查新增内容同样重要。
- PCR 不会通过普通文件同步覆盖原始 `.git` 目录。
- 临时 Agent 的配置与凭据采用复制而非软链接，并在执行后清除；用于 Resume 的状态会保留在 Meta 中。
- `/exit` 与 `Ctrl-C` 会停止活跃 Agent，并执行与正常运行一致的工作区清理；显式保留工作区时除外。
- 在 Push 或发布结果前，请检查 `git status`、`git diff` 与最终采用的 Commit。

## CLI 命令参考

| 参数 | 说明 |
| --- | --- |
| `-n, --num-agents` | 候选数量，默认 `5` |
| `--max-parallel` | Codex 最大并发进程数 |
| `--serial` | 每次只运行一个候选 |
| `--recommend-by` | 推荐策略：`reasoning_tokens` 或 `duration` |
| `--prompt-file` | 从 UTF-8 文件读取 Prompt |
| `--workspace` | 目标工作区，默认为当前目录 |
| `--runs-dir` | 运行记录目录，必须位于工作区之外 |
| `--codex-bin` | Codex 可执行文件，默认为 `codex` |
| `--model` | 可选 Codex 模型名 |
| `--effort` | 当前模型支持的可选推理强度 |
| `--resume` | 交互选择可 Resume 的 Codex Session |
| `--resume-session-id` | Resume 指定 Session ID |
| `--resume-include-non-interactive` | 在选择器中包含 `codex exec` Session |
| `--no-sync-back` | 不修改原始工作区 |
| `--keep-workspaces` | 运行后保留候选工作区 |

## TUI 命令参考

| 命令 | 说明 |
| --- | --- |
| `/help` | 显示所有 TUI 命令 |
| `/status`、`/config` | 显示当前运行配置 |
| `/accept` | 采用当前显示的成功 Agent |
| `/reject` | 将当前 Agent 排除在推荐范围外 |
| `/retry [agent]` | 重跑失败或被 Kill 的 Agent |
| `/more <n>` | 为当前问题追加候选 |
| `/diff` | 切换显示当前 Agent 的完整 Patch |
| `/kill [agent]` | 停止正在运行的 Agent |
| `/numofagents <n>` | 设置下一轮 Agent 数量 |
| `/maxparallel <n\|auto>` | 设置或清除并发限制 |
| `/serial` | 串行运行 Agent |
| `/parallel` | 并行运行 Agent |
| `/recommendby <duration\|reasoning_tokens>` | 设置推荐策略 |
| `/model <name\|clear>` | 设置或清除模型 |
| `/effort <auto\|level>` | 选择模型支持的推理强度 |
| `/workspace <path>` | 切换目标工作区 |
| `/runsdir <path\|clear>` | 设置或重置运行记录目录 |
| `/codexbin <path>` | 设置 Codex 可执行文件 |
| `/syncback <on\|off>` | 启用或禁用回写 |
| `/keepworkspaces <on\|off>` | 启用或禁用候选工作区保留 |
| `/promptfile <path>` | 读取并运行 UTF-8 Prompt 文件 |
| `/resumeinclude <on\|off>` | 包含或排除非交互 Session |
| `/resume` | 显示可 Resume 的 Session |
| `/resume <n\|session>` | 加载列表中或显式指定的 Session |
| `/resume latest` | 加载最新可 Resume Session |
| `/resume clear` | 下一轮不使用 Resume |
| `/clear` | 在安全时清理当前视图 |
| `/exit` | 停止、清理并退出 |

## 项目结构

| 路径 | 职责 |
| --- | --- |
| `parallel_codex_runner.py` | 直接运行与旧导入方式的兼容入口 |
| `parallel_codex_runner_core/app.py` | CLI 编排、Agent 执行、存储估算、结果汇总与 Session 提升 |
| `parallel_codex_runner_core/codex_cli.py` | Codex 能力检测与命令构造 |
| `parallel_codex_runner_core/codex_models.py` | 模型缓存与兼容 Effort 选择 |
| `parallel_codex_runner_core/workspace.py` | 工作区估算、worktree、复制、清理与回写 |
| `parallel_codex_runner_core/tui_textual.py` | Textual TUI 与候选审查工作流 |
| `parallel_codex_runner_core/prompt_history.py` | 持久化 Workspace/Session 输入历史 |
| `parallel_codex_runner_core/diffing.py` | 支持删除的完整工作区 Diff |
| `parallel_codex_runner_core/paths.py` | 路径与 Run Root 辅助逻辑 |
| `parallel_codex_runner_core/models.py` | 共享结果与 Session 数据类 |
| `vendor/textual/` | 内置 Textual 源码、License、测试/文档及 PCR Unicode 补丁 |
| `tests/` | PCR 回归测试 |

## 开发

运行 PCR 测试：

```bash
python3 -m unittest discover -s tests
python3 -m compileall -q parallel_codex_runner.py parallel_codex_runner_core
git diff --check
```

修改内置 Textual 补丁时，还应运行相关上游测试（需要 pytest）：

```bash
PYTHONPATH=vendor/textual/src python3 -m pytest -m 'not syntax' \
  vendor/textual/tests/input vendor/textual/tests/text_area
```

固定的上游版本和本地补丁清单见 [`vendor/textual/PCR_PATCHES.md`](vendor/textual/PCR_PATCHES.md)。

## 参与贡献

欢迎提交聚焦的问题与 Pull Request。请说明用户可见的行为，避免夹带无关重构，并根据影响范围补充回归测试；提交前请运行上述测试命令。

## 许可证

PCR 使用 [MIT License](LICENSE)。内置 Textual 保留其上游 [MIT License](vendor/textual/LICENSE)。
