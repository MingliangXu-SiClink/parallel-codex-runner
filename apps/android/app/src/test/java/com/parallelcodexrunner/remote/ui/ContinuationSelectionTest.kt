package com.parallelcodexrunner.remote.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ContinuationSelectionTest {
    @Test
    fun browsingDuringExecutionCannotOverrideCompletedRecommendation() {
        val browsing = WorkspaceRunSnapshot(status = "running", selectedAgent = 1)
        assertNull(automaticReviewAgent(browsing, false, true, 9, null))
        assertEquals(9, automaticReviewAgent(browsing, true, false, 9, null))
    }

    @Test
    fun reviewChoiceIsNotRepeatedlyOverwrittenByPolling() {
        val reviewed = WorkspaceRunSnapshot(status = "completed", selectedAgent = 2,
            reviewSelectionEstablished = true)
        assertNull(automaticReviewAgent(reviewed, true, false, 9, null))
    }

    @Test
    fun oldCacheIsMigratedAndAdoptedResultWins() {
        val oldCache = WorkspaceRunSnapshot(status = "completed", selectedAgent = 1)
        assertEquals(9, automaticReviewAgent(oldCache, true, false, 9, null))
        assertEquals(2, automaticReviewAgent(oldCache, true, false, 9, 2))
        assertNull(automaticReviewAgent(oldCache, true, true, 9, null))
    }
}
