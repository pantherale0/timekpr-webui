package com.guardian.agent.ui.wizard

import android.content.Intent
import android.os.Bundle
import android.view.View
import androidx.annotation.StringRes
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import androidx.viewpager2.widget.ViewPager2
import com.guardian.agent.GuardianApplication
import com.guardian.agent.R
import com.guardian.agent.admin.DeviceAdminActivationActivity
import com.guardian.agent.admin.DeviceOwnerProvisioner
import com.guardian.agent.admin.ManagementModeSetupActivity
import com.guardian.agent.admin.ProvisioningBootstrap
import com.guardian.agent.admin.SecondaryUserProvisioner
import com.guardian.agent.databinding.ActivitySetupWizardBinding
import com.guardian.agent.service.AgentConnectionState
import com.guardian.agent.service.AgentConnectionStatus
import com.guardian.agent.ui.MainActivity
import com.guardian.agent.utils.permissions.PermissionManager
import com.guardian.agent.utils.permissions.PermissionState
import com.google.android.material.snackbar.Snackbar
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

class SetupWizardActivity : AppCompatActivity(), WizardHost {

    private lateinit var binding: ActivitySetupWizardBinding
    private lateinit var permissionManager: PermissionManager
    private var currentStep = 0
    private var permissionState = PermissionState(false, false, false)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (SecondaryUserProvisioner.blockManagementActivity(this)) {
            finish()
            return
        }
        if (ProvisioningBootstrap.needsManagementModeSetup(this)) {
            startActivity(Intent(this, ManagementModeSetupActivity::class.java))
            finish()
            return
        }

        binding = ActivitySetupWizardBinding.inflate(layoutInflater)
        setContentView(binding.root)

        currentStep = intent.getIntExtra(EXTRA_INITIAL_STEP, computeInitialStep())

        permissionManager = PermissionManager(this) { state ->
            onPermissionsChanged(state)
        }

        binding.wizardPager.apply {
            adapter = WizardPagerAdapter(this@SetupWizardActivity)
            isUserInputEnabled = false
            offscreenPageLimit = 2
            registerOnPageChangeCallback(object : ViewPager2.OnPageChangeCallback() {
                override fun onPageSelected(position: Int) {
                    currentStep = position
                    updateProgressIndicator()
                    updatePrimaryButton()
                    if (position == STEP_PERMISSIONS) {
                        permissionManager.onResume()
                    }
                }
            })
        }

        binding.primaryActionButton.setOnClickListener { onPrimaryAction() }

        binding.wizardPager.setCurrentItem(currentStep, false)
        updateProgressIndicator()
        updatePrimaryButton()

        if (currentStep == STEP_PERMISSIONS) {
            permissionManager.onResume()
        }

        lifecycleScope.launch {
            AgentConnectionState.status.collectLatest { status ->
                if (status == AgentConnectionStatus.ERROR) {
                    showConnectionError(AgentConnectionState.lastMessage.value)
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        if (currentStep == STEP_PERMISSIONS) {
            permissionManager.onResume()
        }
    }

    override fun onPairingComplete() {
        advanceToStep(STEP_PERMISSIONS)
    }

    override fun onPermissionsChanged(state: PermissionState) {
        permissionState = state
        findStep2Fragment()?.updatePermissionStates(state)
        updatePrimaryButton()
    }

    override fun showError(@StringRes messageRes: Int) {
        Snackbar.make(binding.root, messageRes, Snackbar.LENGTH_LONG).show()
    }

    override fun advanceToStep(step: Int) {
        binding.wizardPager.setCurrentItem(step, true)
    }

    override fun getCurrentStep(): Int = currentStep

    private fun onPrimaryAction() {
        when (currentStep) {
            STEP_PERMISSIONS -> {
                if (permissionState.allGranted) {
                    advanceToStep(STEP_COMPLETE)
                } else {
                    permissionManager.startSequentialGrant()
                }
            }
            STEP_COMPLETE -> {
                startActivity(Intent(this, MainActivity::class.java))
                finish()
            }
        }
    }

    private fun updateProgressIndicator() {
        val active = ContextCompat.getColor(this, R.color.guardian_primary)
        val inactive = ContextCompat.getColor(this, R.color.guardian_progress_inactive)
        binding.progressStep1.setBackgroundColor(if (currentStep >= STEP_LINK) active else inactive)
        binding.progressStep2.setBackgroundColor(if (currentStep >= STEP_PERMISSIONS) active else inactive)
        binding.progressStep3.setBackgroundColor(if (currentStep >= STEP_COMPLETE) active else inactive)
    }

    private fun updatePrimaryButton() {
        when (currentStep) {
            STEP_LINK -> binding.primaryActionButton.visibility = View.GONE
            STEP_PERMISSIONS -> {
                binding.primaryActionButton.visibility = View.VISIBLE
                binding.primaryActionButton.text = if (permissionState.allGranted) {
                    getString(R.string.wizard_continue)
                } else {
                    getString(R.string.wizard_grant_requirements)
                }
                binding.primaryActionButton.isEnabled = true
            }
            STEP_COMPLETE -> {
                binding.primaryActionButton.visibility = View.VISIBLE
                binding.primaryActionButton.text = getString(R.string.wizard_finish)
                binding.primaryActionButton.isEnabled = true
            }
        }
    }

    private fun showConnectionError(rawMessage: String) {
        val messageRes = when {
            rawMessage.contains("resolve host", ignoreCase = true) ||
                rawMessage.contains("UnknownHost", ignoreCase = true) ->
                R.string.wizard_error_server
            else -> R.string.wizard_error_connection
        }
        Snackbar.make(binding.root, messageRes, Snackbar.LENGTH_LONG).show()
    }

    private fun findStep2Fragment(): WizardStep2Fragment? {
        return supportFragmentManager.findFragmentByTag("f$STEP_PERMISSIONS") as? WizardStep2Fragment
    }

    private fun computeInitialStep(): Int {
        val config = GuardianApplication.from(this).configStore.load()
        if (config.serverUrl.isBlank()) return STEP_LINK
        DeviceOwnerProvisioner.applyIfDeviceOwner(this)
        val state = PermissionState(
            deviceAdmin = DeviceAdminActivationActivity.isActive(this) ||
                DeviceOwnerProvisioner.isDeviceOrProfileOwner(this),
            vpn = DeviceOwnerProvisioner.hasVpnConsent(this),
            usageAccess = DeviceOwnerProvisioner.hasUsageAccess(this),
        )
        return if (state.allGranted) STEP_COMPLETE else STEP_PERMISSIONS
    }

    companion object {
        const val EXTRA_INITIAL_STEP = "initial_step"
        const val STEP_LINK = 0
        const val STEP_PERMISSIONS = 1
        const val STEP_COMPLETE = 2
    }
}
