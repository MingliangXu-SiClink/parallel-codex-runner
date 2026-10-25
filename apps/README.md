# Parallel Codex Apps

[简体中文](README.zh-CN.md)

The native clients are kept together here and share the **Parallel Codex** product
name. They connect to the Mac-side `pcrd` endpoint; neither client contains the
relay server implementation.

| App | Source | Local build |
| --- | --- | --- |
| macOS control center | [`macos/`](macos/) | `apps/macos/dist/Parallel Codex.app` |
| Android mobile workspace | [`android/`](android/) | `apps/android/dist/Parallel Codex.apk` |

Build the macOS app:

```bash
cd apps/macos
./scripts/build-app.sh
```

Build the Android app after configuring `local.properties`:

```bash
cd apps/android
./scripts/build-app.sh
```

The Mac endpoint and runtime are part of
[`src/parallel_codex_runner_core/remote/`](../src/parallel_codex_runner_core/remote/).
Protocol, relay, deployment, and security documentation is under
[`remote/`](../remote/).
