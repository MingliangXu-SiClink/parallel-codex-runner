package com.parallelcodexrunner.remote

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.core.view.doOnPreDraw
import com.parallelcodexrunner.remote.ui.MainViewModel
import com.parallelcodexrunner.remote.ui.PCRRemoteApp

class MainActivity : ComponentActivity() {
    private val viewModel: MainViewModel by viewModels()

    override fun onStart() {
        super.onStart()
        (application as PCRRemoteApplication).setUiForeground(true)
    }

    override fun onStop() {
        (application as PCRRemoteApplication).setUiForeground(false)
        viewModel.onAppStopped()
        super.onStop()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        (application as PCRRemoteApplication).setUiForeground(true)
        enableEdgeToEdge()
        if (savedInstanceState == null) {
            handleIntent(intent)
        }
        setContent {
            PCRRemoteApp(viewModel)
        }
        window.decorView.doOnPreDraw {
            window.decorView.post(viewModel::onFirstFrameDrawn)
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIntent(intent)
    }

    private fun handleIntent(intent: Intent) {
        val data = intent.data
            ?.takeIf { it.scheme.equals("pcr", ignoreCase = true) }
            ?: return
        // A configuration change recreates the Activity with its last Intent.
        // Consume each deep link so it is handled only once.
        setIntent(Intent(intent).setData(null))
        if (data.host.equals("run", ignoreCase = true)) {
            viewModel.openRunFromNotification(
                macId = data.getQueryParameter("mac_id").orEmpty(),
                workspaceId = data.getQueryParameter("workspace_id").orEmpty(),
                runId = data.getQueryParameter("run_id").orEmpty(),
            )
        } else if (data.host.equals("pair", ignoreCase = true)) {
            viewModel.handlePairingCode(data.toString())
        }
    }
}
