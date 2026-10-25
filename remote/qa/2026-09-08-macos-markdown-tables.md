# macOS 含公式表格显示验收

## 真实问题与根因

在已安装的 0.1.328 中检查用户当前 CodeX论文学习对话、综合 1 的两张表格，并通过只读 `runs.agent` 核对原始 Markdown。原始回答的三列表格及公式正确，错误发生在客户端渲染，未修改用户回答或运行记录。

1. 行首公式被替换成 `NSTextAttachment` 时丢失原来的段落属性，其中包含 `NSTextTableBlock`。公式跑到表格外，剩余两个单元格被重新排成两列，表头与正文错位。公式渲染失败的回退也丢失表格属性。
2. 行内公式编号重复计算全局偏移，而独立公式使用全局数量直接编号。当前回答 10 个公式只有 9 个唯一占位符；第二张表的 `0.5 × 0.5 = 25%` 和后面的联合分布公式使用同一编号，替换时互换位置。不是模型算错或服务端串回答。
3. Markdown 表格列对齐信息被忽略，`---:` 的概率列没有右对齐。

## 修复

- 行内、独立公式使用统一的全局序号，保持文档顺序且不重复。
- 替换公式和失败回退都继承占位文字的段落、单元格、字体、链接等属性，不再重置为普通段落。
- 从 Foundation 的表格列意图读取左、中、右对齐，同时用于表头、普通文字和公式。参考 [Apple TableColumn.Alignment](https://developer.apple.com/documentation/foundation/presentationintent/tablecolumn/alignment-swift.enum)。
- 布局度量缓存 schema 从 6 升到 7，避免修复后继续使用旧的错误表格高度。未清除用户对话缓存。
- 仅修改 macOS 的 `LatexMarkdown.swift`、`TranscriptTextRenderer.swift`、布局缓存版本及 App 版本；未改侧栏、输入框、运行流程、Android 或后台服务。

## 回归与实际客户端验证

- 新增 `MarkdownTableTests.swift`。最初 4 项测试在旧代码上均失败：重复公式编号、公式丢失单元格、回退与链接属性丢失、行列拆开；另补充列对齐测试。
- `bash apps/macos/scripts/test-client.sh`：协议、桌面状态及 8 套原生测试共 32 项通过。最后增加单元格不得越出容器的断言后，原生 32 项再次通过，耗时 8.457 秒。
- 实际 NSTextStorage / NSLayoutManager 排版覆盖深浅色、4 种字号及 760 → 360 → 620 → 760 宽度变化；两张表分别保持 9/15 个单元格，同行同高、不重叠、不越界。
- 最终生产构建 38.89 秒，原本地证书签名、覆盖安装和严格签名验证通过。

| 实际操作 | 验收结果 |
| --- | --- |
| 安装后打开原长对话 | 两张表均恢复完整三列，第一张两行数据、第二张四行数据正常 |
| 核对第二张表 | 第一行概率显示 `0.5 × 0.5 = 25%`，其余三行为 25%，没有被另一公式替换 |
| 下滚核对正文独立公式 | 正确显示两种动作组合各 50% 的联合分布，不再显示表内概率计算 |
| 深色标准字号 | 表头、网格、公式、右对齐概率列正常 |
| 浅色特大字号 | 公式可读且保持在对应单元格，行列仍然对齐 |
| 实际拖动窗口至较窄和最小宽度，再拉回 | 表格随正文宽度变化，无内容横向重叠或挤出边框 |
| 再次完全退出并重新启动 | 原会话、综合 1、空草稿与表格阅读位置恢复，修复后布局缓存正常 |
| 应用内主动检查更新 | 当前 0.1.329（329），已是最新版本 |

最终恢复深色、标准字号、普通窗口宽度、294 点侧栏、原会话和综合 1。主界面留在两张表格完整可见的位置；再次启动后的滚动值为 0.9910033524193788。未发送、采用、重试或修改任何用户消息。设置与结果面板关闭。

## 安装与发布

- macOS 0.1.329（329）已安装到 `/Applications/Parallel Codex.app`，使用 `Parallel Codex Local Signing`。
- 私有发布 `macos-release-d56acf3de9104f57b164fc7076e43bce`，时间 `2026-09-08T08:27:27.873209+00:00`。
- ZIP 大小 8,105,434 字节，SHA-256 `12be2b89e581267c8d493cd8b7e4cd7d97126f951b3a3e1dac6c17fac11fe559`。再次查询 Relay 最新版本确认相同且未撤销。
- `python3 scripts/build-release.py --reuse-clients` 更新统一归档；5 个产物尺寸与 SHA 全部通过，安装版与 ZIP 内可执行文件、Info.plist 完全一致。首次上传连接被关闭，重试后成功；上传临时文件已删除。
- Android 保持 0.1.259（260），APK SHA 保持 `85bb065c0bed635db026eac9f6591c7b94f5f5df30562e7fe02e0a5b604debff`。归档重打了后台 wheel，但没有部署或重启服务。

## 证据与限制

仅本机保存的截图、设置与恢复状态，以及修改前源码和安装版备份：

`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/markdown-tables-20260908-gggsG2`

- `before.jpg`：旧版公式跑出单元格。
- `after-dark-both-tables.jpg`、`final-dark-both-tables.jpg`：修复后的两张表。
- `after-joint-formula.jpg`：表格下方独立公式的位置与内容。
- `after-light-extra-large.jpg`、`after-light-narrow.jpg`、`after-light-minimum-width.jpg`：浅色大字号及缩窄窗口。
- `after-relaunch.jpg`、`final-state.txt`：再次退出恢复。
- `update-check.txt`：真实设置页版本与更新检查。

本轮未做 CPU/GPU 性能对比或高帧率录像，也未验证所有可能的超宽表格和超长公式。原生测试仍有既存的 Testing.framework 部署目标链接警告。设置切换过程中出现过 AX 状态失效，重新读取后继续操作；没有将该工具现象另列为已修复的产品问题。未提交 Git，也未回退用户的仓库迁移改动。
