package com.guardian.agent.vpn

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import com.guardian.agent.util.AgentLog

internal object VpnNetworkCapture {
    private const val TAG = "VpnNetworkCapture"

    fun findUnderlyingNetwork(context: Context): Network? {
        val manager = context.getSystemService(ConnectivityManager::class.java) ?: return null

        manager.activeNetwork?.let { active ->
            if (!isVpnNetwork(manager, active) && hasInternet(manager, active)) {
                AgentLog.d(TAG, "Using active underlying network $active")
                return active
            }
        }

        for (candidate in manager.allNetworks) {
            if (isVpnNetwork(manager, candidate)) continue
            if (hasInternet(manager, candidate)) {
                AgentLog.d(TAG, "Using scanned underlying network $candidate")
                return candidate
            }
        }

        AgentLog.wOnce(TAG, "no_underlying", "No underlying network found for DNS resolution")
        return null
    }

    private fun isVpnNetwork(manager: ConnectivityManager, network: Network): Boolean {
        val capabilities = manager.getNetworkCapabilities(network) ?: return false
        return capabilities.hasTransport(NetworkCapabilities.TRANSPORT_VPN)
    }

    private fun hasInternet(manager: ConnectivityManager, network: Network): Boolean {
        val capabilities = manager.getNetworkCapabilities(network) ?: return false
        return capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) &&
            (
                capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) ||
                    capabilities.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) ||
                    capabilities.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)
                )
    }
}
