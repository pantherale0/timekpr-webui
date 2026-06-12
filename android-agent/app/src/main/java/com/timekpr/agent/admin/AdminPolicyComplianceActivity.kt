package com.timekpr.agent.admin

import android.app.Activity
import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.os.Build
import android.os.Bundle
import android.os.PersistableBundle
import android.util.Log
import android.widget.Button
import com.google.android.material.card.MaterialCardView
import com.timekpr.agent.R
import com.timekpr.agent.TimeKprApplication
import com.timekpr.agent.config.AgentConfigStore
import com.timekpr.agent.service.AgentSessionCoordinator

/**
 * Finalizes Android Enterprise QR provisioning by prompting the administrator
 * for the management mode and applying server config from admin extras.
 */
class AdminPolicyComplianceActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val extras = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            intent.getParcelableExtra(
                DevicePolicyManager.EXTRA_PROVISIONING_ADMIN_EXTRAS_BUNDLE,
                PersistableBundle::class.java,
            )
        } else {
            @Suppress("DEPRECATION")
            intent.getParcelableExtra(DevicePolicyManager.EXTRA_PROVISIONING_ADMIN_EXTRAS_BUNDLE)
                as? PersistableBundle
        }

        val serverUrl = extras?.getString(ProvisioningExtras.SERVER_URL)?.trim().orEmpty()
        val registrationToken = extras?.getString(ProvisioningExtras.REGISTRATION_TOKEN)
            ?.trim()
            ?.takeIf { it.isNotEmpty() }

        setContentView(R.layout.activity_admin_policy_compliance)

        val cardExclusiveDo = findViewById<MaterialCardView>(R.id.cardExclusiveDo)
        val cardSecondaryUsers = findViewById<MaterialCardView>(R.id.cardSecondaryUsers)
        val btnConfirm = findViewById<Button>(R.id.btnConfirm)

        // Default to Single-User Mode (Exclusive Device Owner)
        cardExclusiveDo.isChecked = true
        cardSecondaryUsers.isChecked = false

        cardExclusiveDo.setOnClickListener {
            cardExclusiveDo.isChecked = true
            cardSecondaryUsers.isChecked = false
        }

        cardSecondaryUsers.setOnClickListener {
            cardExclusiveDo.isChecked = false
            cardSecondaryUsers.isChecked = true
        }

        btnConfirm.setOnClickListener {
            val app = TimeKprApplication.from(this)
            val selectedMode = if (cardExclusiveDo.isChecked) {
                AgentConfigStore.MANAGEMENT_MODE_EXCLUSIVE_DO
            } else {
                AgentConfigStore.MANAGEMENT_MODE_SECONDARY_USERS
            }

            app.configStore.saveManagementMode(selectedMode)

            if (serverUrl.isNotEmpty()) {
                app.configStore.applyPairingPayload(serverUrl, registrationToken)
                DeviceOwnerProvisioner.applyIfDeviceOwner(this)
                AgentSessionCoordinator.startMobileAgent(this)
            } else {
                Log.w(TAG, "Provisioning extras did not include a server URL")
            }

            setResult(RESULT_OK)
            finish()
        }
    }

    companion object {
        private const val TAG = "AdminPolicyCompliance"
    }
}
