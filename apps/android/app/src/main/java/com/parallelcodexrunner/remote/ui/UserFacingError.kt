package com.parallelcodexrunner.remote.ui

import com.parallelcodexrunner.remote.data.RemoteCommandException
import com.parallelcodexrunner.remote.data.RelayRequestException

internal fun isUnavailableConversationHistory(error: Throwable): Boolean =
    "is not resumable from" in error.message.orEmpty().lowercase()

internal fun friendlyError(
    error: Throwable,
    language: AppLanguage = AppLanguage.CHINESE,
): String {
    transientRelayConnectionMessage(error, language)?.let { return it }
    val message = error.message.orEmpty().trim()
    remoteFileSystemMessage(message, language)?.let { return it }
    val normalized = message.lowercase()
    return when {
        error is RelayRequestException && error.errorCode in setOf("device_inactive", "device_unknown", "refresh_revoked") ->
            language.text(
                "这台手机的连接授权已失效，请扫描电脑上的新配对码重新连接。",
                "This phone's connection authorization is no longer valid. Scan a new pairing code from your computer.",
            )
        error is RelayRequestException && error.errorCode == "user_inactive" ->
            language.text(
                "此账户已停用，请联系服务器管理员。",
                "This account has been disabled. Contact the server administrator.",
            )
        normalized.startsWith("saved files differ from the recorded result:") -> language.text(
            "这些文件与待保存结果不同，已保留你的文件，未重新覆盖：" +
                message.substringAfter(": ").removeSuffix(". No files were changed"), message)
        "no verification record" in normalized || "no complete verification record" in normalized -> language.text(
            "这次旧任务没有完整的保存核对记录。未改动你的文件，请手动检查后继续。", message)
        normalized.startsWith("git history or staged changes differ") ||
            normalized.startsWith("the destination folder was replaced") -> language.text(
                "原文件夹或版本记录已经改变，无法直接确认保存完成。已保留你的修改，请检查后继续。", message)
        error is RemoteCommandException && error.code == "incompatible_protocol" ->
            language.text(
                "目标设备上的 Parallel Codex 与当前手机版本不兼容，请更新两端应用。",
                "Parallel Codex on the target device is incompatible with this app. Update both apps.",
            )
        error is RemoteCommandException && error.code == "machine_offline" ->
            language.text(
                "这台设备当前未连接，请确认机器端在线后重试。",
                "This device is offline. Bring it online and try again.",
            )
        error is RemoteCommandException && error.code == "response_too_large" ->
            language.text(
                "返回内容过长，已停止加载这一页，请缩小查看范围后重试。",
                "This response is too large to load in one page. Narrow the range and try again.",
            )
        error is RemoteCommandException && error.code == "workspace_mismatch" ->
            language.text(
                "这个文件链接不属于当前工作区。",
                "This file link does not belong to the current workspace.",
            )
        error is RemoteCommandException && error.code == "follow_up_context_conflict" ->
            language.text(
                "这个工作区的另一段对话已有排队消息，请先打开现有队列。",
                "Another conversation in this workspace already has queued messages. Open the existing queue first.",
            )
        "run artifacts are not available" in normalized ||
            "saved agent output could not be verified" in normalized ->
            language.text(
                "这段历史工作过程已经清理，已保留仍可读取的对话内容。",
                "This historical work log has been cleaned up. Available conversation content is preserved.",
            )
        error is RemoteCommandException && error.code == "workspace_file_error" -> when {
            "larger than" in normalized -> language.text(
                "文件超过 256 MiB，暂时无法传到手机。",
                "Files larger than 256 MiB cannot currently be transferred to this phone.",
            )
            "working copy" in normalized -> language.text(
                "这个 Agent 的工作副本已经清理，文件无法再读取。",
                "This Agent workspace has been cleaned up, so the file is no longer available.",
            )
            "changed while" in normalized -> language.text(
                "文件在下载时发生了变化，请重新打开。",
                "The file changed while downloading. Open it again.",
            )
            "outside" in normalized -> language.text(
                "为保护工作区，不能打开指向工作区外部的文件。",
                "Links outside the workspace cannot be opened.",
            )
            "not a file" in normalized -> language.text(
                "这个链接不是可打开的文件。",
                "This link does not point to a file that can be opened.",
            )
            else -> language.text(
                "文件已经不存在，或当前无法从工作区读取。",
                "This file no longer exists or cannot currently be read from the workspace.",
            )
        }
        error is RemoteCommandException &&
            error.code in setOf("unsupported_action", "unknown_action") ->
            language.text(
                "目标设备上的 Parallel Codex 版本不支持此功能，请更新机器端。",
                "Parallel Codex on the target device does not support this action. Update it and try again.",
            )
        "pairing has expired" in normalized ||
            "pairing is unavailable" in normalized ->
            language.text(
                "配对码已过期，请在目标设备上生成新码后重新扫描。",
                "This pairing code has expired. Generate a new one on the target device and scan it again.",
            )
        "pairing authorization is invalid" in normalized ->
            language.text(
                "配对码无效，请在目标设备上生成新码后重新扫描。",
                "This pairing code is invalid. Generate a new one on the target device and scan it again.",
            )
        normalized.startsWith("pairing code ") ||
            normalized.startsWith("this is not a parallel codex pairing code") ||
            normalized.startsWith("server address ") ->
            language.text(
                "无法识别这个配对码，请在目标设备上生成新码后重新扫描。",
                "This pairing code could not be recognized. Generate a new one on the target device and scan it again.",
            )
        isRelaySessionRecoveryError(error) ->
            language.text(
                "暂时无法恢复连接，已保留配对信息，请稍后重试。",
                "The connection could not be restored. Your pairing is preserved; try again shortly.",
            )
        "timed out" in normalized || "timeout" in normalized ->
            language.text(
                "连接目标设备超时，请确认机器端和服务器均已连接后重试。",
                "The target device did not respond in time. Check the device and server connections, then try again.",
            )
        "websocket is not connected" in normalized ||
            "relay connection closed" in normalized ->
            language.text(
                "暂时无法连接目标设备，请稍后重试。",
                "The target device is temporarily unavailable. Try again shortly.",
            )
        "mac is offline" in normalized ->
            language.text(
                "目标设备暂未连接服务器，请确认设备已开机并运行机器端。",
                "The target device is not connected to the server. Make sure it is on and Parallel Codex Server is running.",
            )
        "offline queue is full" in normalized ->
            language.text(
                "服务器暂存的离线消息已满，请让目标设备联网后重试。",
                "The server's offline queue is full. Bring the target device online and try again.",
            )
        "request is too large" in normalized ||
            "encrypted payload is too large" in normalized ->
            language.text(
                "发送内容过长，请缩短后重试。",
                "This message is too large to send. Shorten it and try again.",
            )
        "active paired mac is required" in normalized ->
            language.text(
                "需要保留至少一台已配对的设备才能更新。",
                "Keep at least one paired device connected to install updates.",
            )
        "android updates are temporarily unavailable" in normalized ->
            language.text(
                "应用更新暂时不可用，请稍后重试。",
                "App updates are temporarily unavailable. Try again later.",
            )
        "relay request failed with http 404" in normalized ->
            language.text(
                "服务器暂未启用应用更新。",
                "App updates are not enabled on this server.",
            )
        "relay request failed with http 429" in normalized ->
            language.text(
                "操作过于频繁，请稍后再试。",
                "Too many requests. Try again shortly.",
            )
        Regex("relay request failed with http 5\\d\\d").containsMatchIn(normalized) ->
            language.text(
                "服务器暂时无法处理请求，请稍后重试。",
                "The server cannot process this request right now. Try again later.",
            )
        "update is unavailable" in normalized ->
            language.text(
                "这个更新已失效，请重新检查。",
                "This update is no longer available. Check for updates again.",
            )
        "android update checksum" in normalized ||
            "downloaded android update checksum" in normalized ->
            language.text(
                "安装包完整性校验未通过，已停止安装。",
                "The update failed its integrity check and was not installed.",
            )
        "signed by a different key" in normalized ||
            "certificate does not match" in normalized ->
            language.text(
                "安装包签名校验未通过，已停止安装。",
                "The update signature could not be verified and was not installed.",
            )
        "different android app" in normalized ||
            "belongs to a different app" in normalized ->
            language.text(
                "安装包身份校验未通过，已停止安装。",
                "This update belongs to a different app and was not installed.",
            )
        "interrupted finalization" in normalized ->
            language.text(
                "请先确认工作区同步状态并完成清理，再切换工作区。",
                "Confirm the interrupted workspace sync and finish cleanup before switching workspaces.",
            )
        "is not resumable from" in normalized ->
            language.text(
                "这段旧对话记录目前无法在此工作区打开。",
                "This earlier conversation is not currently available in this workspace.",
            )
        "finish or discard the current run" in normalized ||
            "finish or discard the current task" in normalized ->
            language.text(
                "请先采用或放弃当前任务，再切换工作区。",
                "Accept or discard the current task before switching workspaces.",
            )
        message == "Queued follow-ups require workspace sync-back" ->
            language.text(
                "排队继续需要开启工作区同步。",
                "Workspace sync must be enabled to continue queued prompts.",
            )
        message == "Choose a Mac first" -> language.text("请先选择一台设备。", "Choose a device first.")
        message == "Choose a workspace" -> language.text("请先选择工作区。", "Choose a workspace first.")
        message == "Choose a directory" -> language.text("请先选择文件夹。", "Choose a folder first.")
        message == "The selected device is disconnected" -> language.text(
            "这台设备已断开连接。",
            "The selected device is disconnected.",
        )
        message == "The target device could not create the folder" -> language.text(
            "目标设备未能创建文件夹。",
            "The target device could not create the folder.",
        )
        message == "Enter a prompt" -> language.text("请输入任务要求。", "Enter a message first.")
        message == "No active run" -> language.text("当前没有运行中的任务。", "There is no active task.")
        message == "Background submission service is unavailable" -> language.text(
            "应用后台发送服务尚未就绪，请重新打开应用后再试。",
            "Background submission is not ready. Reopen the app and try again.",
        )
        message == "The pending message is invalid" -> language.text(
            "待发送消息已损坏，请重新编辑后发送。",
            "The pending message is invalid. Edit it and send again.",
        )
        message == "The target device was removed" -> language.text(
            "目标设备已从设备列表移除，请重新连接后再发送。",
            "The target device was removed. Pair it again before sending.",
        )
        message == "This task requires storage confirmation" ||
            message == "此任务需要先确认可用存储空间。" -> language.text(
            "此任务需要先确认可用存储空间。",
            "Confirm available storage before starting this task.",
        )
        message == "The target device rejected this message" -> language.text(
            "目标设备拒绝了这条消息。",
            "The target device rejected this message.",
        )
        message == "Background submission was cancelled" ||
            message == "后台发送已取消，请重新发送。" -> language.text(
            "后台发送已取消，请重新发送。",
            "Background submission was cancelled. Send the message again.",
        )
        message == "Message could not be sent" ||
            message == "消息未能发送，请检查连接后重试。" -> language.text(
            "消息未能发送，请检查连接后重试。",
            "The message could not be sent. Check the connection and try again.",
        )
        message == "A message is still being submitted for this workspace" -> language.text(
            "这条消息仍在提交中，请发送完成或取消后再移除工作区。",
            "A message is still being submitted. Wait for it to finish or cancel it before removing this workspace.",
        )
        message == "A newly started task is still syncing for this workspace" -> language.text(
            "新任务已在设备上启动，正在同步到当前界面，请稍后再移除工作区。",
            "A new task has started and is still syncing. Wait before removing this workspace.",
        )
        message == "Queued prompts must be finished or cleared before removing this workspace" ->
            language.text(
                "请先完成或清空这个工作区排队中的提问，再移除工作区。",
                "Finish or clear this workspace's queued prompts before removing it.",
            )
        message == "The task must be stopped before removing this workspace" -> language.text(
            "请先停止这个工作区正在运行的任务，再移除工作区。",
            "Stop the task running in this workspace before removing it.",
        )
        message == "Workspace sync and cleanup must be resolved before removing this workspace" ->
            language.text(
                "请先确认工作区同步状态并完成清理，再移除工作区。",
                "Resolve workspace sync and cleanup before removing this workspace.",
            )
        message == "Mac did not return a run id" ||
            message == "Target machine did not return a run id" ->
            language.text(
                "目标设备未能创建任务，请重试。",
                "The target device could not create the task. Try again.",
            )
        message == "Scan a Mac pairing code first" ||
            message == "Scan a device pairing code first" ->
            language.text(
                "请先扫描目标设备上的配对码。",
                "Scan a pairing code from the target device first.",
            )
        message == "No Android update is available" -> language.text(
            "当前没有可安装的更新。",
            "No update is currently available.",
        )
        message in setOf(
            "The pending feedback image is corrupted",
            "待上传截图已损坏，请删除反馈后重新提交。",
        ) -> language.text(
            "待上传截图已损坏，请删除反馈后重新提交。",
            "A pending feedback image is corrupted. Delete this draft and submit it again.",
        )
        message in setOf(
            "The pending feedback image failed verification",
            "待上传截图校验失败，请删除反馈后重新提交。",
        ) -> language.text(
            "待上传截图校验失败，请删除反馈后重新提交。",
            "A pending feedback image failed verification. Delete this draft and submit it again.",
        )
        message in setOf(
            "The pending feedback image is missing",
            "待上传截图不存在，请删除反馈后重新提交。",
        ) -> language.text(
            "待上传截图不存在，请删除反馈后重新提交。",
            "A pending feedback image is missing. Delete this draft and submit it again.",
        )
        message in setOf(
            "The feedback connection has expired",
            "当前连接已失效，请重新连接后提交。",
        ) -> language.text(
            "当前连接已失效，请重新连接后提交。",
            "The connection has expired. Reconnect before submitting feedback.",
        )
        message == "relay_unreachable" -> language.text(
            "无法连接到中继服务器，请检查网络连接。",
            "Could not reach the relay server. Check your network connection.",
        )
        message == "relay_credentials_rejected" -> language.text(
            "服务器不再接受此设备的凭证，请重新配对。",
            "The server no longer accepts this device's credentials. Re-pair with the server.",
        )
        message == "relay_state_error" -> language.text(
            "中继状态已损坏或无法读取。",
            "The relay state is corrupted or unreadable.",
        )
        message == "workspace_sync_preflight_failed" -> language.text(
            "工作区同步前的检查失败，工作区未被改动。可以切换 Agent 或重试。",
            "Pre-sync checks failed. The workspace was not modified. You can switch agents or retry.",
        )
        message in setOf(
            "The feedback record no longer exists",
            "反馈记录已不存在，请删除后重新提交。",
        ) -> language.text(
            "反馈记录已不存在，请删除后重新提交。",
            "This feedback record no longer exists. Delete this draft and submit it again.",
        )
        message in setOf(
            "The feedback could not be submitted",
            "反馈内容无法提交，请检查后重试。",
        ) -> language.text(
            "反馈内容无法提交，请检查后重试。",
            "This feedback could not be submitted. Review it and try again.",
        )
        normalized.startsWith("the feedback service is temporarily unavailable") ||
            message.startsWith("反馈服务暂时无法接收内容") -> language.text(
            "反馈服务暂时不可用，反馈已安全保存在本机。",
            "The feedback service is temporarily unavailable. Your feedback is safely stored on this device.",
        )
        normalized.startsWith("the network is temporarily unavailable") ||
            message.startsWith("网络暂时不可用") -> language.text(
            "网络暂时不可用，反馈已安全保存在本机。",
            "The network is temporarily unavailable. Your feedback is safely stored on this device.",
        )
        message in setOf(
            "This workspace was removed from the target device",
            "这个工作区已从目标设备移除，排队消息已暂停。",
        ) -> language.text(
            "这个工作区已从目标设备移除，排队消息已暂停。",
            "This workspace was removed from the target device. The queued message is paused.",
        )
        message in setOf(
            "The target device could not safely finish workspace sync",
            "目标设备未能安全完成工作区同步，排队消息已暂停。",
        ) -> language.text(
            "目标设备未能安全完成工作区同步，排队消息已暂停。",
            "The target device could not safely finish workspace sync. The queued message is paused.",
        )
        message in setOf(
            "No Agent is available to continue this queued message",
            "没有可继续的 Agent，排队消息已暂停。",
        ) -> language.text(
            "没有可继续的 Agent，排队消息已暂停。",
            "No Agent is available to continue this queued message. The message is paused.",
        )
        message in setOf(
            "The queued message could not be sent yet",
            "暂时无法发送排队消息，稍后会自动重试。",
        ) -> language.text(
            "暂时无法发送排队消息，稍后会自动重试。",
            "The queued message could not be sent yet. It will retry automatically.",
        )
        message == "The conversation changed before this queued message could be sent. Open the queue to review it." -> language.text(
            "对话已发生变化，这条排队消息没有被发送。请打开队列确认后重试。",
            message,
        )
        message == "This workspace already has queued messages for another conversation. Open the existing queue before changing it." -> language.text(
            "这个工作区的另一段对话已有排队消息，请先打开现有队列。",
            message,
        )
        message.isBlank() -> language.text("操作失败，请重试。", "The action failed. Try again.")
        else -> language.text(message, message)
    }
}

