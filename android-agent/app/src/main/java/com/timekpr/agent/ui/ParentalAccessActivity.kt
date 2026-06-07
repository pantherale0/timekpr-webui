package com.timekpr.agent.ui

import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.os.Build
import android.os.Bundle
import android.text.InputType
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import com.timekpr.agent.R
import com.timekpr.agent.TimeKprApplication
import com.timekpr.agent.admin.DeviceOwnerProvisioner
import com.timekpr.agent.admin.TimeKprDeviceAdminReceiver
import com.timekpr.agent.enforcement.EnforcementController
import com.timekpr.agent.enforcement.OwnerProfilePinRotator
import com.timekpr.agent.util.ParentalAccessOtp

class ParentalAccessActivity : AppCompatActivity() {

    private lateinit var pinEntryField: EditText
    private var pinBuffer = StringBuilder()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        
        // Allow the activity to be shown on top of the secure lock screen
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(
                android.view.WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED
                        or android.view.WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON
            )
        }

        setContentView(R.layout.activity_parental_access)

        pinEntryField = findViewById(R.id.pinEntryField)
        pinEntryField.inputType = InputType.TYPE_NULL // Prevent system keyboard from appearing

        setupKeypad()
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
            verifyAndResetPin()
        }

        findViewById<Button>(R.id.btnCancel).setOnClickListener {
            finish()
        }
    }

    private fun appendDigit(digit: String) {
        if (pinBuffer.length < 6) {
            pinBuffer.append(digit)
            updatePinField()
        }
    }

    private fun deleteLastDigit() {
        if (pinBuffer.isNotEmpty()) {
            pinBuffer.deleteAt(pinBuffer.length - 1)
            updatePinField()
        }
    }

    private fun updatePinField() {
        pinEntryField.setText(pinBuffer.toString())
    }

    private fun verifyAndResetPin() {
        val enteredCode = pinBuffer.toString()
        if (enteredCode.length != 6) {
            Toast.makeText(this, "Please enter a 6-digit code", Toast.LENGTH_SHORT).show()
            return
        }

        val configStore = TimeKprApplication.from(this).configStore
        val agentToken = configStore.load().agentToken

        if (agentToken.isNullOrBlank()) {
            showErrorDialog("This device is not paired with the TimeKpr server. Offline recovery is not available.")
            return
        }

        val isValid = ParentalAccessOtp.verifyOtp(enteredCode, agentToken)
        if (!isValid) {
            Toast.makeText(this, "Incorrect code. Please try again.", Toast.LENGTH_LONG).show()
            pinBuffer.clear()
            updatePinField()
            return
        }

        EnforcementController(
            this,
            TimeKprApplication.from(this).appPolicyStore,
        ).onOwnerProfileUnlocked()

        // OTP is correct! Attempt to reset lock screen passcode.
        val dpm = getSystemService(Context.DEVICE_POLICY_SERVICE) as DevicePolicyManager
        val admin = ComponentName(this, TimeKprDeviceAdminReceiver::class.java)

        if (!DeviceOwnerProvisioner.isDeviceOrProfileOwner(this)) {
            showErrorDialog(
                "Passcode reset requires Device Owner or Profile Owner permissions.\n\n" +
                "The app is not provisioned as Owner on this user profile."
            )
            return
        }

        val success = DeviceOwnerProvisioner.resetDevicePassword(this, enteredCode)
        if (success) {
            OwnerProfilePinRotator.markAppliedForCurrentSlot(this)
            showSuccessDialog(enteredCode)
        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O &&
            !dpm.isResetPasswordTokenActive(admin)
        ) {
            showErrorDialog(
                "Reset password token is not active yet.\n\n" +
                    "Lock the device once (power button), then try again."
            )
        } else {
            showErrorDialog("System rejected the password reset. Ensure the code matches device security constraints.")
        }
    }

    private fun showSuccessDialog(resetPin: String) {
        AlertDialog.Builder(this)
            .setTitle("Passcode Reset Successful")
            .setMessage(
                "The system lock screen PIN has been reset successfully.\n\n" +
                "Use the access code you just typed to unlock the device:\n\n" +
                "PIN: $resetPin"
            )
            .setCancelable(false)
            .setPositiveButton("Dismiss") { dialog, _ ->
                dialog.dismiss()
                finish()
            }
            .show()
    }

    private fun showErrorDialog(message: String) {
        AlertDialog.Builder(this)
            .setTitle("Recovery Failed")
            .setMessage(message)
            .setPositiveButton("OK", null)
            .show()
    }
}
