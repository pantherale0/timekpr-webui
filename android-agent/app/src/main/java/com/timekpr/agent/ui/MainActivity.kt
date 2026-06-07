package com.timekpr.agent.ui

import android.content.Intent
import android.net.VpnService
import android.os.Bundle
import android.provider.Settings
import android.view.View
import android.widget.Button
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.timekpr.agent.BuildConfig
import com.timekpr.agent.R
import com.timekpr.agent.TimeKprApplication
import com.timekpr.agent.admin.DeviceAdminActivationActivity
import com.timekpr.agent.admin.DeviceOwnerProvisioner
import com.timekpr.agent.admin.SecondaryUserProvisioner
import com.timekpr.agent.service.AgentConnectionState
import com.timekpr.agent.service.AgentConnectionStatus
import com.timekpr.agent.service.AgentSessionCoordinator
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

class MainActivity : AppCompatActivity() {
    private val qrLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        if (result.resultCode != RESULT_OK) return@registerForActivityResult
        val data = result.data ?: return@registerForActivityResult
        val serverUrl = data.getStringExtra(QrScanActivity.EXTRA_SERVER_URL) ?: return@registerForActivityResult
        val registrationToken = data.getStringExtra(QrScanActivity.EXTRA_REGISTRATION_TOKEN)
        TimeKprApplication.from(this).configStore.applyPairingPayload(serverUrl, registrationToken)
        AgentSessionCoordinator.startMobileAgent(this)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (SecondaryUserProvisioner.blockManagementActivity(this)) {
            finish()
            return
        }

        val config = TimeKprApplication.from(this).configStore.load()
        if (config.serverUrl.isBlank()) {
            startActivity(Intent(this, PairingSetupActivity::class.java))
            finish()
            return
        }

        setContentView(R.layout.activity_main)
        val statusView = findViewById<TextView>(R.id.statusText)
        val deviceIdView = findViewById<TextView>(R.id.deviceIdText)
        deviceIdView.text = getString(R.string.status_connected) + ": " + config.systemId +
            "\n" + getString(R.string.agent_version_label, BuildConfig.DEFAULT_AGENT_VERSION)

        findViewById<Button>(R.id.scanQrButton).setOnClickListener {
            qrLauncher.launch(Intent(this, QrScanActivity::class.java))
        }
        findViewById<Button>(R.id.enableAdminButton).setOnClickListener {
            DeviceAdminActivationActivity.request(this)
        }
        findViewById<Button>(R.id.usageAccessButton).setOnClickListener {
            startActivity(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS))
        }
        findViewById<Button>(R.id.vpnButton).setOnClickListener {
            val prepare = VpnService.prepare(this)
            if (prepare != null) {
                startActivity(prepare)
            }
        }
        findViewById<Button>(R.id.reconnectButton).setOnClickListener {
            AgentSessionCoordinator.scheduleSync(this, reason = "manual")
        }

        lifecycleScope.launch {
            AgentConnectionState.status.collectLatest { status ->
                statusView.text = when (status) {
                    AgentConnectionStatus.AUTHENTICATED -> getString(R.string.status_connected)
                    AgentConnectionStatus.PENDING_APPROVAL -> getString(R.string.status_pending)
                    AgentConnectionStatus.CONNECTING -> "Connecting…"
                    AgentConnectionStatus.ERROR -> AgentConnectionState.lastMessage.value
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
        refreshCapabilityButtons()
    }

    private fun refreshCapabilityButtons() {
        val deviceOwner = DeviceOwnerProvisioner.isDeviceOrProfileOwner(this)
        val adminActive = DeviceAdminActivationActivity.isActive(this)
        val usageGranted = DeviceOwnerProvisioner.hasUsageAccess(this)
        val vpnGranted = DeviceOwnerProvisioner.hasVpnConsent(this)

        findViewById<Button>(R.id.enableAdminButton).apply {
            visibility = if (deviceOwner || adminActive) View.GONE else View.VISIBLE
        }
        findViewById<Button>(R.id.usageAccessButton).apply {
            visibility = if (usageGranted) View.GONE else View.VISIBLE
        }
        findViewById<Button>(R.id.vpnButton).apply {
            visibility = if (vpnGranted) View.GONE else View.VISIBLE
        }
    }
}
