package com.guardian.agent.admin

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.widget.Button
import com.google.android.material.card.MaterialCardView
import com.guardian.agent.R
import com.guardian.agent.config.AgentConfigStore

/**
 * Prompts for device management mode during MDM provisioning (Android 12+) or on first launch
 * after device-owner QR setup on older Android releases.
 */
class ManagementModeSetupActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        ProvisioningBootstrap.stageFromAdminExtras(this, ProvisioningBootstrap.readAdminExtras(intent))

        setContentView(R.layout.activity_admin_policy_compliance)

        val cardExclusiveDo = findViewById<MaterialCardView>(R.id.cardExclusiveDo)
        val cardSecondaryUsers = findViewById<MaterialCardView>(R.id.cardSecondaryUsers)
        val btnConfirm = findViewById<Button>(R.id.btnConfirm)

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
            val selectedMode = if (cardExclusiveDo.isChecked) {
                AgentConfigStore.MANAGEMENT_MODE_EXCLUSIVE_DO
            } else {
                AgentConfigStore.MANAGEMENT_MODE_SECONDARY_USERS
            }

            ProvisioningBootstrap.completeManagementModeSetup(this, selectedMode)

            if (ProvisioningBootstrap.isProvisioningComplianceFlow(intent)) {
                setResult(RESULT_OK)
                finish()
            } else {
                startActivity(
                    Intent(this, com.guardian.agent.ui.MainActivity::class.java).apply {
                        addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_NEW_TASK)
                    },
                )
                finish()
            }
        }
    }
}
