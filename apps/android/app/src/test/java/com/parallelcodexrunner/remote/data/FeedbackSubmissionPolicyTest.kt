package com.parallelcodexrunner.remote.data

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class FeedbackSubmissionPolicyTest {
    @Test
    fun automaticRetriesAreBounded() {
        assertTrue(feedbackShouldRetryAutomatically(0))
        assertTrue(feedbackShouldRetryAutomatically(6))
        assertFalse(feedbackShouldRetryAutomatically(7))
        assertFalse(feedbackShouldRetryAutomatically(100))
    }
}
