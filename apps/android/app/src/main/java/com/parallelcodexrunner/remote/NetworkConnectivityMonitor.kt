package com.parallelcodexrunner.remote

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

enum class InternetAvailability {
    UNKNOWN,
    AVAILABLE,
    UNAVAILABLE,
}

internal class NetworkConnectivityMonitor(context: Context) {
    private val connectivityManager =
        context.getSystemService(ConnectivityManager::class.java)
    private val mutableAvailability = MutableStateFlow(currentAvailability())

    val availability: StateFlow<InternetAvailability> = mutableAvailability.asStateFlow()

    private val callback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) = refresh()

        override fun onCapabilitiesChanged(
            network: Network,
            networkCapabilities: NetworkCapabilities,
        ) {
            if (network == connectivityManager.activeNetwork) {
                mutableAvailability.value = networkCapabilities.availability()
            } else {
                refresh()
            }
        }

        override fun onLost(network: Network) {
            // activeNetwork can still point at the network being removed while
            // this callback runs. Publish the loss directly; the ViewModel's
            // debounce is cancelled if a replacement network arrives.
            mutableAvailability.value = InternetAvailability.UNAVAILABLE
        }

        override fun onUnavailable() {
            mutableAvailability.value = InternetAvailability.UNAVAILABLE
        }
    }

    init {
        runCatching { connectivityManager.registerDefaultNetworkCallback(callback) }
            .onFailure { mutableAvailability.value = InternetAvailability.UNKNOWN }
    }

    private fun refresh() {
        mutableAvailability.value = currentAvailability()
    }

    private fun currentAvailability(): InternetAvailability {
        val network = connectivityManager.activeNetwork
            ?: return InternetAvailability.UNAVAILABLE
        val capabilities = connectivityManager.getNetworkCapabilities(network)
            ?: return InternetAvailability.UNKNOWN
        return capabilities.availability()
    }
}

private fun NetworkCapabilities.availability(): InternetAvailability =
    // Android's VALIDATED bit depends on the system probe endpoint and can be
    // false on otherwise usable networks. The Relay itself remains the source
    // of truth for server reachability; this monitor only reports a network path.
    if (hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)) {
        InternetAvailability.AVAILABLE
    } else {
        InternetAvailability.UNAVAILABLE
    }
