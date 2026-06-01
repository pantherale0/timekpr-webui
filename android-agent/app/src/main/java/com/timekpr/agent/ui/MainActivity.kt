package com.timekpr.agent.ui

import android.app.AppOpsManager
import android.content.Context
import android.content.Intent
import android.net.VpnService
import android.os.Bundle
import android.provider.Settings
import android.widget.Button
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.timekpr.agent.R
import com.timekpr.agent.TimeKprApplication
import com.timekpr.agent.admin.DeviceAdminActivationActivity
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
        val config = TimeKprApplication.from(this).configStore.load()
        if (config.serverUrl.isBlank()) {
            startActivity(Intent(this, PairingSetupActivity::class.java))
            finish()
            return
        }

        setContentView(R.layout.activity_main)
        val statusView = findViewById<TextView>(R.id.statusText)
        val deviceIdView = findViewById<TextView>(R.id.deviceIdText)
        deviceIdView.text = getString(R.string.status_connected) + ": " + config.systemId

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

    companion object {
        fun hasUsageAccess(context: Context): Boolean {
            val appOps = context.getSystemService(AppOpsManager::class.java) ?: return false
            val mode = appOps.checkOpNoThrow(
                AppOpsManager.OPSTR_GET_USAGE_STATS,
                android.os.Process.myUid(),
                context.packageName,
            )
            return mode == AppOpsManager.MODE_ALLOWED
        }
    }
}
