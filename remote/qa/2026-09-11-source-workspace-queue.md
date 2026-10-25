# 单 Agent 源目录执行与队列等待时间验收

## 已交付行为

- 1 个候选 Agent、0 个综合 Agent、启用同步回原工作区、关闭保留候选工作区时，新回合直接以源项目目录为 cwd，不创建 `workspaces/agent_001`。
- 多 Agent、有综合 Agent、关闭同步或保留候选工作区，仍使用隔离工作区。旧回合保持原执行方式，不在执行中搬迁文件。
- 源目录模式保留一份用于差异审查的 baseline；采用结果后删除该 baseline，保留已归档差异，绝不删除源目录。停止或放弃结果不自动回滚已写入的源文件。
- 源目录执行和其他结果同步共用项目写入锁；正在直接执行的回合不能临时追加并行候选。
- 修改等待秒数会更新服务器上尚未派发的消息。当前倒计时从修改生效时重新按新秒数计时；设为 0 时允许立即派发。已经派发的消息不受影响，不覆盖暂停状态和失败重试信息。

## 构建与发布

| 组件 | 实际版本 | 结果 |
| --- | --- | --- |
| Android | 0.1.262 / 263 | 签名 release 构建、模拟器覆盖安装、Relay 应用内更新发布成功 |
| macOS | 0.1.333 / 333 | 签名构建、安装到 /Applications、启动与恢复验证成功；更新归档已发布 |
| 本机 pcrd | 0.1.40 | 安装到实际服务使用的 miniconda Python，重启服务，实时 capabilities 返回 0.1.40 |

Relay 未修改。发布清单在 `release/manifest.json`。实际安装的 pcrd 与发布 wheel 的 307 个 Python 源文件逐一比较，无差异。

- Android 发布 ID：`android-release-b83476b9ae3045d688237df1b85fdf61`
- Android SHA-256：`de320cd7b577272946a165c83c645cfac04a3fb2e87c6dd5e42ca0e44873842e`
- macOS 发布 ID：`macos-release-6d9dead5da2c4aa59d2674413d7dc663`
- macOS SHA-256：`3d82fdd2ab0e148a4a3445e84c6dedf02a2b926b30398c2967f75abf3d9694bb`

## 自动测试

- Python：核心执行 296、Plugin 生命周期 89、源目录/队列/remote 160、协议/发布/Relay 契约/模型缓存/工作区配置 41，共 586 项通过，另有 13 个 subtest 通过。这是相关回归集，不是全部仓库测试。
- 核心执行测试关闭了非测试目标的真实模型缓存预热；模型缓存自身的测试单独运行。源目录单测使用真实本地子进程模拟 Agent，下面的客户端验收使用真实 Codex。
- Android：239 项单测通过，0 失败，release 构建通过。
- macOS：协议/状态测试脚本通过，52 项原生测试通过，签名 strict 验证通过。

## 真实客户端验收

仅使用已配对的 Android 35 模拟器 `emulator-5554`，未操作实体手机。macOS 通过原生 UI 操作。

测试工作区：`ws-da14fca3a5f748f8`，目录位于用户缓存中的 `source-workspace-queue-20260911/SourceQueueQA-20260911`。原有 `existing.txt` 保留标记 `USER-EDIT-MUST-STAY`。

1. 从 Android 新建并发送第一条消息：`pcr-20260911T085445Z-d198a6ad`。真实 Codex 的 `command.json.cwd` 等于源目录；任务仍执行时 `qa-first.txt` 已出现在源目录，没有候选目录。
2. 第一轮运行中从 Android 排队第二条消息，服务器确认消息 ID `ac320099-39f5-45f0-9030-399cc774836c`。
3. 等待第一轮完成后，Android 将倒计时从 180 改为 240 秒；macOS 查看同一消息并改为 300 秒；Android 同步看到 300，再改为 20 秒。四份服务器快照中，deadline 与更新时间之差分别严格为 180、240、300、20 秒。
4. 20 秒设置被服务器确认后，强制停止 Android，并退出 macOS。检查两个客户端进程均不存在。后台在到期后完成上一轮采用并派发 `pcr-20260911T085906Z-aa8ee2ac`；没有打开客户端推动派发。
5. 第二轮在源目录生成 `qa-second.txt`，沿用上一轮 Codex session，仅执行一次。队列变为 idle，记录已消费消息 ID，错误为空。
6. 重新打开两端：都显示第一条提问、`SOURCE-FIRST-OK`、第二条提问、`SOURCE-SECOND-OK`，队列不再出现。Android 点击采用后，两轮均 finalized，候选审查目录已清理，三个源文件仍完整。第二轮归档差异只含新增 `qa-second.txt`。
7. Android 在设置中的应用更新入口检查，实际显示“当前已是最新版本 0.1.262。”；Relay 最新目录也返回 263、available=true 和相同 APK 摘要。验收后关闭本轮启动的模拟器，并恢复 macOS 原来的工作区页面。

## 证据与范围

本轮原始证据目录：`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/source-workspace-queue-20260911`。

包含倒计时 JSON、退出后队列与运行快照、Android 恢复截图、旧版本备份及构建产物。macOS 的倒计时、退出恢复和跨端内容另有本轮原生 UI 走查记录。未修改真实业务文件，未清空配对信息，未重置用户工作树。

本轮没有对其他电脑的 pcrd 执行部署，也没有声称在实体 Android 手机上验收。使用其他电脑时，该电脑的 pcrd 同样需要升级到 0.1.40。