internal fun isRelaySessionRecoveryError(error: Throwable): Boolean {
    val message = error.message.orEmpty().lowercase()
    return (error is RelayRequestException && error.errorCode in setOf(
        "access_invalid", "access_expired", "refresh_invalid", "refresh_expired",
    )) || "access token is invalid or expired" in message ||
        "refresh token is invalid or expired" in message
}

internal fun recoveredRelayMessages(language: AppLanguage): Set<String> = setOf(
    friendlyError(RelayRequestException(401, "Access token is invalid or expired", "access_invalid"), language),
    friendlyError(RelayRequestException(403, "Device is inactive", "device_inactive"), language),
    language.text(
        "配对已过期，请重新扫描目标设备上的配对码。",
        "This pairing has expired. Scan a new pairing code from the target device.",
    ),
)

internal fun friendlyStoredError(
    message: String,
    language: AppLanguage = AppLanguage.CHINESE,
): String {
    val trimmed = message.trim()
    if (trimmed.isEmpty()) return ""
    val retrySuffixes = listOf(
        ". Automatic retry is scheduled." to language.text(
            "，稍后会自动重试。",
            ". Automatic retry is scheduled.",
        ),
        " 将自动重试。" to language.text(
            "，稍后会自动重试。",
            ". Automatic retry is scheduled.",
        ),
        ". Check the connection and tap Retry." to language.text(
            "，请确认连接后点按重试。",
            ". Check the connection and tap Retry.",
        ),
        " 请确认连接后点按重试。" to language.text(
            "，请确认连接后点按重试。",
            ". Check the connection and tap Retry.",
        ),
    )
    retrySuffixes.firstOrNull { (raw, _) -> trimmed.endsWith(raw) }?.let { (raw, localized) ->
        val base = trimmed.removeSuffix(raw).trimEnd('。', '.')
        return friendlyError(IllegalStateException(base), language).trimEnd('。', '.') + localized
    }
    return friendlyError(IllegalStateException(trimmed), language)
}
