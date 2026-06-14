package com.guardian.agent.update

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build
import android.util.Log
import com.guardian.agent.service.AgentConnectionState
import com.guardian.agent.service.AgentConnectionStatus
import com.guardian.agent.service.AgentSessionCoordinator

class AgentUpdateReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        val status = intent?.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)
            ?: PackageInstaller.STATUS_FAILURE
        val message = intent?.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE).orEmpty()
        val appContext = context.applicationContext

        val packageName = intent?.getStringExtra(PackageInstaller.EXTRA_PACKAGE_NAME)
        val isSelf = packageName == null || packageName == context.packageName

        when (status) {
            PackageInstaller.STATUS_SUCCESS -> {
                Log.i(TAG, "Package installed successfully: $packageName")
                if (isSelf) {
                    AgentConnectionState.update(AgentConnectionStatus.CONNECTING, "Update installed, reconnecting…")
                    AgentSessionCoordinator.scheduleSync(appContext, reason = "agent_updated")
                }
            }
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                val confirmIntent = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    intent?.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
                } else {
                    @Suppress("DEPRECATION")
                    intent?.getParcelableExtra(Intent.EXTRA_INTENT)
                }
                if (confirmIntent != null) {
                    confirmIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    appContext.startActivity(confirmIntent)
                }
            }
            else -> {
                Log.w(TAG, "Install failed for $packageName: status=$status message=$message")
                if (isSelf) {
                    AgentConnectionState.update(
                        AgentConnectionStatus.ERROR,
                        message.ifBlank { "Agent update install failed (status $status)" },
                    )
                }
            }
        }
    }

    companion object {
        private const val TAG = "AgentUpdateReceiver"
        const val ACTION_INSTALL_COMPLETE = "com.guardian.agent.action.INSTALL_COMPLETE"
    }
}
