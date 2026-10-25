# macOS Agent 结果详情验收

## 范围与观察

用户反馈 Agent 旁 info 按钮打开的界面不好看。本轮先操作已安装的 0.1.327：默认窗口中，结果界面占满对话区域，先列出 16 个候选，真正的当前结果信息被压到最下方，还需要再次展开。问题在信息结构，而不只是颜色或圆角。

本轮仅重做 macOS 结果检查器，没有改动侧栏导航、输入框、运行、采用、服务端或 Android 的业务流程。参考 [Apple Panels](https://developer.apple.com/design/human-interface-guidelines/panels) 对上下文检查器的定位和 [Disclosure Controls](https://developer.apple.com/design/human-interface-guidelines/disclosure-controls) 的信息层级原则；不宣称复制了 ChatGPT 源码或完成其原生客户端对比。

## 修改

- 打开即显示当前 Agent、实际状态、推荐标记、耗时、推理用量，删除重复的长候选列表及第二层详情折叠。
- 顶部采用 Agent 名称、原生候选菜单和前后切换箭头。普通候选与综合结果分组；浏览其他候选不会采用它。
- 用量明细按每次 tokens、次数、合计对齐；汇总使用全部推理步骤的加权和，未知值与零区分。
- 文件修改独立展示，保留完整 Diff 入口，覆盖运行中、加载、无修改和不可用状态。长路径换行并提供完整路径提示。
- 工作过程、更多和原有采用/停止/重试等操作保留在底部，仍使用原来的业务判断。切换尚未完成时禁用这些操作，避免针对旧 Agent 执行。
- 普通窗口使用有尺寸上限的悬浮面板，打开不重新排版正文；足够宽时为并排检查器。支持 Escape 及面板外点击关闭。
- 真实大字号走查发现原生 Menu 不遵循自定义标题字号，最终将可缩放的 Agent 标题与原生菜单触发器分开，避免标题异常细小。

主要文件：`ConversationResultsInspector.swift`、新增 `ConversationResultDetails.swift`、`ConversationResultActions.swift`、`ConversationWorkspaceView.swift` 和既有视觉样式常量。

## 构建与发布

- `bash apps/macos/scripts/test-client.sh`：协议测试、桌面状态检查及 7 个原生测试套件共 27 项通过。新增 3 项结果检查器回归；最终原生测试耗时 4.630 秒。
- 新增测试覆盖推理汇总、非有限耗时、未知值、窄尺寸约束，以及中英文、深浅色、4 种字号下的实际详情 View 渲染。渲染测试使用合成数据，不等同于真实服务器任务。
- `zsh apps/macos/scripts/build-app.sh`：最终生产构建 37.92 秒。使用原 `Parallel Codex Local Signing` 签名，退出客户端后覆盖安装到 `/Applications/Parallel Codex.app`，严格签名验证及重新启动通过。
- `python3 scripts/build-release.py --reuse-clients`：5 个归档产物全部校验尺寸与 SHA-256；macOS ZIP 解包签名通过，归档版本和安装版本均为 0.1.328（328），主可执行文件逐字节一致。
- 私有发布：`macos-release-5953292162b44263856caacbefceeb6b`，时间 `2026-09-08T07:52:53.830289+00:00`。再次读取 Relay 最新记录，版本、尺寸及 SHA 与本地相同，未撤销。
- ZIP：8,104,662 字节；SHA-256：`f8ab23bd71eae9c5e829a0b0710c093e0ee9a99129f8761c7d3d55540c2819b9`。
- 真实安装版设置页主动检查更新：当前 0.1.328（328），已是最新版本。上传的远端临时 ZIP 已删除。
- Android 仍为 0.1.259（260），APK SHA 保持 `85bb065c0bed635db026eac9f6591c7b94f5f5df30562e7fe02e0a5b604debff`。统一归档重打了后台 wheel，但没有安装或重启后台服务。

## 真实客户端验收

在原 CodeX论文学习工作区的 31 次提问长对话内验证，没有发送新问题或采用任何结果。

| 操作 | 实际结果 |
| --- | --- |
| 点击 Agent info | 直接显示当前综合 1 的信息，不再出现全屏候选长列表 |
| 菜单选择 Agent 15 | 显示 1m 14s、409 tokens；对话仍标记已采用综合 1 |
| 下一候选返回综合 1 | 显示 3m 1s、2,604 tokens；明细为 1545×1、516×2、27×1 |
| 打开更多菜单 | 原复制对话、复制工作过程入口可见，没有执行这些操作 |
| 打开后 Escape 关闭 | 面板关闭，原对话及空草稿不变；最终滚动值前后均为 0.9907873577901681 |
| 点击面板外区域 | 紧凑面板关闭，阅读位置不变 |
| 显示/隐藏侧栏改变可用宽度 | 分别使用悬浮与并排布局；没有文字/按钮重叠 |
| 浅色与特大字号 | 信息、数值、标题与底部操作均可读，内容不越出面板 |
| 最终构建重启 | 原工作区、会话及所选综合 1 恢复；未产生新回合 |

测试结束恢复深色、标准字号、294 点侧栏、综合 1、空草稿、折叠工作过程及原滚动值。设置和结果面板已关闭。

## 证据与限制

仅本机保存的前后截图、版本更新 AX 结果及最终状态：

`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/result-inspector-20260908-Nr76ET`

- `before.jpg`：原 327 界面。
- `final-dark.jpg`：最终普通窗口。
- `final-dark-wide.jpg`：最终宽布局。
- `final-light-extraLarge.jpg`：最终浅色特大字号。
- `update-check.txt`、`final-state.txt`：更新检查与最终恢复状态。
- 同目录保留修改前源码和安装版备份。

本轮真实任务为已采用且文件产物不可用的会话；修改文件列表的视觉覆盖来自合成渲染测试，失败任务未做真实走查，没有为美化测试而新建会修改文件的任务。未测量帧率或开展新的 CPU/GPU 性能对比，不声称解决了整个客户端的所有显示问题。过程中 AX 设置按钮操作曾未打开窗口，原生 Command-, 正常；未将尚未定位的工具/焦点问题列为已修复。测试工具仍有既存的 Testing.framework 部署目标链接警告。
