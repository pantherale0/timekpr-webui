package com.guardian.agent.admin

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity

/**
 * Prompts for device management mode on first launch after device-owner QR setup on Android 11 and below.
 */
class ManagementModeSetupActivity : AppCompatActivity() {
    private lateinit var setupController: ManagementModeSetupController

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setupController = ManagementModeSetupDelegate.bind(
            activity = this,
            complianceFlow = false,
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
