# parallel-codex-runner

`parallel-codex-runner` 是一个轻量级 Python CLI，用来把同一个开发任务交给多个隔离的 Codex agent 并行探索，然后按策略选出一个结果同步回原工作区。

它适合用于修复问题、补测试、重构、改文档、比较实现方案等需要多路尝试的代码任务。每个候选 agent 都在复制出来的工作区中运行，主工作区保持清晰；运行结束后，工具会汇总结果、记录日志，并把被采纳的候选同步回来。

## 特性

- 并行或串行运行多个 `codex exec` 候选。
- 默认启动 5 个候选，可通过 `-n/--num-agents` 调整。
- 支持按 reasoning tokens 或运行时长选择候选。
- 支持命令行参数、文件或 stdin 传入 prompt。
- 支持从当前 workspace 的 Codex 历史会话中选择 `--resume`。
- 每个候选使用独立的临时 `CODEX_HOME`，便于保留可追踪的候选日志和 Codex session。
- 同步时保留原工作区的 Git 元数据和 runner 自身运行目录。
- 可选安装 `rich`、`tqdm`、`loguru` 获得更好的终端输出。

## 安装

需要 Python 3.8+，并确保 Codex CLI 可通过 `codex` 命令访问。

从源码安装：

```bash
python3 -m pip install .
```

开发时推荐 editable 安装：

```bash
python3 -m pip install -e .
```

安装可选终端输出依赖：

```bash
python3 -m pip install '.[pretty]'
```

安装后可使用 `pcr` 命令：

```bash
pcr "fix the failing tests" -n 8
```

也可以直接运行脚本：

```bash
python3 parallel_codex_runner.py "fix the failing tests" -n 8
```

## 快速开始

在当前目录启动多个候选，按默认策略选择结果：

```bash
pcr "implement the requested change"
```

启动 20 个候选：

```bash
pcr "implement the requested change" -n 20
```

设置同时运行的 Codex 进程数量：

```bash
pcr "refactor the API client and update tests" -n 20 --max-parallel 5
```

串行运行候选：

```bash
pcr "make the migration idempotent" -n 6 --serial
```

从文件读取长 prompt：

```bash
pcr --prompt-file /tmp/prompt.txt -n 10 --workspace /path/to/project
```

使用指定模型：

```bash
pcr "improve error handling" -n 10 --model gpt-5
```

## Resume 工作流

从当前 workspace 的 Codex 历史会话中选择一个继续：

```bash
pcr --resume "continue the previous task"
```

在脚本或自动化中指定 session id：

```bash
pcr --resume-session-id 019f2dde-d5ab-7473-856b-ab1b8001f6da "continue the previous task"
```

候选阶段会使用隔离的临时 `CODEX_HOME`。采纳结果同步回原工作区后，最佳候选的 Codex session 会导入真实 Codex 索引，并绑定到原 workspace，便于后续通过 `codex resume` 或 `pcr --resume` 继续。

## 选择策略

默认策略是 `reasoning_tokens`，即选择成功候选中观测到的最大 reasoning token 值最高的结果：

```bash
pcr "fix a tricky bug" --best-by reasoning_tokens
```

也可以按运行时间选择最长的成功候选：

```bash
pcr "explore possible fixes" --best-by duration
```

## 探索模式

有时你可能想先比较候选结果，再手动决定后续操作：

```bash
pcr "investigate this bug" -n 5 --no-sync-back --keep-workspaces
```

`--no-sync-back` 会生成完整运行结果和日志。`--keep-workspaces` 会保留候选工作区，方便继续检查 diff、运行测试或手动挑选实现。

## 输出目录

每次运行会在工作区外部创建 `.codex_parallel_runs/<timestamp>/`，常见内容包括：

- `prompt.txt`：本次传给候选 agent 的 prompt。
- `summary.json`：运行摘要、候选结果和采纳结果。
- `BEST_AGENT.txt`：被采纳的候选编号。
- `BEST_CODEX_SESSION.txt`：被采纳候选的 Codex session id。
- `FINAL_RESULT_WORKSPACE.txt`：同步目标工作区。
- `reasoning_tokens.tsv`：每个候选观测到的 reasoning token 数据。
- `resume_session.json`：使用 `--resume` 时记录被选中的来源 session。
- `codex_session_promotion.json`：采纳 session 导入 Codex 索引的结果。
- `meta/agent_*/stdout.log` 和 `stderr.log`：每个 Codex 进程的输出日志。
- `meta/agent_*/final_message.md`：每个候选的最终回复。
- `meta/agent_*/codex_home/`：候选运行时使用的临时 Codex home。

候选工作区默认在同步结束后清理；运行日志、摘要和候选 Codex home 会保留在运行目录中。

## 常用选项

| 选项 | 说明 |
| --- | --- |
| `-n, --num-agents` | 候选数量，默认 5 |
| `--max-parallel` | 最大并发数 |
| `--serial` | 串行运行，等价于 `--max-parallel 1` |
| `--best-by` | 选择策略：`reasoning_tokens` 或 `duration` |
| `--prompt-file` | 从 UTF-8 文本文件读取 prompt |
| `--workspace` | 指定目标工作区 |
| `--runs-dir` | 指定运行记录目录 |
| `--codex-bin` | 指定 Codex CLI 路径 |
| `--model` | 传递 Codex 模型名 |
| `--resume` | 从当前 workspace 的 Codex 历史中选择 session |
| `--resume-session-id` | 使用指定 Codex session id |
| `--resume-include-non-interactive` | 在 resume 列表中包含非交互 session |
| `--no-sync-back` | 生成候选结果后跳过同步 |
| `--keep-workspaces` | 保留候选工作区 |

## 开发

运行基础检查：

```bash
python3 -m py_compile parallel_codex_runner.py
python3 -m unittest discover
python3 parallel_codex_runner.py --help
```

项目当前保持单文件 CLI 结构，便于阅读、复制和调试。欢迎通过 issue 或 pull request 讨论改进方向。

## License

MIT License. See [LICENSE](LICENSE).
