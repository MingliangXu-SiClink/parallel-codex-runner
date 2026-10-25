# Parallel Codex 产品链路验收

日期：2026-09-05 至 2026-09-06，Asia/Shanghai。下文 Run 和队列时间保留服务记录的 UTC。

## 结论

本轮不是仅编译验证：在已安装 macOS 客户端和已配对 Android 模拟器中，执行了新建、发送、运行中切换 Agent、未采用结果直接续问、后台队列、退出恢复及跨端查看。走查中定位并修复了 7 个客户端问题，修复后重新构建、安装并私有发布。

**核心链路已实际跑通，剩余两项后台修复也已激活并复验。** 经用户批准，在无运行任务时更新并重启 Mac 后台服务；新隔离对话首次未采用结果续问实测准备 4.114 秒，旧轮询不再覆盖新对话设置。另修复 macOS 工作过程文字错位并发布 0.1.325。性能已有现场基线，但没有实体 Android 手机和 ChatGPT 原生客户端的受控对比，不能据此宣称“体验领先”。下文保留重启前的失败样本，新增验收见“后台激活与对齐复验”。

## 交付版本

| 组件 | 版本 | 实际状态 |
| --- | --- | --- |
| macOS | 0.1.325 / build 325 | 正式构建、本地签名、覆盖安装、启动、设置内检查更新及私有发布完成 |
| Android | 0.1.259 / code 260 | 正式签名构建、模拟器覆盖安装、真实链路验证、设置内检查更新及私有发布完成 |
| pcrd | 0.1.37，更新后的代码 | 已重装、重启；旧空闲 worker 已退出，新任务使用更新后的 worker；304 个已安装 Python 文件与发行 wheel 一致 |
| Relay | 本地 wheel 0.1.18 | 本轮未升级服务代码；只使用既有私有版本发布接口 |

Android 私有发行记录：`android-release-15ef7b77d7d24e75aca305b2a1d1b231`，发布时间 `2026-09-05T16:51:32.294548Z`。

macOS 私有发行记录：`macos-release-071740cc6bf44c249c09967ea541d1bb`，发布时间 `2026-09-06T01:44:23.046655Z`。上一轮 324 的记录为 `macos-release-fa681a1f31de4e27ae26ca835e0cbd86`。

发行物位于统一 `release/`，`manifest.json` 中全部文件的 SHA-256 已逐一复核。客户端下载仍需已连接设备身份，没有新增公开下载地址。

| 文件 | SHA-256 |
| --- | --- |
| `clients/android/Parallel-Codex-android-0.1.259.apk` | `85bb065c0bed635db026eac9f6591c7b94f5f5df30562e7fe02e0a5b604debff` |
| `clients/macos/Parallel-Codex-macos-0.1.325.zip` | `acce05453362b6e7d268e45c537f550270ce59ae6698743b3773ba8e4004cc9a` |
| `pcrd/parallel_codex_runner-0.1.37-py3-none-any.whl` | `a4977a55d90c9eb443aadc1375eace0ed7d1937bf94c19654583139595d36d22` |

macOS 继续使用 `Parallel Codex Local Signing`，安装后的 `codesign --verify --deep --strict` 通过。这不是 Apple Developer ID 公证发行，不能把本机可用等同于任意新 Mac 无提示安装。

## 现场发现与根因修复

| 问题 | 根因 | 修复与复核 |
| --- | --- | --- |
| macOS 发送续问后仍能采用旧回复 | 输入提交与采用操作使用不同可用性判断 | UI 和提交入口共享 pending submission 限制；单次发送从当前未采用的非推荐 Agent 继续，不再给出重复采用入口 |
| macOS 自己启动的新回合短暂被判定为“另有新回合” | active Run 与 Workspace 汇总不是同时更新，旧请求还可能晚到 | 新回合交接原子更新 Workspace 汇总并使旧刷新失效；N/P 两轮实测不再出现错误恢复操作 |
| macOS 新回合首次切换 Agent 跳到旧回合位置 | 滚动缓存键没有 Run，复用了同号 Agent 的旧位置 | 改为机器、工作区、对话、Run、Agent 五维键并迁移旧位置；N/P 首次看 Agent 2 定位最新，同一回合重新进入保留原阅读位置 |
| macOS 冷启动缓存同步时闪出“新回合等待处理” | 尚未完成身份同步被当作真正禁止续问 | 独立 loading 状态保留缓存正文，不显示错误或误导的恢复/采用操作；状态回归及最终安装冷启动通过。未做逐帧录像，不能承诺所有场景绝无单帧闪烁 |
| Android 退出后选中的历史对话变成最新对话 | 正在查看的 Run 与 Workspace 最新 Run 混用 | 独立持久化 viewed Run；实际历史 E/G 强制退出恢复正确，不被较新对话抢走 |
| Android 停留在已结束对话时看不到 Mac 新回复 | 只检查设备在线状态，停止的 Run 轮询不会发现 Workspace 下一轮 | 前台任务页每 10 秒进行有上限的 Workspace 汇总刷新；工作区页 5 秒，其他页面不启动该轮询。N/P 在 Android 前台无需手动刷新自动出现 |
| Android 续问把上一轮问题改写成新问题 | 内容键缓存持有可变 MarkdownState；稳定的当前问题行被复用时解析器同时修改旧缓存 | 用 `key(content)` 隔离解析状态的生命周期，保留内容缓存；设备回归修复前失败、修复后通过。真实 S→T→U 中旧问题、新问题各显示一次 |

