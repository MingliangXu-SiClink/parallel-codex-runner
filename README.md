# parallel-codex-runner

`parallel-codex-runner` 是一个单文件 Python CLI，用来把同一个任务分发给多个隔离的 Codex agent，并把选中的成功结果同步回原工作区。

它适合需要并行探索实现方案、修 bug、补测试或改文档的场景。每个 agent 都会在完整复制的工作区里运行，互不影响；运行结束后，工具按指定策略选择一个成功结果。

## 功能

- 并行或串行启动多个 `codex exec` 进程。
- 为每个候选运行创建独立工作区和日志目录。
- 默认按最大 observed reasoning tokens 选择成功结果，也支持按最长运行时间选择。
- 支持通过参数、文件或 stdin 传入 prompt。
- 支持 `--resume` 选择当前 workspace 下可恢复的 Codex 历史会话，并通过 `codex exec resume <session_id>` 继续上下文。
- 支持保留候选工作区用于人工检查。
- 同步回原工作区时会跳过 `.git`、`.codex_parallel_runs` 和 `.codex_parallel_meta`，避免覆盖原仓库状态和运行器元数据；同时只会把被选中的最佳 agent 的 Codex session 挂回原 workspace，其他 agent 的 session 不会搬回原 workspace。

## 安装

需要 Python 3.8+，并确保 Codex CLI 可通过 `codex` 命令访问。

```bash
python3 -m pip install .
```

可选安装更好的终端输出依赖：

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

## 常用示例

在当前目录并行运行 20 个候选，默认选择 reasoning tokens 最大的成功结果：

```bash
pcr "implement the requested change" -n 20
```

不指定 `-n` 时默认启动 5 个候选：

```bash
pcr "implement the requested change"
```

从当前 workspace 的 Codex 历史会话中选择一个继续：

```bash
pcr --resume "continue the previous task"
```

非交互脚本里可直接指定 session id：

```bash
pcr --resume-session-id 019f2dde-d5ab-7473-856b-ab1b8001f6da "continue the previous task"
```

限制最多 5 个 agent 同时运行：

```bash
pcr "refactor the API client and update tests" -n 20 --max-parallel 5
```

串行运行：

```bash
pcr "make the migration idempotent" -n 6 --serial
```

从文件读取长 prompt：

```bash
pcr --prompt-file /tmp/prompt.txt -n 10 --workspace /path/to/project
```

只运行候选，不同步回原工作区，并保留候选目录：

```bash
pcr "investigate this bug" -n 5 --no-sync-back --keep-workspaces
```

按最长运行时间选择成功结果：

```bash
pcr "improve error handling" -n 10 --best-by duration
```

## 输出

每次运行会在工作区外部创建 `.codex_parallel_runs/<timestamp>/`，其中包含：

- `prompt.txt`：本次传给 agent 的 prompt。
- `summary.json`：所有 agent 的结果和选中结果。
- `BEST_AGENT.txt`：被选中的 agent。
- `BEST_CODEX_SESSION.txt`：被选中的 Codex session id。
- `reasoning_tokens.tsv`：每个 agent 观测到的 reasoning token 数据。
- `meta/agent_*/stdout.log` 和 `stderr.log`：每个进程的输出日志。

默认情况下，候选工作区会在同步结束后删除；使用 `--keep-workspaces` 可以保留。

默认情况下，每个 agent 都使用独立的临时 `CODEX_HOME` 运行，候选过程不会写入真实的 Codex 历史。同步回原工作区时，工具只会把最佳 agent 的 Codex session 导入真实 Codex 索引，并把它绑定到原 workspace；其他 agent 的 session 留在各自 `meta/agent_*/codex_home` 下，不会出现在原 workspace 的 `codex resume` / `pcr --resume` 候选里。这样下一次在原 workspace resume 时，看到的是这次被采纳的最佳历史。使用 `--no-sync-back` 时不会更新原 workspace，也不会导入 session。

## 开发验证

```bash
python3 -m py_compile parallel_codex_runner.py
python3 -m unittest discover
python3 parallel_codex_runner.py --help
```

## License

MIT License. See [LICENSE](LICENSE).
