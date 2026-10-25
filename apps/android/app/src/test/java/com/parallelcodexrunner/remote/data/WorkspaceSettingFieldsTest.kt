package com.parallelcodexrunner.remote.data

import org.junit.Assert.assertEquals
import org.junit.Test

class WorkspaceSettingFieldsTest {
    @Test
    fun changedFieldsReportsOnlyValuesThatActuallyChanged() {
        val previous = RemoteRunSettings(
            agents = 4,
            model = "luna",
            resumeSessionId = "session-old",
        )
        val updated = previous.copy(
            agents = 6,
            model = "sol",
        )

        assertEquals(
            setOf(WorkspaceSettingFields.AGENTS, WorkspaceSettingFields.MODEL),
            updated.changedFields(previous),
        )
    }

    @Test
    fun mergePreservesOnlyDirtyLocalFields() {
        val local = RemoteRunSettings(
            agents = 6,
            model = "luna",
            resumeSessionId = "session-old",
        )
        val remote = RemoteRunSettings(
            agents = 4,
            model = "sol",
            resumeSessionId = "session-new",
        )

        val merged = local.mergingRemote(
            remote = remote,
            preservingFields = setOf(WorkspaceSettingFields.AGENTS),
        )

        assertEquals(6, merged.agents)
        assertEquals("sol", merged.model)
        assertEquals("session-new", merged.resumeSessionId)
    }

    @Test
    fun maxParallelModeAndValueAreAlwaysSyncedAsOneSetting() {
        val automatic = RemoteRunSettings(
            automaticMaxParallel = true,
            maxParallel = 4,
        )
        val manual = automatic.copy(automaticMaxParallel = false)

        assertEquals(
            setOf(
                WorkspaceSettingFields.AUTOMATIC_MAX_PARALLEL,
                WorkspaceSettingFields.MAX_PARALLEL,
            ),
            manual.changedFields(automatic),
        )
        assertEquals(
            setOf(
                WorkspaceSettingFields.AUTOMATIC_MAX_PARALLEL,
                WorkspaceSettingFields.MAX_PARALLEL,
            ),
            WorkspaceSettingFields.normalized(
                setOf(WorkspaceSettingFields.AUTOMATIC_MAX_PARALLEL)
            ),
        )
    }
}
