package com.guardian.agent.boot

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Process
import android.util.Log
import com.guardian.agent.GuardianApplication
import com.guardian.agent.config.AgentConfigStore
import com.guardian.agent.admin.SecondaryUserProvisioner

class BootCompletedReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        val action = intent?.action ?: return
        val isLocked = action == Intent.ACTION_LOCKED_BOOT_COMPLETED
        val userId = Process.myUid() / 100_000

        if (userId != 0) {
            if (SecondaryUserProvisioner.isManagedSecondaryUser(context)) {
                Log.i("BootCompletedReceiver", "Managed secondary user $userId received action: $action")
                if (action == Intent.ACTION_BOOT_COMPLETED || action == Intent.ACTION_MY_PACKAGE_REPLACED) {
                    val pendingResult = goAsync()
                    com.guardian.agent.enforcement.EnforcementCoordinator
                        .schedulePrepareManagedSecondaryUser(context) {
                            pendingResult.finish()
                        }
                }
            }
            return
        }

        DirectBootStartup.onBoot(context, action, lockedBoot = isLocked)

        if (action == Intent.ACTION_BOOT_COMPLETED || action == Intent.ACTION_LOCKED_BOOT_COMPLETED) {
            val configStore = GuardianApplication.from(context).configStore
            val isSecondaryMode = configStore.load().managementMode == AgentConfigStore.MANAGEMENT_MODE_SECONDARY_USERS

            if (isSecondaryMode) {
                val lockIntent = Intent(context, OtpLockActivity::class.java).apply {
                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
                }
                context.startActivity(lockIntent)
            }
        }
    }
}
