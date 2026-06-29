package com.guardian.agent.admin

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity

/**
 * Prompts for device management mode on first launch after device-owner QR setup on Android 11 and below.
 */
class ManagementModeSetupActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        ManagementModeSetupDelegate.bind(this, complianceFlow = false)
    }
}
