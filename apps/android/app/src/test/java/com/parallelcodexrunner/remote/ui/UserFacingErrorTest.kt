package com.parallelcodexrunner.remote.ui

import com.parallelcodexrunner.remote.data.RemoteCommandException
import com.parallelcodexrunner.remote.data.RelayRequestException
import java.net.ConnectException
import org.junit.Assert.assertEquals
import org.junit.Test

class UserFacingErrorTest {
    @Test
    fun expiredSessionDoesNotClaimThatDevicePairingExpired() {
        for (error in listOf(
            RelayRequestException(401, "Access token is invalid or expired", "access_invalid"),
            RelayRequestException(401, "Refresh token is invalid or expired", "refresh_expired"),
            RelayRequestException(401, "Credential is unavailable", "refresh_invalid"),
        )) {
            assertEquals("暂时无法恢复连接，已保留配对信息，请稍后重试。", friendlyError(error))
            assertEquals(
                "The connection could not be restored. Your pairing is preserved; try again shortly.",
                friendlyError(error, AppLanguage.ENGLISH),
            )
        }
    }

    @Test
    fun expiredQrAndRevokedDeviceHaveDifferentActions() {
        assertEquals(
            "配对码已过期，请在目标设备上生成新码后重新扫描。",
            friendlyError(RelayRequestException(410, "Pairing has expired", "pairing_expired")),
        )
        for (code in listOf("device_inactive", "device_unknown", "refresh_revoked")) {
            assertEquals(
                "这台手机的连接授权已失效，请扫描电脑上的新配对码重新连接。",
                friendlyError(RelayRequestException(403, "Authorization rejected", code)),
            )
        }
        assertEquals(
            "此账户已停用，请联系服务器管理员。",
            friendlyError(RelayRequestException(403, "User disabled", "user_inactive")),
        )
    }

    @Test
    fun successfulReconnectClearsOldAuthenticationErrorsButNotUnrelatedErrors() {
        val messages = recoveredRelayMessages(AppLanguage.CHINESE)
        assertEquals(true, "配对已过期，请重新扫描目标设备上的配对码。" in messages)
        assertEquals(true, friendlyError(RelayRequestException(401, "Token failed", "refresh_invalid")) in messages)
        assertEquals(false, friendlyError(IllegalStateException("Pairing has expired")) in messages)
        assertEquals(false, "自定义错误说明" in messages)
    }

    @Test
    fun protocolAndOfflineErrorsDoNotExposeInternalMessages() {
        assertEquals(
            "目标设备上的 Parallel Codex 与当前手机版本不兼容，请更新两端应用。",
            friendlyError(
                RemoteCommandException("incompatible_protocol", "internal protocol mismatch")
            ),
        )
        assertEquals(
            "这台设备当前未连接，请确认机器端在线后重试。",
            friendlyError(RemoteCommandException("machine_offline", "Mac is offline")),
        )
    }

    @Test
    fun transportAddressesAreHiddenFromUsers() {
        assertEquals(
            TRANSIENT_RELAY_CONNECTION_MESSAGE,
            friendlyError(ConnectException("Failed to connect to /8.153.193.1:443")),
        )
    }

    @Test
    fun invalidPairingDetailsAreHiddenFromUsers() {
        assertEquals(
            "无法识别这个配对码，请在目标设备上生成新码后重新扫描。",
            friendlyError(IllegalArgumentException("Pairing code is missing mac_key")),
        )
    }

    @Test
    fun relayServerFailuresDoNotExposeHttpInternals() {
        assertEquals(
            "服务器暂时无法处理请求，请稍后重试。",
            friendlyStoredError("Relay request failed with HTTP 500"),
        )
        assertEquals(
            "操作过于频繁，请稍后再试。",
            friendlyStoredError("Relay request failed with HTTP 429"),
        )
    }

    @Test
    fun unavailableResumeHistoryDoesNotExposeSessionOrWorkspaceDetails() {
        val error = IllegalStateException(
            "Codex session 01a02e88 is not resumable from /Users/example/Project",
        )

        assertEquals(true, isUnavailableConversationHistory(error))
        assertEquals(
            "这段旧对话记录目前无法在此工作区打开。",
            friendlyError(error),
        )
        assertEquals(
            "This earlier conversation is not currently available in this workspace.",
            friendlyError(error, AppLanguage.ENGLISH),
        )
    }

    @Test
    fun unknownUsefulMessagesRemainAvailable() {
        assertEquals(
            "自定义错误说明",
            friendlyError(IllegalStateException("自定义错误说明")),
        )
    }

    @Test
    fun englishLanguageNeverFallsBackToChineseForKnownErrors() {
        assertEquals(
            "This device is offline. Bring it online and try again.",
            friendlyError(
                RemoteCommandException("machine_offline", "Mac is offline"),
                AppLanguage.ENGLISH,
            ),
        )
        assertEquals(
            "The server cannot process this request right now. Try again later.",
            friendlyStoredError(
                "Relay request failed with HTTP 500",
                AppLanguage.ENGLISH,
            ),
        )
    }

    @Test
    fun persistedFeedbackErrorsFollowTheCurrentLanguage() {
        assertEquals(
            "网络暂时不可用，反馈已安全保存在本机，稍后会自动重试。",
            friendlyStoredError(
                "The network is temporarily unavailable. Automatic retry is scheduled.",
            ),
        )
        assertEquals(
            "The network is temporarily unavailable. Your feedback is safely stored on this device. Check the connection and tap Retry.",
            friendlyStoredError(
                "网络暂时不可用，反馈已安全保存在本机。 请确认连接后点按重试。",
                AppLanguage.ENGLISH,
            ),
        )
    }

    @Test
    fun changedConversationQueueErrorFollowsTheCurrentLanguage() {
        val message =
            "The conversation changed before this queued message could be sent. Open the queue to review it."
        assertEquals(
            "对话已发生变化，这条排队消息没有被发送。请打开队列确认后重试。",
            friendlyStoredError(message),
        )
        assertEquals(message, friendlyStoredError(message, AppLanguage.ENGLISH))
    }

    @Test
    fun queueContextConflictFollowsTheCurrentLanguage() {
        val message =
            "This workspace already has queued messages for another conversation. Open the existing queue before changing it."
        assertEquals(
            "这个工作区的另一段对话已有排队消息，请先打开现有队列。",
            friendlyError(RemoteCommandException("follow_up_context_conflict", message)),
        )
        assertEquals(
            "Another conversation in this workspace already has queued messages. Open the existing queue first.",
            friendlyError(
                RemoteCommandException("follow_up_context_conflict", message),
                AppLanguage.ENGLISH,
            ),
        )
        assertEquals(
            "这个工作区的另一段对话已有排队消息，请先打开现有队列。",
            friendlyStoredError(message),
        )
    }
}
