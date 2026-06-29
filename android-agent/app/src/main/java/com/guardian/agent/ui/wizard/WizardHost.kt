package com.guardian.agent.ui.wizard

import androidx.annotation.StringRes
import com.guardian.agent.utils.permissions.PermissionState

interface WizardHost {
    fun onPairingComplete()
    fun onPermissionsChanged(state: PermissionState)
    fun showError(@StringRes messageRes: Int)
    fun advanceToStep(step: Int)
    fun getCurrentStep(): Int
}
