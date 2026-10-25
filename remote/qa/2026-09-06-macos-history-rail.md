# macOS 历史提问导航验收

日期：2026-09-06。目标版本：macOS 0.1.327（327）。

## 本轮范围

依据用户提供的两张客户端截图，增加对话左侧的历史提问刻度导航，不重新改动侧栏结构、输入框或运行流程。

- 默认仅显示细刻度；鼠标靠近时，目标及邻近刻度以弹簧动画伸长，并在旁边预览原提问。
- 点击刻度或预览只定位到对应问题，不切换历史 Turn、Agent，不产生采用或网络请求。阅读位置继续使用现有消息锚点保存。
- 当前阅读位置的刻度与悬停位置分开计算；浏览预览不会让正文跳动。
- 支持上下键浏览、Return 定位、Escape 关闭预览、原生辅助功能按钮，以及系统“减少动态效果”。
- 预览直接使用已经加载的提问，不制造占位加载动画。深浅色、字号和窄窗口使用同一套布局与颜色规则。

截图只说明视觉方向，不能据此声称复刻了官方客户端的动画曲线或使用了其源代码。本轮未重新申请或绕过 ChatGPT 原生客户端访问限制。

## 实现

- `ConversationHistoryIndex.swift`：按消息 ID 建立提问索引，同样的文字在不同回合仍是独立提问；不把回复或思考收录为问题。
- `ConversationHistoryRailView.swift`：独立 AppKit 覆盖视图与 Core Animation 图层，只在导航热区拦截点击，其余区域仍交给正文。预览、键盘和辅助功能共用定位入口。
- `ConversationTimelineView.swift`：复用现有语义滚动锚点；缓存问题的布局位置，滚动时二分查询当前问题。内容、窗口宽度或字号变化后才重新测量。
- 可见刻度数量按窗口高度设上限，而不是为几千条问题生成几千个动画图层。没有新增持续计时器，也不在每次悬停时发布整个 AppModel。

参考 Apple 的 [Motion 指南](https://developer.apple.com/design/human-interface-guidelines/motion) 与 [CASpringAnimation settlingDuration](https://developer.apple.com/documentation/quartzcore/caspringanimation/settlingduration)，采用局部、可中途重新定向的动画并尊重减少动态效果设置。

## 测试与构建

- `bash apps/macos/scripts/test-client.sh`：协议、桌面状态及 24 项原生测试通过。
- 最终刻度留白调整后再次运行 `bash apps/macos/scripts/test-transcript-layout.sh`：6 个 suite、24 项测试通过，测试执行 2.963 秒。
- 本轮新增 6 项原生测试：消息身份、5000 条问题的有界刻度和定位、阅读与悬停状态分离、辅助功能及动画、窄窗口与减少动态效果、真实 collection view 中定位后流式追加和上下文切换。
- 最终生产构建：`zsh apps/macos/scripts/build-app.sh`，44.89 秒；安装到 `/Applications/Parallel Codex.app` 后签名验证通过。
- `python3 scripts/build-release.py --reuse-clients` 完成统一归档；5 个 manifest 成品的大小和 SHA-256 均核对通过，最终 ZIP 解包签名验证通过。
- 安装版与归档版可执行文件逐字节一致，SHA-256 为 `5edc826bc4f003555633235df6553b80a3385080acae1524659588b57e4b3b35`。

测试脚本仍有现有的 Testing 框架部署版本链接警告。本轮没有把合成的 5000 条索引测试称为 5000 轮真实会话性能测试。

## 真实客户端走查

使用正式签名安装版，而不是预览窗口；未发送新消息、采用回复或改动对话正文。

- `测试文件夹` 中的 `BackendRestartQA-20260906-A.`：6 个问题可独立定位。鼠标点击及辅助功能点击可将 A/B 问题放到阅读区上方，已采用 Agent 1 不变。
- 上下键打开 C 的多行原提问预览时，正文仍停在 B；Escape 关闭预览。没有声称 Escape 在任意焦点来源下都一定返回输入框。
- 在输入框保留一段临时草稿后跳到 A，草稿未改变；验收结束后仅清除了该临时草稿，发送按钮恢复禁用。
- 退出并重新启动最终安装版，恢复 B 的既有阅读位置；记录的滚动值为 `0.4982078853046595`。
- 在浅色、特大字号及缩小窗口下检查预览的换行、对比度和范围，没有遮挡输入工具栏或溢出窗口。
- 原 `CodeX论文学习` 长对话显示 30 个提问索引。返回时仍为已采用 Agent 15，输入为空，底部滚动值为 1。
- 最后恢复原窗口大小、深色、标准字号、橙色主题、原工作区和对话，并将焦点返回消息输入框。测试对话也恢复到底部。
- 发布后从真实设置页点击“检查更新”，显示当前版本 0.1.327（327）和“已是最新版本”；同时独立读取 Relay 最新发布记录核对版本及文件哈希。

走查中实际发现并在最终构建前修复了两处问题：辅助功能导航按钮初始没有标为 enabled；预览文本使用尾部截断导致只显示一行。最终版已检查可点击的辅助功能按钮和正常多行预览。

当前界面工具没有原生单独移动鼠标或逐帧录像 API。实际应用验证了鼠标点击、键盘驱动预览与定位；鼠标悬停映射、局部弹簧动画和减少动态效果另有原生测试覆盖。没有宣称已经完成高帧率鼠标扫过录像或与 ChatGPT 的受控性能对比。

## 交付

- macOS：0.1.327（327）。
- 私有发布：`macos-release-7fc85a596b484ae59391f056418316b6`。
- 发布时间：`2026-09-06T06:20:02.326038+00:00`。
- ZIP：`release/clients/macos/Parallel-Codex-macos-0.1.327.zip`，8,083,590 字节。
- ZIP SHA-256：`66466953a3c1b16b0678efe598efe5a8d3c5d9532ecb82760c43ca249795108d`。
- 签名：`Parallel Codex Local Signing`，沿用既有 designated requirement。
- Android 仍为 0.1.259（260），APK 哈希未变；未操作模拟器或实体手机。
- 没有重启或部署 pcrd/Relay。统一打包重新生成了 pcrd wheel，不等于运行中的服务发生更新。
- 未提交 Git，也未覆盖或清理用户正在进行的仓库迁移。

本地验收图片位于 `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/history-rail-20260906-mLSfMV`：

- `macos-327-history-preview-dark.jpg`
- `macos-327-history-preview-light-large.jpg`
- `macos-327-history-preview-narrow.jpg`
- `macos-327-update-confirmed.jpg`

本轮完成的是历史提问导航，不代表整个 App 已达到参考客户端的全部产品完成度。后续受控性能对比、真实连续鼠标悬停的高帧率检查，以及更广的跨端整体设计验收仍需继续。
