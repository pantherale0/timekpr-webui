package com.guardian.agent.ui

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.guardian.agent.R
import com.guardian.agent.GuardianApplication
import com.guardian.agent.admin.DeviceOwnerProvisioner
import com.guardian.agent.admin.ManagementModeSetupActivity
import com.guardian.agent.admin.ProvisioningBootstrap
import com.guardian.agent.admin.SecondaryUserProvisioner
import com.guardian.agent.service.AgentConnectionState
import com.guardian.agent.service.AgentConnectionStatus
import com.guardian.agent.service.AgentSessionCoordinator
import com.guardian.agent.ui.wizard.SetupWizardActivity
import com.guardian.agent.utils.permissions.PermissionState
import com.guardian.agent.admin.DeviceAdminActivationActivity
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

class MainActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (SecondaryUserProvisioner.blockManagementActivity(this)) {
            finish()
            return
        }

        if (ProvisioningBootstrap.needsManagementModeSetup(this)) {
            startActivity(Intent(this, ManagementModeSetupActivity::class.java))
            finish()
            return
        }

        val config = GuardianApplication.from(this).configStore.load()
        if (needsSetupWizard(config.serverUrl.isBlank())) {
            startActivity(Intent(this, SetupWizardActivity::class.java))
            finish()
            return
        }

        setContentView(R.layout.activity_main)
        val statusView = findViewById<TextView>(R.id.statusText)

        findViewById<View>(R.id.reconnectButton).setOnClickListener {
            AgentSessionCoordinator.scheduleSync(this, reason = "manual")
        }

        lifecycleScope.launch {
            AgentConnectionState.status.collectLatest { status ->
                statusView.text = when (status) {
                    AgentConnectionStatus.AUTHENTICATED -> getString(R.string.status_connected)
                    AgentConnectionStatus.PENDING_APPROVAL -> getString(R.string.status_pending)
                    AgentConnectionStatus.CONNECTING -> getString(R.string.status_connected)
                    AgentConnectionStatus.ERROR -> getString(R.string.status_disconnected)
                    AgentConnectionStatus.DISCONNECTED -> getString(R.string.status_disconnected)
                }
            }
        }

        AgentSessionCoordinator.startMobileAgent(this)
    }

    override fun onResume() {
        super.onResume()
        if (SecondaryUserProvisioner.isManagedSecondaryUser(this)) {
            finish()
            return
        }
        DeviceOwnerProvisioner.applyIfDeviceOwner(this)
        SecondaryUserProvisioner.ensurePrimaryUiVisible(this)
    }

    private fun needsSetupWizard(serverUrlBlank: Boolean): Boolean {
        if (serverUrlBlank) return true
        DeviceOwnerProvisioner.applyIfDeviceOwner(this)
        if (DeviceOwnerProvisioner.skipsManualPermissionSetup(this)) {
            return false
        }
        val state = PermissionState(
            deviceAdmin = DeviceAdminActivationActivity.isActive(this) ||
                DeviceOwnerProvisioner.isDeviceOrProfileOwner(this),
            vpn = DeviceOwnerProvisioner.hasVpnConsent(this),
            usageAccess = DeviceOwnerProvisioner.hasUsageAccess(this),
        )
        return !state.allGranted
    }
}
