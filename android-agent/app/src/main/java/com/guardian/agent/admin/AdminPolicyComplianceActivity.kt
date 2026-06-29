package com.guardian.agent.admin

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity

/**
 * Android 12+ handler for [android.app.admin.DevicePolicyManager.ACTION_ADMIN_POLICY_COMPLIANCE].
 *
 * Must be a real activity class (not an activity-alias); some OEM setup wizards reject aliases.
 */
class AdminPolicyComplianceActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        ManagementModeSetupDelegate.bind(this, complianceFlow = true)
    }
}
