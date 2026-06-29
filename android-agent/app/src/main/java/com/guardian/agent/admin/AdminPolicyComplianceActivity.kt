package com.guardian.agent.admin

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity

/**
 * Android 12+ handler for [android.app.admin.DevicePolicyManager.ACTION_ADMIN_POLICY_COMPLIANCE].
 *
 * Must be a real activity class (not an activity-alias); some OEM setup wizards reject aliases.
 */
class AdminPolicyComplianceActivity : AppCompatActivity() {
    private lateinit var setupController: ManagementModeSetupController

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setupController = ManagementModeSetupDelegate.bind(
            activity = this,
            complianceFlow = true,
            savedInstanceState = savedInstanceState,
        )
    }

    override fun onResume() {
        super.onResume()
        if (::setupController.isInitialized) {
            setupController.onResume()
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        if (::setupController.isInitialized) {
            setupController.onSaveInstanceState(outState)
        }
    }

    override fun onDestroy() {
        if (::setupController.isInitialized) {
            setupController.onDestroy()
        }
        super.onDestroy()
    }
}
