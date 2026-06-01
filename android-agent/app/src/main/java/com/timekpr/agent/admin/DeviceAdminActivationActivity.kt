package com.timekpr.agent.admin

import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity

class DeviceAdminActivationActivity : AppCompatActivity() {
    private val adminComponent by lazy {
        ComponentName(this, TimeKprDeviceAdminReceiver::class.java)
    }

    private val launcher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { finish() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val intent = Intent(DevicePolicyManager.ACTION_ADD_DEVICE_ADMIN).apply {
            putExtra(DevicePolicyManager.EXTRA_DEVICE_ADMIN, adminComponent)
            putExtra(
                DevicePolicyManager.EXTRA_ADD_EXPLANATION,
                "TimeKpr needs device admin to lock the device when screen time limits are reached and suspend blocked apps.",
            )
        }
        launcher.launch(intent)
    }

    companion object {
        fun isActive(context: Context): Boolean {
            val dpm = context.getSystemService(DevicePolicyManager::class.java) ?: return false
            val component = ComponentName(context, TimeKprDeviceAdminReceiver::class.java)
            return dpm.isAdminActive(component)
        }

        fun request(context: Context) {
            context.startActivity(
                Intent(context, DeviceAdminActivationActivity::class.java).apply {
                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                },
            )
        }
    }
}
