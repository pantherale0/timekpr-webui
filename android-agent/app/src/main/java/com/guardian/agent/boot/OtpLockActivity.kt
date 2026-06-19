package com.guardian.agent.boot

import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.provider.Settings
import com.guardian.agent.enforcement.TimeExemptionResolver
import android.os.Build
import android.os.Bundle
import android.os.Process
import android.os.UserHandle
import android.util.Log
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.progressindicator.CircularProgressIndicator
import com.google.android.material.button.MaterialButton
import com.guardian.agent.R
import com.guardian.agent.GuardianApplication
import com.guardian.agent.admin.DeviceOwnerProvisioner
import com.guardian.agent.admin.GuardianDeviceAdminReceiver
import com.guardian.agent.util.AndroidUsers
import com.guardian.agent.util.ParentalAccessOtp
import kotlinx.coroutines.*

class OtpLockActivity : AppCompatActivity() {
    companion object {
        const val EXTRA_CLOCK_TAMPER_UNLOCK = "clock_tamper_unlock"
        private const val TAG = "OtpLockActivity"
        private const val COUNTDOWN_MAX_MS = 5000L
        private const val STEP_MS = 100L
    }

    private lateinit var otpEntryField: EditText
    private lateinit var countdownProgress: CircularProgressIndicator
    private lateinit var countdownText: TextView
    private lateinit var otpContainerCard: View
    private lateinit var keypadGrid: View
    private lateinit var btnPrimaryAction: MaterialButton
    private lateinit var btnSecondaryAction: MaterialButton

