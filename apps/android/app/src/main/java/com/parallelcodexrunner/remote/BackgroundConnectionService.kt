package com.parallelcodexrunner.remote

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import com.parallelcodexrunner.remote.ui.AppLanguage
import com.parallelcodexrunner.remote.ui.DisplayPreferences
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

class BackgroundConnectionService : Service() {
    private val serviceJob = SupervisorJob()
    private val serviceScope = CoroutineScope(serviceJob + Dispatchers.IO)
    private var connectionJob: Job? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (!DisplayPreferences.backgroundConnectionEnabled(this)) {
            stopSelf()
            return START_NOT_STICKY
        }
        startAsForeground()
        if (connectionJob?.isActive != true) {
            connectionJob = serviceScope.launch {
                while (isActive) {
                    try {
                        (application as PCRRemoteApplication).repository.macs()
                    } catch (error: CancellationException) {
                        throw error
                    } catch (_: Throwable) {
                        // A later heartbeat retries after temporary network loss.
                    }
                    delay(KEEP_ALIVE_INTERVAL_MILLIS)
                }
            }
        }
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        connectionJob?.cancel()
        serviceJob.cancel()
        super.onDestroy()
    }

    private fun startAsForeground() {
        val notification = connectionNotification()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_REMOTE_MESSAGING,
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun connectionNotification(): Notification {
        val english = AppLanguage.load(this) == AppLanguage.ENGLISH
        val channelName = if (english) "Background connection" else "后台连接"
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                channelName,
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = if (english) {
                    "Keeps Parallel Codex connected while it is in the background"
                } else {
                    "让 Parallel Codex 在后台保持连接"
                }
            }
        )
        val openApp = PendingIntent.getActivity(
            this,
            NOTIFICATION_ID,
            Intent(this, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setContentTitle("Parallel Codex")
            .setContentText(if (english) "Connected in background" else "正在后台保持连接")
            .setContentIntent(openApp)
            .setOngoing(true)
            .setCategory(Notification.CATEGORY_SERVICE)
            .build()
    }

    companion object {
        private const val CHANNEL_ID = "background-connection"
        private const val NOTIFICATION_ID = 0x504352
        private val KEEP_ALIVE_INTERVAL_MILLIS = TimeUnit.MINUTES.toMillis(2)

        fun applySetting(context: Context, enabled: Boolean): Boolean = try {
            val intent = Intent(context, BackgroundConnectionService::class.java)
            if (enabled) {
                context.startForegroundService(intent)
            } else {
                context.stopService(intent)
            }
            true
        } catch (_: RuntimeException) {
            false
        }
    }
}
