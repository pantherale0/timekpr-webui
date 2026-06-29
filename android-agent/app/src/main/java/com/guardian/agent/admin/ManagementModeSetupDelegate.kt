package com.guardian.agent.admin

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity

/**
 * Thin wrapper kept for call sites; delegates to [ManagementModeSetupController].
 */
object ManagementModeSetupDelegate {
    fun bind(
        activity: AppCompatActivity,
        complianceFlow: Boolean,
        savedInstanceState: Bundle? = null,
    ): ManagementModeSetupController {
        val controller = ManagementModeSetupController(activity, complianceFlow)
        controller.onCreate(savedInstanceState)
        return controller
    }
}
