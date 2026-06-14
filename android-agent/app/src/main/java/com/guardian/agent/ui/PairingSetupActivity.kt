package com.guardian.agent.ui

import android.content.Intent
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import com.guardian.agent.R
import com.guardian.agent.GuardianApplication
import com.guardian.agent.admin.SecondaryUserProvisioner
import com.guardian.agent.service.AgentSessionCoordinator

class PairingSetupActivity : AppCompatActivity() {
    private val qrLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        if (result.resultCode != RESULT_OK) return@registerForActivityResult
        val data = result.data ?: return@registerForActivityResult
        val serverUrl = data.getStringExtra(QrScanActivity.EXTRA_SERVER_URL) ?: return@registerForActivityResult
        val registrationToken = data.getStringExtra(QrScanActivity.EXTRA_REGISTRATION_TOKEN)
        applyPairing(serverUrl, registrationToken)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (SecondaryUserProvisioner.blockManagementActivity(this)) {
            finish()
            return
        }
        setContentView(R.layout.activity_pairing_setup)

        val serverInput = findViewById<EditText>(R.id.serverUrlInput)
        findViewById<Button>(R.id.scanQrButton).setOnClickListener {
            qrLauncher.launch(Intent(this, QrScanActivity::class.java))
        }
        findViewById<Button>(R.id.saveButton).setOnClickListener {
            applyPairing(serverInput.text.toString(), null)
        }
    }

    private fun applyPairing(serverUrl: String, registrationToken: String?) {
        if (serverUrl.isBlank()) return
        val store = GuardianApplication.from(this).configStore
        store.applyPairingPayload(serverUrl, registrationToken)
        AgentSessionCoordinator.startMobileAgent(this)
        startActivity(Intent(this, MainActivity::class.java))
        finish()
    }
}
