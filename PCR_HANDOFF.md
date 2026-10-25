# PCR 项目交接

更新：2026-10-25。这是接手入口，不是新的执行授权；用户最新人工指令优先。

PCR 为同一任务运行多个隔离候选、可选综合并由用户保存所选结果。macOS 是控制和阅读界面；原生 Codex、后台 worker、pcrd 与远端计算的生命周期必须分别核实。

## 目录与当前交付

- 唯一开发目录：`/Users/mingliangxu/Desktop/parallel-codex-runner`。用户所说“桌面的 PCR”就是这里；`~/Desktop/PCR` 不存在。不要在本聊天最初的 Documents/Codex 工作目录开发。
- 本轮已正式发布并安装 `/Applications/Parallel Codex.app` **0.1.474 / build474**，正常重启后 PID 31022；实际加载执行文件 SHA-256 `eafdf67912053bb4a95967abfd3670017dd619477036bc7d77c7bddbdfbe90a0`。操作前重查，不固定 PID。原473已正常退出，没有强退。
- LaunchAgent 当前指向 pcrd 0.1.105。必须动态读取 `~/Library/LaunchAgents/com.parallelcodexrunner.pcrd.plist` 的 ProgramArguments，并用那个 runtime 的 Python/PcrdClient；不能拿仓库代码冒充已加载服务。
- `release/manifest.json` 记录 macOS 474、pcrd 105、Relay 0.1.20、Android 0.1.268/build269。后两项只是发行清单，本轮没有验证其实际加载状态。
- 仓库处于已有的大规模迁移：旧根目录 Python/vendor 显示删除，正式 `src/`、`apps/`、`remote/`、`release/` 大量未跟踪。不要 reset、clean、覆盖克隆或顺手全部提交；仅看 git diff 会漏掉本轮修改。正式归档的 `source_hashes` 才是近期版本比较依据。
- `MAIN_AGENT_HANDOFF.md` 是历史材料，含过期版本、旧待办和已被覆盖的通知要求；不要把其中“继续处理”当成当前授权。

## 本轮保存问题

用户操作：保存结果后切到另一个 workspace，再回来仍需保存。

已经复现的客户端根因不是“切走取消了后台保存”：`acceptSelectedAgent` 收到原 run 回执后先检查页面是否仍显示它，不匹配就丢弃缓存更新和后续 finalizing 轮询；后台读到的新状态也没有独立持久化。成功判断又依赖当前页面，混淆了业务完成与导航状态。

修复位置（相对本仓库）：

- `apps/macos/Sources/PCRRemoteMac/AppModel+RunControls.swift`：保存回执先归还原 machine/workspace/run；只在仍显示原 run 时更新界面。异步保存继续原 run 的只读轮询；只有确认 finalized 且 syncedBack 才算完成，不因切页误判失败，不派发另一工作区队列。
- `AppModel+RunOperations.swift`（同目录）：统一收取业务回执；丢回复后只读核对原任务，不能重发保存。可见状态仍有完整身份检查。
- `AppModel+RunState.swift`（同目录）：将新汇总连同原回答、历史、分支选择写回原缓存；拒绝更旧投影，不能把新 child 或其他 workspace 改回父轮。
- `AppModel+RunSettings.swift`（同目录）：保存完成后的“修改续问来源”也检查原页面代次，不能把旧选择写进刚打开的另一个工作区。首次回执核对失败则保留待读取状态，继续原 run 的只读观察。
- `apps/macos/Tests/ClientTransport/WorkspaceReadinessTests.swift`：真实 `selectWorkspace` 切出/返回、迟到回执、异步 finalizing、丢回复核对、冷缓存恢复、重复点击、父子轮交错回归。生产比赛消息不是测试数据。

现场验收：原硬膜外运动解码 run `pcr-20261023T174618Z-d8773f9f` 的 journal、原命令回执和 API 均确认 Agent10 finalized/syncedBack；474真实界面显示“已保存 / 综合2已采用”，保存按钮消失。切到记忆赛道再返回，立即保留该状态，未再次点击保存。最后恢复用户原记忆赛道、Agent6和空输入框。升级保留10个原worker、40个原生子进程、daemon及持久输入；不等于逐个远端计算健康已重验。

独立未修边界：pcrd health/runs.list 仍出现10-12秒读超时，原 `turns.get` 约29.55秒才返回。首次升级后的旧缓存因此仍短暂显示未保存，随后实际读回并收敛。采样中的字符串拼接和RLock等待尚未定位到具体Python行。巡检初期两条未返回保存请求也按各自原命令ID核实完成，见QA；没有重复保存、重发任务或重启daemon。