关键代码：macOS 的 `ConversationPresentation.swift`、`PromptSubmissionPlan.swift`、`AppModel+Presentation.swift`、`AppModel+RunLaunch.swift`、`WorkspaceModels.swift`、`TranscriptScrollSupport.swift`；Android 的 `MobileStateStore.kt`、`DomainStateRepositories.kt`、`RunPollingActions.kt`、`WorkspacePaneStore.kt`、`WorkspaceScreen.kt`、`MarkdownTranscript.kt`。

修改保持在上述状态所有权、交接及渲染生命周期范围内，没有重写整个客户端，也没有回退用户已有的目录迁移和其他修改。

## 完整链路记录

测试工作区：桌面 `测试文件夹`，工作区 ID `ws-3d0decde47e84b1f`。只向独立 QA 对话发送只读提示，没有向用户原有研究对话发送消息。

主验收对话 `pcr-20260905T151905Z-87deab88`，标题 `ProductFlowQA-20260905-I。`，累计 12 轮，索引 0 至 11。结论来自客户端界面和后台结构化记录，不采用 Agent 自己输出的“检查通过”清单作为证据。

| 步骤 | 实际操作与证据 | 结果 |
| --- | --- | --- |
| 新建、发送 | 新建独立 I，对话 ID 与其他 QA 历史不同；创建于 `15:19:05.608154Z`，两位 Agent 实际运行 | 通过 |
| 运行中切换 Agent | I 中鼠标及 Command-Shift-[ 切换；P 中 Agent 仍运行时切换 Agent 2，当前内容和选中项更新 | 通过；未触发隐式采用 |
| 不手动采用直接续问 | I 推荐 Agent 2，查看 Agent 1 后只点一次发送，J 的续接来源是 I Agent 1 | 功能通过；准备耗时 115.303 秒，不满足速度要求 |
| 从非推荐 Agent 2 续问 | L 推荐 Agent 1，选择 Agent 2 后一次发送创建 M；来源明确是 L Agent 2 | 通过，准备耗时 5.066 秒 |
| Mac 排队后两端退出 | K 队列项 `dc270b3a-586d-43c7-be9a-53733df2ee93` 于 `15:24:03.947Z` 提交；确认 Mac 与 Android 进程均不存在后，后台于 `15:25:03.987038Z` 创建 K | 通过，不依赖 App 前台打开 |
| Android 排队后退出 | T 运行中提交 U，界面显示已排队 1 条；服务队列 revision 42 保存相同 item ID | 通过，队列不是仅存在手机内存 |
| 后台队列完成 | U 项 `f72ea59d-651a-46e9-a00a-e7cfd6236691`；T 于 `16:44:02.602725Z` 采用，U 于 `16:44:09.387425Z` 创建、`16:44:49.552682Z` 完成；队列 revision 47 为空且无错误 | 通过 |
| 退出恢复 | Mac 完全退出后恢复当前轮和原阅读位置；Android force-stop 后重新进入保留选定历史。两端恢复 U，前面各轮仍在 | 通过本次样本 |
| 跨端查看 | Android 前台自动发现 Mac N/P；Android 创建 Q/R/S/T/U 后 Mac 恢复查看同一 Conversation 的正确轮次 | 通过，无手动刷新要求 |
| 客户端更新 | 两端在最终安装版本进入设置并主动检查更新，分别显示最新 0.1.324 和 0.1.259；Relay 发行记录及 SHA 与本地匹配 | 通过；没有操作实体手机 |

U 的 Run ID 为 `pcr-20260905T164409Z-0c8beaff`，属于上述 Conversation 的第 12 轮，续接 T Agent 1。Android 在 force-stop 后由 WorkManager 的 SystemJobService 于 `16:43:44.661Z` 重启了后台进程，因此这一例只能说明“两端界面关闭时队列有效”，不能声称两端进程始终不存在。K 的两端进程退出证据是独立案例。

测试结束已恢复测试工作区原设置（Agent 1、max、综合 Agent 0、队列延迟 60 秒）、Mac 原工作区、原草稿和原阅读位置。没有修改或发送原草稿。

## 构建与回归

| 检查 | 结果 |
| --- | --- |
| Android 单元测试 | 236 项，0 失败、0 错误、0 跳过 |
| Android 模拟器设备测试 | 7 项，0 失败；包含先失败后修复的 Markdown 缓存污染回归 |
| Android lintDebug | 通过 |
| Android 最终生产构建 | 通过，33 秒；签名 APK 已覆盖安装及私有发布 |
| macOS 协议与桌面状态测试 | 通过，包括 pending、loading、Run 汇总交接与滚动缓存迁移 |
| macOS 原生测试 | 15 项通过 |
| macOS 最终生产构建 | 通过，37.69 秒；签名、安装与启动通过 |
| Python 全量回归 | 此工作阶段此前运行 596 项通过；后台激活前另跑 10 项针对性回归，安装后 3 项通过，另有真实 IPC 与客户端验收 |
| 发行物完整性 | manifest 全部 SHA-256 校验通过 |
| Git 空白检查 | `git diff --check` 通过；大量客户端目录仍未跟踪，因此不把该命令当作全部源码审查 |

没有创建 Git 提交；现有未跟踪文件及迁移状态完整保留。

## 性能现场基线

### Android

设备是已配对 API 35 / Android 15 arm64 模拟器，1080×2400，RAM 2560 MiB，gfxstream / Skia OpenGL。不是实体小米手机。

最终 release 259 在 12 轮 QA 对话上，以 `dumpsys gfxinfo` 清零后连续 20 次上下滑动采样：

| 指标 | 观测值 |
| --- | --- |
| 采样帧数 | 511 |
| deadline janky | 3 / 0.59% |
| legacy janky | 162 / 31.70% |
| 帧耗时 P50 / P90 / P95 / P99 | 18 / 22 / 23 / 29 ms |
| 长尾 | 150 ms 桶 1 帧，200 ms 桶 1 帧 |
| GPU P50 / P95 / P99 | 17 / 22 / 25 ms |
| Total PSS | 51,040 KiB，约 49.8 MiB |
| Total RSS | 163,596 KiB |
| `am start -W` 启动样本 | 323 ms、261 ms |

启动数据只反映 Activity 启动，不等于对话正文已同步完成。两种 jank 口径都保留，不能只选较好看的 0.59%。P95 已超过 60 Hz 的 16.7 ms 帧预算，并有明显长尾；还需要实体设备 Perfetto 定位，不能标为“滚动性能达标”。

### macOS

324 的渲染和滚动修复构建中，在 QA 长对话实际滚动、将窗口从 1283×709 拖到 1003×694 再拖回：静态截图中正文重新排版、输入区固定、无控件重叠，操作没有被运行状态禁止。

- idle CPU 采样为 0.0%；vmmap physical footprint 100.5 MiB，峰值 215.3 MiB。
- 上述走查后 footprint 99.3 MiB，峰值仍为 215.3 MiB。
- 累计 CPU 时间增加 1.64 秒，但采样跨度含约 110 秒工具等待，因此不能用它推算单次交互延迟或 FPS。
- 这些性能采样在最后的冷启动 loading 状态修复之前完成；最终安装另外核对了启动、更新检查、原草稿和位置恢复。
- 323 的早先 190.3 MiB 是不同缓存、不同会话条件，不能据此声称本次内存降低约一半。
- 没有取得 macOS 逐帧 resize / scroll trace；没有测出可与 ChatGPT 对齐的 FPS、输入延迟或首字延迟。

重启前的续问准备时间差异很大：J 冷路径 115.303 秒、L 已采用热路径 2.234 秒、M 未采用热路径 5.066 秒。重启后使用新隔离对话 A→B 验证首次路径为 4.114 秒；不能把单次样本当作稳定 P95 指标。

## 整体设计实际对比

### 参考的证据等级

- **ChatGPT 官方原生客户端：未完成现场操作。** 本轮界面工具访问被拒绝，没有改用其他方式绕过。参考限于[官方 macOS 发布说明](https://help.openai.com/en/articles/9703738-macos-app-release-notes)，不能声称复制了其开源原生客户端或完成性能对比。
- **Chatbox：实际操作官方 Web 客户端。** 打开 [Chatbox Web](https://web.chatboxai.app/)，进入 Markdown 101 示例，查看公式、表格、消息操作和固定输入框，收起侧栏并观察重新排版；没有登录或发送私人内容。这是 Web 对比，不是其原生桌面性能验证。
- **开源架构：读了实际源码。** [Chatbox action-gates](https://github.com/chatboxai/chatbox/blob/main/packages/chatbox-core/src/session/action-gates.ts) 将会话操作可用性集中判断；[Jan useThreads](https://github.com/janhq/jan/blob/main/web-app/src/hooks/useThreads.ts) 分离线程集合、当前选择及搜索索引。吸收的是状态组织方式，不是机械照搬视觉或复制整段实现。
- **平台实践：** 对照 [Android 渲染性能文档](https://developer.android.com/topic/performance/vitals/render)，保留采样设备和帧指标口径，不将模拟器数值等同实体手机。

| 维度 | 实际对比判断 | 本轮落实及仍需改进 |
| --- | --- | --- |
| 导航与可用空间 | Chatbox 的可收起侧栏释放正文空间；本次窄浏览器窗口中展开侧栏也明显压缩内容，不能盲目照搬固定宽度 | Mac 保持原生可拖侧栏；Android 对话页没有底部一级导航。跨工作区搜索、最近对话与工作区入口层级仍值得单独设计验证 |
| 输入与状态 | 成熟对话产品需要一种稳定发送语义，不能发送中还暴露另一条采用路径 | 统一 pending/loading 状态与采用约束；后台修复已激活，首次续问准备实测 4.114 秒，仍需更大样本与首字延迟测量 |
| 正文与内容可信度 | Chatbox 示例的用户输入、公式、表格及逐消息操作有清晰归属；仅视觉美化不能弥补历史问题被改写 | 修正 Android 解析器缓存污染；保留正文而不是用 loading 空页替换。长 Markdown 的帧耗时仍需优化 |
| 候选与阅读位置 | 多 Agent 是本产品特有需求，不能把通用聊天客户端直接贴皮 | 历史分支固定、当前候选可浏览；新增 Run 级位置隔离，避免“Agent 2”跨轮误用缓存 |
| 错误与引导噪音 | Chatbox 此次出现登录提示和 Web Search 引导覆盖内容，属于不应照搬的干扰 | Mac 同步状态不再当作错误弹出。仍需系统检视设置页说明、重复状态文案和真实不可续问错误的呈现 |
| 跨端一致性 | 共享会话语义不等于共享导航外观；Android 系统返回与 Mac 菜单快捷键应各自原生 | 手机独立恢复浏览位置并及时发现 Mac 新轮；Mac 保留菜单和键盘操作，不追加占满一行的动作条 |

本轮没有以“全面美化”为名重排整个界面。实际对比导出的首要改进是可预测的状态和阅读连续性；整体视觉、可访问性与性能竞争力仍不是已完成项。

## 后台激活与对齐复验

### 后台更新

- 用户批准后先核对无 active Run、无 active operation、队列为空。备份原安装包、LaunchAgent、工作区及队列记录；没有改 Relay 配置、身份密钥或 LaunchAgent 参数。
- 原 pcrd PID 89594 与已取消测试的空闲 worker 19479 均退出。新服务 PID 80819 于本地时间 `2026-09-06 01:44:09` 启动，Relay 恢复连接。最终复查服务 `ok`、无运行任务、无启动告警，10 个工作区和 6 个可信设备仍在。
- 安装时 wheel SHA 为 `b7318d365223b857e9f39a70d846d23181aaa539fab1d325378739d60fd59e8a`。打包 macOS 325 时重新生成统一发行目录，wheel 归档 SHA 变为上表的 `a497...`；304 个 Python 文件逐字节比对一致。包版本仍是 0.1.37，不声称本轮提升了后台版本号。
- 生效的两项修复：容量估算在采用前使用当前候选的隔离会话，不再遍历无关全局历史；已完成会话提升的旧 Run 轮询不再反复改写工作区 RESUME。

### 真实验收

新增独立 Conversation `pcr-20260905T174927Z-7ad4459b`，标题 `BackendRestartQA-20260906-A.`。A 至 E 通过 macOS 输入框发送，F 是两端关闭后的真实 IPC 入队探针，不冒充手机或 Mac UI 点击。

| 项目 | 实际证据 | 结果 |
| --- | --- | --- |
| 新隔离对话首次续问 | A 未采用时单次发送 B，点击 `17:50:55.877Z`，B 创建 `17:50:59.991092Z`；B 续接 A Agent 1 | 准备 4.114 秒；不用第二次发送 |
| 另一条未采用续问 | C 未采用时单次发送 D，点击 `01:34:37.653Z`，D 创建 `01:34:43.655288Z` | 准备 6.002 秒 |
| UI 入队 | B 运行时提交 C、D 运行时提交 E，界面及后台记录均确认入队；分别完成，队列为空且无错误 | 通过；C 的倒计时结束至创建为 5.203 秒 |
| 两端进程退出后的独立调度 | F 入队前确认 Mac 进程不存在、Android 模拟器已关闭；持续采样覆盖 waiting、countdown、dispatching、创建及 completed，两端进程始终不存在 | `01:40:23.915Z` 倒计时结束，`01:40:30.616163Z` 创建 F，`01:41:38.396718Z` 完成；准备 6.701 秒 |
| 旧轮询与新对话设置 | 在旧 U 和新 F 分别是工作区最近已采用回合时，各测试显式 NO、另一会话 ID；每种交替执行 runs.get / turns.get 六次 | 共 24 次真实 IPC 读取均不覆盖用户选择，原配置恢复 |
| 退出恢复与跨端查看 | Mac 325 重开恢复 A 至 F；Android 259 模拟器重开后无需重新配对即显示 D/E/F、BACKEND-F-OK 及已采用 Agent 1 | 通过，未操作实体手机 |

C 测试期间用户重新打开了 Mac，Android 的后台进程也曾重启，不能作为“两端进程全程退出”的证据。E 的退出采样开始晚于派发，只证明后续完成阶段；严格的全过程证据采用 F，明确其通过 IPC 入队。测试不使用 Agent 自称的“验证完成”作为通过依据。

### macOS 文字对齐

- 真实 324 界面中，普通回复起点约 x=408，思考 x=428，工作过程摘要 x=382。根因是思考、正文各按 720 / 760 宽度居中，命令和状态却固定靠左并各加内边距；窗口越宽，错位越明显。
- 325 统一非用户消息的文字阅读列。思考、过程摘要、命令及正文共用文字起点，图标放在独立左侧位置，背景内边距不再推动文字；用户问题仍右对齐。窗口调整走同一布局计算，不按消息类型另外居中。
- 新原生布局回归在旧代码出现 224 次错位断言失败，修复后通过；覆盖 7 种行、4 种字号、明暗外观、320/640/920/1400 宽度及原位缩放。协议、桌面状态及全部 16 项原生测试通过。
- 最终生产构建 32.32 秒，签名、覆盖安装、私有发布及真实更新检查通过。亲自展开工作过程，对照思考、sleep 命令和最终回复；1283×709、1003×709 窗口及浅色模式都保持相同文字起点。没有以静态截图承诺所有动画逐帧零闪烁。
- 原窗口尺寸、深色外观、标准字号恢复；Mac 原 MouseViewDataset 对话、未发送草稿和滚动值 `0.9407891185836886` 保留。测试工作区队列延迟恢复 60 秒。

备份及机器可读证据在 `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/pcrd-20260906-dQFqCM`：`resume-poll-result.json`、`resume-poll-f-result.json`、`queue-f-enqueue.json`、`queue-f-monitor.json`、Android AX 记录。界面截图：[宽窗口](</Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/pcrd-20260906-dQFqCM/macos-325-alignment-wide.png>)、[窄窗口](</Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/pcrd-20260906-dQFqCM/macos-325-alignment-narrow.png>)、[浅色](</Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/pcrd-20260906-dQFqCM/macos-325-alignment-light.png>)、[Android 跨端恢复](</Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/pcrd-20260906-dQFqCM/android-backend-f.png>)。

## 未完成项与下一步

1. **性能验收补齐。** 实体 Android 的长会话 Perfetto、Mac Instruments resize/scroll/input trace，以及相同设备条件下 ChatGPT 原生操作对比均未完成。需要相应设备/应用访问，不绕过工具拒绝。
2. **扩大可靠性样本。** 本轮单 Mac + 单 Android 模拟器、12 轮主对话及 6 轮后台复验不是多机器并发、超长历史分页、断网时采用、重复投递和升级中断的穷尽验证。首次续问延迟应扩大样本统计，而不是拿 4.114 秒作为保证。
3. **保留旧报告与用户改动。** 本仓库仍处目录迁移与大量未跟踪状态；应单独审查后建立 Git 基线，不在此次验收中强行提交或清理。
