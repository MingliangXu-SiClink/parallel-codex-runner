package com.parallelcodexrunner.remote.data

import com.parallelcodexrunner.remote.ui.AppLanguage
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class RunCompletionWorkerTest {
    @Test
    fun completionNotificationFollowsTheSelectedLanguage() {
        assertEquals(
            "所有 Agent 已完成",
            runCompletionNotificationText(0, AppLanguage.CHINESE),
        )
        assertEquals(
            "All Agents finished; 2 did not complete",
            runCompletionNotificationText(2, AppLanguage.ENGLISH),
        )
    }

    @Test
    fun notificationPermissionIsRequiredOnlyOnAndroid13AndNewer() {
        assertEquals(true, completionNotificationPermissionSatisfied(32, false))
        assertEquals(false, completionNotificationPermissionSatisfied(33, false))
        assertEquals(true, completionNotificationPermissionSatisfied(33, true))
    }

    @Test
    fun completionNotificationsUseTheFullRunIdentity() {
        // "Aa" and "BB" deliberately share a Java hash code. A numeric hash ID
        // would let one completed run replace the other in the notification tray.
        assertEquals(
            "pcr-run-completed-mac-a-workspace-a-Aa",
            runCompletionNotificationTag("mac-a", "workspace-a", "Aa"),
        )
        assertEquals(
            "pcr-run-completed-mac-b-workspace-b-Aa",
            runCompletionNotificationTag("mac-b", "workspace-b", "Aa"),
        )
    }

    @Test
    fun runningTaskDoesNotNotify() {
        val run = buildJsonObject {
            put("status", "running")
            put("agents", buildJsonArray {
                add(buildJsonObject { put("status", "success") })
            })
        }

        assertNull(completedRunFailedAgentCount(run))
    }

    @Test
    fun terminalTaskCountsFailedAndKilledAgents() {
        val run = buildJsonObject {
            put("status", "completed")
            put("agents", buildJsonArray {
                add(buildJsonObject { put("status", "success") })
                add(buildJsonObject { put("status", "failed") })
                add(buildJsonObject { put("status", "killed") })
            })
        }

        assertEquals(2, completedRunFailedAgentCount(run))
    }

    @Test
    fun terminalRunStatusStillWaitsForEveryAgent() {
        val run = buildJsonObject {
            put("status", "completed")
            put("agents", buildJsonArray {
                add(buildJsonObject { put("status", "success") })
                add(buildJsonObject { put("status", "running") })
            })
        }

        assertNull(completedRunFailedAgentCount(run))
    }

    @Test
    fun interruptedTaskIsTerminal() {
        val run = buildJsonObject {
            put("status", "interrupted")
            put("agents", buildJsonArray {
                add(buildJsonObject { put("status", "interrupted") })
            })
        }

        assertEquals(1, completedRunFailedAgentCount(run))
    }
}