本轮详细验收与交付状态：`remote/qa/2026-10-25-save-result-ownership.md`。私密证据：`~/Library/Caches/ParallelCodexMaintenance/SaveOwnership-20261025/`。

最终门禁：703项原生测试、176项聚焦测试、58项严格协作池测试全部通过，另通过协议、图标、签名及归档门禁。正式包 `release/clients/macos/Parallel-Codex-macos-0.1.474.zip`，SHA-256 `70d36cedc708c5526ac9430e7fe1be6f8aafc9b1c2393d588b3bdfe90bbc9ea6`。认证更新检查仍报IpcError；正式私有目录发布/readback完成后，用同一归档走未修改的原生验签和安装器，不能称认证下载已修好。

## 已做过什么

以下是已有实现及其审计入口，不代表本轮重验了所有现场：

| 范围 | 已落地内容 / 证据入口（均在 remote/qa） |
| --- | --- |
| 连续对话与工作区导航 | 回合目标、缓存覆盖、迟到读响应、已接受续问恢复；`2026-10-23-workspace-continuation-coherence.md`（469/470）、`2026-10-23-save-history-gates.md`（472）。已有缓存先显示，缺失历史继续加载，不能靠隐藏旧轮提示冒充修好。 |
| 保存、续问与队列 | 472 分离保存写门禁与历史/health读等待；容量确认显示服务端原因，未改5GiB阈值。本轮继续修保存回执的跨页面归属。`2026-10-18-client458-queued-continuation.md`、`2026-10-05-macos-durable-message-handoff.md`。 |
| 原生询问与回答 | 问题内联、选择/自由输入、独立草稿及原回答ID投递；“本机保存”“PCR接受”“Agent原生接收”分层显示。471修按钮对比度及编辑中草稿被刷新清空。`2026-10-18-client460-question-layout-and-receipts.md`、`2026-10-20-inline-agent-questions.md`、`2026-10-23-question-editor-drafts.md`。 |
| 输入栏与执行配置 | 单行候选数量/模型、综合数量/模型；紧凑向上浮层，原生选项菜单，数量直接编辑/步进，不二次确认、不向下撑开、不加标题和关闭X。发送/停止/入队共用一个主按钮。`2026-10-18-client456-compact-option-menus.md`、`2026-10-20-client463-composer.md`、`2026-10-21-composer-polish.md`。 |
| 阅读、命令、图片 | 跳底按钮是覆盖层；工具列表不困住外层滚动；图片内联，不展示Base64正文；长对话近期命令与历史分页分开。`2026-10-20-client461-transcript-jump-overlay.md`、`2026-10-20-client462-work-scroll-ownership.md`、`2026-10-20-native-image-preview.md`、`2026-10-21-recent-command-visibility.md`。 |
| 账号切换与恢复 | 468/105区分当前登录额度和原执行器绑定，优先原生 thread/resume；原会话、工作区、输入ID、工具归属和去重不能丢。旧执行器迁移的实际范围见 `2026-10-21-account-native-resume.md`。 |
| 窗口卡死 | 473把 transcript 原生视图提交移出 SwiftUI updateNSView 同步调用栈，合并最新快照并撤销脱离挂载的更新；保留绘制隔离。旧472仅在用户针对该卡死客户端授权后强退，12个worker未动；`2026-10-24-macos-focused-transcript-hang.md`。此授权不能复用于新进程。 |
| 退出、签名与升级 | 447起关闭/退出/更新不采用、不同步、不清理结果，只保存输入和查看状态。专用离线加密签名流程及严格原生验签；`2026-10-17-client-exit-and-signed-updates.md`、`apps/macos/README.md`。 |
| 远端目录及通知 | 取消注入统一 `.pcr/时间戳/agent编号/` 研究目录规则，按项目/方案命名，不搬动在跑目录；通知只在实际全部工作结束时发，采用/停止/重开不能触发。`2026-10-18-loading-coherence-and-remote-layout.md`、`2026-10-18-client455-continuation-and-notifications.md`。 |

## 接手代码地图

- macOS：`apps/macos/Sources/PCRRemoteMac/`。`AppModel+Conversations/WorkspaceContext/ConversationPreload/RunState` 管导航和读模型；`RunControls/RunOperations` 管业务动作；`RunLaunch/FollowUps` 管输入和队列；`WorkspaceRunState.swift` 管内存/磁盘缓存；`DesktopState.swift` 管草稿/outbox/accepted handoff；`ConversationTimelineView.swift` 管 AppKit transcript；`AgentQuestions` 相关文件管问答。
- 本机服务：`src/parallel_codex_runner_core/remote/{ipc,controller,daemon}.py`；会话协议：`conversation_service.py`、`conversation_state.py`；执行/采用：`plugin_runtime.py`、`plugin/worker.py`、`finalization.py`；原生恢复与账号：`codex_app_server.py` 等（先 rg 实际调用链）。
- `pcrd` 与 persistent worker 是独立进程，客户端不是其 owner。Relay 是认证加密业务链路，不等于实际计算节点；只测公网HTTP不能证明远端作业健康。
- 产品文档和协议见 `README.md`、`remote/README.md`、`apps/macos/README.md`；历史问题按上述 QA 文件定位，不用全量读所有日志或私有数据库。

