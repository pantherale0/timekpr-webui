package com.guardian.agent.admin

import android.content.Intent
import androidx.appcompat.app.AppCompatActivity
import android.widget.Button
import com.google.android.material.card.MaterialCardView
import com.guardian.agent.R
import com.guardian.agent.config.AgentConfigStore
import com.guardian.agent.ui.MainActivity

/**
 * Shared management-mode UI used during MDM compliance (Android 12+) and first launch (older Android).
 */
object ManagementModeSetupDelegate {
    fun bind(
        activity: AppCompatActivity,
        complianceFlow: Boolean,
        onComplete: (() -> Unit)? = null,
    ) {
        ProvisioningBootstrap.stageFromAdminExtras(
            activity,
            ProvisioningBootstrap.readAdminExtras(activity.intent),
        )

        activity.setContentView(R.layout.activity_admin_policy_compliance)

        val cardExclusiveDo = activity.findViewById<MaterialCardView>(R.id.cardExclusiveDo)
        val cardSecondaryUsers = activity.findViewById<MaterialCardView>(R.id.cardSecondaryUsers)
        val btnConfirm = activity.findViewById<Button>(R.id.btnConfirm)

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

            ProvisioningBootstrap.completeManagementModeSetup(activity, selectedMode)
            onComplete?.invoke()

            if (complianceFlow) {
                activity.setResult(AppCompatActivity.RESULT_OK)
                activity.finish()
            } else {
                activity.startActivity(
                    Intent(activity, MainActivity::class.java).apply {
                        addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_NEW_TASK)
                    },
                )
                activity.finish()
            }
        }
    }
}