    private var pinBuffer = StringBuilder()
    private var countdownJob: Job? = null
    private var isOtpMode = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Show above system lockscreen and keep screen on
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
                        WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON
            )
        }

        setContentView(R.layout.activity_otp_lock)

        // Initialize UI components
        otpEntryField = findViewById(R.id.otpEntryField)
        otpEntryField.showSoftInputOnFocus = false // Prevent soft keyboard

        countdownProgress = findViewById(R.id.countdownProgress)
        countdownText = findViewById(R.id.countdownText)
        otpContainerCard = findViewById(R.id.otpContainerCard)
        keypadGrid = findViewById(R.id.keypadGrid)
        btnPrimaryAction = findViewById(R.id.btnPrimaryAction)
        btnSecondaryAction = findViewById(R.id.btnSecondaryAction)

        setupKeypad()
        setupActions()

        // Verify configuration: If we aren't in multi-user secondary users mode, or if we aren't User 0, finish immediately.
        val configStore = GuardianApplication.from(this).configStore
        val isSecondaryMode = configStore.load().managementMode == com.guardian.agent.config.AgentConfigStore.MANAGEMENT_MODE_SECONDARY_USERS
        val isPrimaryUser = Process.myUid() / 100_000 == 0

        val clockTamperUnlock = intent.getBooleanExtra(EXTRA_CLOCK_TAMPER_UNLOCK, false)

        if (!isPrimaryUser || !isSecondaryMode) {
            if (!clockTamperUnlock) {
                Log.d(TAG, "Not primary user or not in secondary user mode, exiting.")
                finish()
                return
            }
        }

        if (!clockTamperUnlock) {
            startLockTaskMode()
            startCountdown()
        } else {
            isOtpMode = true
            otpContainerCard.visibility = View.VISIBLE
            keypadGrid.visibility = View.VISIBLE
            countdownProgress.visibility = View.GONE
            countdownText.visibility = View.GONE
            btnPrimaryAction.text = getString(R.string.verify_otp)
            btnSecondaryAction.visibility = View.GONE
        }
    }

    override fun onResume() {
        super.onResume()
        if (!intent.getBooleanExtra(EXTRA_CLOCK_TAMPER_UNLOCK, false)) {
            startLockTaskMode()
        }
    }

    private fun startLockTaskMode() {
        if (DeviceOwnerProvisioner.isDeviceOwner(this)) {
            try {
                startLockTask()
            } catch (e: Exception) {
                Log.w(TAG, "Failed to start LockTask mode", e)
            }
        }
    }

    private fun setupKeypad() {
        val buttonIds = listOf(
            R.id.btn0 to "0", R.id.btn1 to "1", R.id.btn2 to "2",
            R.id.btn3 to "3", R.id.btn4 to "4", R.id.btn5 to "5",
            R.id.btn6 to "6", R.id.btn7 to "7", R.id.btn8 to "8",
            R.id.btn9 to "9"
        )

        for ((id, digit) in buttonIds) {
            findViewById<Button>(id).setOnClickListener {
                appendDigit(digit)
            }
        }

        findViewById<Button>(R.id.btnDel).setOnClickListener {
            deleteLastDigit()
        }

        findViewById<Button>(R.id.btnOk).setOnClickListener {
            verifyOtp()
        }
    }

    private fun setupActions() {
        btnPrimaryAction.setOnClickListener {
            if (!isOtpMode) {
                cancelCountdownAndShowOtp()
            } else {
                verifyOtp()
            }
        }

        btnSecondaryAction.setOnClickListener {
            switchToChildProfile()
        }
    }

    private fun startCountdown() {
        countdownJob = lifecycleScope.launch {
            val totalSteps = (COUNTDOWN_MAX_MS / STEP_MS).toInt()
            for (i in totalSteps downTo 0) {
                val progress = (i * 100) / totalSteps
                val seconds = (i * STEP_MS) / 1000.0
                countdownProgress.progress = progress
                countdownText.text = String.format("%.1fs", seconds)
                delay(STEP_MS)
            }
            switchToChildProfile()
        }
    }

    private fun cancelCountdownAndShowOtp() {
        countdownJob?.cancel()
        findViewById<View>(R.id.countdownFrame).visibility = View.GONE
        findViewById<TextView>(R.id.subtitleText).text = "Unlock system using Parent OTP"

        otpContainerCard.visibility = View.VISIBLE
        keypadGrid.visibility = View.VISIBLE
        isOtpMode = true

        btnPrimaryAction.text = "Unlock Admin"
        btnSecondaryAction.text = "Switch to Child Profile"
    }

    private fun appendDigit(digit: String) {
        if (pinBuffer.length < 6) {
            pinBuffer.append(digit)
            updateOtpField()
        }
    }

    private fun deleteLastDigit() {
        if (pinBuffer.isNotEmpty()) {
            pinBuffer.deleteAt(pinBuffer.length - 1)
            updateOtpField()
        }
    }

    private fun updateOtpField() {
        otpEntryField.setText(pinBuffer.toString())
    }

    private fun verifyOtp() {
        val enteredCode = pinBuffer.toString()
        if (enteredCode.length != 6) {
            Toast.makeText(this, "Please enter a 6-digit OTP", Toast.LENGTH_SHORT).show()
            return
        }

        val configStore = GuardianApplication.from(this).configStore
        val agentToken = configStore.load().agentToken

        if (agentToken.isNullOrBlank()) {
            Toast.makeText(this, "Device is not paired.", Toast.LENGTH_LONG).show()
            return
        }

        val isValid = if (intent.getBooleanExtra(EXTRA_CLOCK_TAMPER_UNLOCK, false)) {
            val trustedMs = com.guardian.agent.integrity.ClockIntegrityStore(this).trustedWallMs()
            ParentalAccessOtp.verifyOtp(enteredCode, agentToken, trustedMs)
        } else {
            ParentalAccessOtp.verifyOtp(enteredCode, agentToken)
        }
        if (!isValid) {
            Toast.makeText(this, "Incorrect OTP. Please try again.", Toast.LENGTH_LONG).show()
            pinBuffer.clear()
            updateOtpField()
            return
        }

        // OTP is correct!
        if (intent.getBooleanExtra(EXTRA_CLOCK_TAMPER_UNLOCK, false)) {
            com.guardian.agent.enforcement.EnforcementController(
                this,
                GuardianApplication.from(this).appPolicyStore,
            ).onClockTamperOtpUnlocked()
            finish()
            return
        }

        if (!DeviceOwnerProvisioner.hasUsageAccess(this)) {
            TimeExemptionResolver.tempExemptSettingsUntil = System.currentTimeMillis() + 120_000
            val dpm = getSystemService(DevicePolicyManager::class.java)
            val admin = ComponentName(this, GuardianDeviceAdminReceiver::class.java)
            try {
                dpm.setPackagesSuspended(admin, arrayOf("com.android.settings"), false)
            } catch (_: Exception) {}

            try {
                val intent = Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS).apply {
                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                }
                startActivity(intent)
            } catch (e: Exception) {
                Log.w(TAG, "Failed to launch usage access settings", e)
            }
        }

        try {
            stopLockTask()
        } catch (_: Exception) {}
        finish()
    }

    private fun switchToChildProfile() {
        countdownJob?.cancel()
        val childUids = AndroidUsers.managedSecondaryUserIds(this)
        if (childUids.isNotEmpty()) {
            val childUid = childUids.first()
            try {
                val constructor = UserHandle::class.java.getConstructor(Int::class.javaPrimitiveType)
                val userHandle = constructor.newInstance(childUid) as UserHandle
                val dpm = getSystemService(DevicePolicyManager::class.java)
                val admin = ComponentName(this, GuardianDeviceAdminReceiver::class.java)
                dpm.switchUser(admin, userHandle)
            } catch (e: Exception) {
                Log.w(TAG, "Failed to switch to child profile (uid $childUid)", e)
                Toast.makeText(this, "Failed to switch user.", Toast.LENGTH_SHORT).show()
            }
        } else {
            Toast.makeText(this, "No managed secondary users found.", Toast.LENGTH_LONG).show()
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        // Disable back button inside overlay
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
            @Suppress("DEPRECATION")
            super.onBackPressed()
        }
    }
}