## 验证与发布

在仓库根目录执行原生门禁：

```sh
PCR_MAC_SDK=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk PCR_MAC_BUILD_JOBS=2 PCR_INTERACTIVE_QA_AUTHORIZED=0 bash apps/macos/scripts/test-client.sh
```

- 当前默认27.0 SDK曾报 SwiftUIMacros 缺失；26.5是已有可用工具链。聚焦测试使用同SDK、`--build-system native --disable-xctest --enable-swift-testing --no-parallel`，并添加 CommandLineTools 的 Swift Testing framework/rpath（现成命令在 `apps/macos/scripts/test-transcript-layout.sh`）。不要把构建环境失败报成回归失败，也不要忽略真实断言失败。
- Python受影响时运行对应 `tests/test_*.py`，再扩大到必要范围；本轮未改后端协议或执行逻辑。
- 签名身份仅用现有 `Parallel Codex Release Signing`，流程见 macOS README。不得要求用户提供内部密码、新建身份、改登录钥匙串/系统信任、降级验签。
- 发布必须新不可变版本：测试 -> 签名归档 -> 私有Relay正式目录readback -> 安装 -> 已加载字节 -> 实际交互，分别验收。不能把本地签名包说成服务器发布。近期认证更新IPC曾超时，之前使用精确已发布归档走未修改的原生验证器；不能声称认证下载已测通。
- 本轮辅助脚本在 `~/Library/Caches/ParallelCodexMaintenance/SaveOwnership-20261025/`：`release.py`、`channel.py`、`prepare_delivery.py`、`update_client.py`；部分复用 `Freeze-20261024/`。它们有固定版本和一次性标记，**不是可照跑的接手命令**，尤其不能重跑安装或旧强退脚本。正式构建入口是 `apps/macos/scripts/build-app.sh`，发布实现参考 `remote/tools`。`publication.json`、`installation.json`、`ui-acceptance.json` 分别记录发布、安装和真实界面验收。

## 不可破坏的边界

- 只修PCR，不代替五赛道做研究，不动 SiCLAB/Android/无关应用，不拿真实输入测试投递。查看、切页、关闭窗口不能采用结果。
- 原任务恢复保留 machine/workspace/run/物理Agent/native thread/turn/命令回执；先查 accepted、fingerprint、已有child和仍活着的远端作业。用户主动停止永不自动复活；状态不明只读取证，不整批重跑、不重复发已接受输入。
- 用户对旧问答执行器选了“不打断，等待安全边界”；对远端OOM选了“暂不调整”，不新增限流、不改并发，不重复询问同一授权。
- UI规则：执行摘要一行、不省略号；不向下撑开；锁定用小锁/灰态/禁用；不能随刷新清空输入。保存必须以业务回执为准，不能把本机缓存、accepted、running、PID存活当成最终成功。
- 更新前看真实全部窗口与持久 outbox、accepted handoff、草稿、队列、每Agent回答；正常后台退出，不抢焦点、不模拟键盘、不弹QA。只回退程序，不回滚已接受数据。daemon换版必须证明原detached workers、锁代次和在途操作安全，否则保留现场。
- 五赛道巡检原范围在 `~/Library/Caches/ParallelCodexMaintenance/CompetitionWatch/five-competitions-20261018/scope.json`；最近决定同目录 `policy-decision-20261018T015100Z.json`。本轮没有运行整轮维护或改变自动化，接手先核对最新人工指令和实际自动化状态，不复活历史暂停任务。
- 接手优先项：调查服务端慢响应和认证更新检查失败。入口为 `remote/controller.py::_turns_get` → `conversation_service.py::get_turn` → `plugin_runtime.py::get_run/_require_run_locked`；先定向取证具体调用与锁持有者，不能仅凭超时重启。474的已保存状态切页验收已通过，但没有另发真实保存测试，也未声称远端链路、所有工作区和未来时序全部验证。
- 本次技术协作仍在原 ChatGPT Web 会话 `https://chatgpt.com/c/6ad6b825-6ae8-83ec-883e-5a4672178530`，使用已核对的 Pro 最大强度；用于质疑边界及补回归，不是完成证明。用户要求非机械、诊断不明确时继续这一协作方式。
