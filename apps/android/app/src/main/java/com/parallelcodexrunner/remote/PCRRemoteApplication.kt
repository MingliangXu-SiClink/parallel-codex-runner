package com.parallelcodexrunner.remote

import android.app.Application
import androidx.work.Configuration
import com.parallelcodexrunner.remote.data.EnvelopeCrypto
import com.parallelcodexrunner.remote.data.FollowUpQueueScheduler
import com.parallelcodexrunner.remote.data.FeedbackSubmissionScheduler
import com.parallelcodexrunner.remote.data.MobileStateStore
import com.parallelcodexrunner.remote.data.PCRRepository
import com.parallelcodexrunner.remote.data.PromptSubmissionScheduler
import com.parallelcodexrunner.remote.data.PendingFeedbackSubmission
import com.parallelcodexrunner.remote.data.RelayApi
import com.parallelcodexrunner.remote.data.RelayTransport
import com.parallelcodexrunner.remote.data.RunCompletionNotifications
import com.parallelcodexrunner.remote.data.RunCompletionScheduler
import com.parallelcodexrunner.remote.data.SecureStore
import com.parallelcodexrunner.remote.ui.WorkspaceRunDiskCache

class PCRRemoteApplication : Application(), Configuration.Provider {
    override val workManagerConfiguration: Configuration
        get() = Configuration.Builder().build()
    lateinit var repository: PCRRepository
        private set
    lateinit var mobileStateStore: MobileStateStore
        private set
    internal lateinit var workspaceRunCache: WorkspaceRunDiskCache
        private set
    lateinit var promptSubmissionScheduler: PromptSubmissionScheduler
        private set
    lateinit var runCompletionScheduler: RunCompletionScheduler
        private set
    lateinit var followUpQueueScheduler: FollowUpQueueScheduler
        private set
    lateinit var feedbackSubmissionScheduler: FeedbackSubmissionScheduler
        private set
    internal lateinit var networkConnectivityMonitor: NetworkConnectivityMonitor
        private set
    var secureStoreRecoveryNotice: String? = null
        private set
    @Volatile
    var uiForeground: Boolean = false
    private set

    override fun onCreate() {
        super.onCreate()
        networkConnectivityMonitor = NetworkConnectivityMonitor(this)
        val secureStore = SecureStore(this)
        secureStore.prepare()?.let { report ->
            secureStoreRecoveryNotice = if (report.resetIdentity) {
                "手机的安全存储已失效。已保留恢复副本，请重新配对设备。"
            } else {
                "已隔离无法读取的本地缓存，其余配对和工作区不受影响。"
            }
        }
        mobileStateStore = MobileStateStore(secureStore)
        workspaceRunCache = WorkspaceRunDiskCache.create(
            noBackupFilesDir.resolve("workspace-run-cache-v1"),
            secureStore,
        )
        val crypto = EnvelopeCrypto(secureStore)
        val relayApi = RelayApi(secureStore, crypto)
        repository = PCRRepository(
            relayApi,
            RelayTransport(relayApi, crypto, secureStore),
        )
        promptSubmissionScheduler = PromptSubmissionScheduler(this)
        runCompletionScheduler = RunCompletionScheduler(this)
        followUpQueueScheduler = FollowUpQueueScheduler(this)
        feedbackSubmissionScheduler = FeedbackSubmissionScheduler(this)
        mobileStateStore.load().feedbackOutbox.values
            .filter { it.status != PendingFeedbackSubmission.STATUS_FAILED }
            .forEach { submission ->
                if (submission.status == PendingFeedbackSubmission.STATUS_RETRYING) {
                    feedbackSubmissionScheduler.retry(submission)
                } else {
                    feedbackSubmissionScheduler.enqueue(submission)
                }
            }
        RunCompletionNotifications.createChannel(this)
    }

    fun setUiForeground(value: Boolean) {
        uiForeground = value
    }
}
