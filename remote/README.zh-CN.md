# PCR Remote

[English](README.md)

PCR Remote 在不把项目源码上传到中继服务器的前提下，把 PCR 拆成彼此独立的机器端和客户端：

- **机器端：** `pcrd` 可运行在 macOS 或 Linux，负责浏览目录、管理 Workspace、运行 Codex、创建隔离副本和回写结果。
- **中继服务端：** 完成一次性二维码身份校验和设备配对，只转发端到端加密的请求与响应。
- **Android 与 macOS 客户端：** 切换已连接的机器，浏览目标机器目录，管理多个 Workspace，启动或继续任务，按时间顺序查看 Agent 对话，并明确选择要采用的 Agent。

当前代码已经包含跨平台机器控制层、原生 SwiftUI 客户端、Android 客户端和中继服务。两个用户端统一放在顶层的 [`apps/`](../apps/) 目录；本目录只保留共享协议、中继服务与部署配置。两个客户端共用每个 Workspace 的运行设置和结构化对话 Item；通信协议有版本号；修改操作具备持久化去重；设备通过二维码双向确认密钥；离线消息只以密文形式保存在中继服务。

在 macOS 上，完成通知由长期运行的 `pcrd` 负责。即使 SwiftUI App 已经关闭，只要所有 Agent 都进入终态（允许部分失败或被终止），系统仍会以 Workspace 名称提醒；守护进程重启后也会继续处理尚未送达的完成转换。

## 每个 Agent 独立提问

`pcrd` 0.1.95、macOS 0.1.438 和 Codex app-server 0.159.2 及以上支持新原生执行中的结构化问题。问题显示在所属 Agent 的对话里，回答直接交回原生请求，不创建新提问或重发原任务。其他 Agent 继续运行；非阻塞问题也不会把提问者标成暂停。已有执行不会因此迁移或重启。

只有具备回答能力的客户端创建的运行才开放该工具。新增的 `client_capabilities: ["agent_questions"]` 是传输层能力声明，经加密 Relay 原样传递并记入 run 所有权记录，不改变原消息正文、命令 ID 或指纹。旧客户端和独立 CLI 保持原有非交互行为，不会产生无人能够回答的新问题。

`runs.agent` 增加 `input_requests`；只读 `runs.question` 查询原问题回执；`runs.answer_question` 将不可变的明确回答绑定到原 run、Agent、问题和原生连接。客户端先保存选择，再投递，并在重试前核对原问题回执。服务端继续按命令身份去重，不默认选择或自动同意。

客户端断线或 IPC 重建不会使仍然有效的原生问题失效。原生 owner 消失时，旧问题才失效：已保存的选择仍保留，但不自动重放给新进程或新问题。本机保存、服务端接受和原生接收是不同事实，无法确认交付时不伪称已经采用。权限审批、采用结果仍是独立操作。新增字段不改变旧客户端的读取；新客户端连接旧服务时保留原有阅读和任务操作。

## 本地开发

安装当前代码并启动机器端服务：

```bash
python3 -m pip install -e .
pcrd serve
```

在另一个终端检查状态和目录访问：

```bash
pcrd status
pcrd call filesystem.roots
pcrd call filesystem.list --body '{"path":"~","directories_only":true}'
pcrd call workspaces.add --body '{"path":"~/Desktop/my-project"}'
```

开发环境只需为机器端预配置一次，不再要求普通用户在客户端填写服务器信息：

```bash
export PCR_RELAY_MACHINE_ENROLLMENT_TOKEN="..."
pcrd configure-server --url https://pcr.example.com --name "Studio Computer"
```

配置完成后，在机器端直接显示一次性二维码，再使用 Android App 扫码：

```bash
pcrd pair-device
```

命令会等待手机完成双向校验并明确显示配对结果；Linux 不需要安装 macOS App。

