# Parallel Codex 应用

[English](README.md)

两个原生客户端统一放在这里，并使用 **Parallel Codex** 产品名称。它们连接 Mac
上的 `pcrd` 端点；中继服务端不属于任何一个客户端工程。

| 应用 | 源码 | 本地构建产物 |
| --- | --- | --- |
| macOS 控制中心 | [`macos/`](macos/) | `apps/macos/dist/Parallel Codex.app` |
| Android 移动工作台 | [`android/`](android/) | `apps/android/dist/Parallel Codex.apk` |

构建 macOS 应用：

```bash
cd apps/macos
./scripts/build-app.sh
```

配置 `local.properties` 后构建 Android 应用：

```bash
cd apps/android
./scripts/build-app.sh
```

Mac 端点与运行逻辑位于
[`src/parallel_codex_runner_core/remote/`](../src/parallel_codex_runner_core/remote/)，共享协议、
中继服务、部署和安全文档位于 [`remote/`](../remote/)。