发布包中的 `pcrd/install-pcrd.sh` 会为各版本建立独立 Python 环境，不修改用户的公共环境。
macOS 目录是 `~/Library/Application Support/parallel-codex-runner/runtime/<版本>`，
Linux 目录是 `${XDG_DATA_HOME:-~/.local/share}/parallel-codex-runner/runtime/<版本>`。
维护服务时使用该环境的 `bin/pcrd`；安装器支持用 `PCR_RUNTIME_ROOT` 指定运行环境根目录。

安装新版 wheel 后再次运行该环境的 `pcrd install-service` 会更新服务定义并重启机器端，
确保 Linux 不会继续运行升级前已经加载到内存中的旧代码。

机器端的日常维护不依赖 macOS App：

```bash
pcrd service-status
pcrd service-logs --lines 200
pcrd restart-service
pcrd uninstall-service
```
`pcrd relay-enroll`、`pcrd relay-poll` 和 `pcrd relay-status` 仍保留给协议开发和诊断使用。旧环境变量 `PCR_RELAY_MAC_ENROLLMENT_TOKEN` 继续兼容，但新安装应使用机器端命名。

把当前 wheel 安装成独立用户服务。相同命令会在 macOS 创建 LaunchAgent，
在 Linux 创建 systemd 用户服务：

```bash
pcrd install-service \
  --server-url https://pcr.example.com
# 不再需要时：pcrd uninstall-service
```

本地 Unix Socket、认证 Token、命令日志和 Workspace 注册表都保存在当前用户的应用状态目录，权限仅对当前用户开放。互联网通信使用另一套设备配对和端到端加密密钥。

## Workspace 访问方式

PCR Remote 不要求先在机器端建立目录白名单。配对后的客户端可以浏览运行 `pcrd` 的账户有权访问的任意目录，包括挂载卷，并把任意目录加入 Workspace 列表。最终是否可读仍由目标系统的文件权限决定；在 macOS 访问受保护目录时，可能还需要给安装 `pcrd` 的 Python 或可执行文件授予“完全磁盘访问权限”。

从 Android 端移除 Workspace 只会删除 PCR 的快捷记录，不会删除原始目录。

## Android 私有更新通道

仍与至少一台有效机器端保持配对的手机，可以通过中继服务检查并安装新版
Android App。APK 不会放进任何公网下载目录。下载需要手机的登录 Token，
还需要一个与当前 Android 设备、同一账户下的有效机器端、具体版本和 APK
SHA-256 绑定的短时单次票据。票据只放在请求 Header 中，不写进 URL。

这里校验的是有效配对关系，不要求目标机器此刻在线或保持唤醒；一旦手机或
机器端被移除，后续票据签发和下载都会立即被拒绝。Android 端下载完成后会
再次核对文件大小、SHA-256、应用包名、版本号和签名继承关系，最后交给
Android 系统安装界面由用户确认，不会静默安装。服务端配置与发布命令见
[`server/README.md`](server/README.md)。

## 目录结构

```text
apps/
  macos/          原生 SwiftUI 客户端
  android/        Kotlin / Compose 多机器、多 Workspace 客户端

remote/
  protocol/       三端共用的通信与加密协议
  server/         设备身份校验和密文 WebSocket 中继
  infra/          FRP、TLS、PostgreSQL 与 systemd 部署模板

src/parallel_codex_runner_core/remote/
  protocol.py             请求与响应格式
  filesystem.py           按目标机器当前账户权限浏览目录
  workspace_registry.py   多 Workspace 持久化
  journal.py              修改命令的持久化去重
  controller.py           明确列举的 PCR 远程操作
  ipc.py                  带本地认证的 Unix Socket
  daemon.py               pcrd 命令行与服务入口
  crypto.py               X25519 与 AES-GCM
  relay_config.py         一键配对所需的本机私有服务器配置
  relay_http.py           配对、Token 刷新与设备发现
  relay_bridge.py         pcrd 持有的加密 WebSocket 桥接
```

完整设计见 [ARCHITECTURE.md](ARCHITECTURE.md)，远程目录访问和上线前必须处理的事项见 [SECURITY.md](SECURITY.md)。
