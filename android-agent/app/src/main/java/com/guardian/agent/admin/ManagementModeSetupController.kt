package com.guardian.agent.admin

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.card.MaterialCardView
import com.guardian.agent.GuardianApplication
import com.guardian.agent.R
import com.guardian.agent.config.AgentConfigStore
import com.guardian.agent.protocol.AgentWebSocketClient
import com.guardian.agent.service.AgentConnectionState
import com.guardian.agent.service.AgentConnectionStatus
import com.guardian.agent.service.AgentSessionCoordinator
import com.guardian.agent.ui.MainActivity
import com.guardian.agent.util.GoogleAccountSetupHelper
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * Guides management-mode selection during MDM compliance and first-launch setup.
 * For single-user mode on GMS devices, optionally prompts to add a parent Google account,
 * then waits on-device until the server approves pairing.
 */
class ManagementModeSetupController(
    private val activity: AppCompatActivity,
    private val complianceFlow: Boolean,
) {
    private var pendingMode: String? = null
    private var awaitingAccountResult = false
    private var showingGoogleAccountStep = false
    private var showingApprovalWait = false
    private var enrollmentStarted = false
    private var approvalPollJob: Job? = null
    private var exitRequested = false

    private lateinit var panelModeSelection: View
    private lateinit var panelGoogleAccount: View
    private lateinit var panelApprovalWait: View
    private lateinit var cardExclusiveDo: MaterialCardView
    private lateinit var cardSecondaryUsers: MaterialCardView
    private lateinit var btnConfirm: Button
    private lateinit var btnSkipGoogleAccount: Button
    private lateinit var btnSignInGoogleAccount: Button
    private lateinit var googleAccountStatus: TextView
    private lateinit var approvalStatusText: TextView

    fun onCreate(savedInstanceState: Bundle?) {
        ProvisioningBootstrap.stageFromAdminExtras(
            activity,
            ProvisioningBootstrap.readAdminExtras(activity.intent),
        )

        activity.setContentView(R.layout.activity_admin_policy_compliance)
        bindViews()
        restoreState(savedInstanceState)
        setupModeSelection()
        setupGoogleAccountStep()
        updateVisiblePanel()

        if (showingApprovalWait) {
            resumeApprovalWait()
        }
    }

    fun onResume() {
        if (!showingGoogleAccountStep || !awaitingAccountResult) {
            return
        }
        awaitingAccountResult = false
        if (GoogleAccountSetupHelper.hasGoogleAccount(activity)) {
            transitionToApprovalWait()
        }
    }

    fun onSaveInstanceState(outState: Bundle) {
        pendingMode?.let { outState.putString(STATE_PENDING_MODE, it) }
        outState.putBoolean(STATE_AWAITING_ACCOUNT, awaitingAccountResult)
        outState.putBoolean(STATE_SHOWING_GOOGLE, showingGoogleAccountStep)
        outState.putBoolean(STATE_SHOWING_APPROVAL, showingApprovalWait)
        outState.putBoolean(STATE_ENROLLMENT_STARTED, enrollmentStarted)
    }

    fun onDestroy() {
        approvalPollJob?.cancel()
    }

    private fun bindViews() {
        panelModeSelection = activity.findViewById(R.id.panelModeSelection)
        panelGoogleAccount = activity.findViewById(R.id.panelGoogleAccount)
        panelApprovalWait = activity.findViewById(R.id.panelApprovalWait)
        cardExclusiveDo = activity.findViewById(R.id.cardExclusiveDo)
        cardSecondaryUsers = activity.findViewById(R.id.cardSecondaryUsers)
        btnConfirm = activity.findViewById(R.id.btnConfirm)
        btnSkipGoogleAccount = activity.findViewById(R.id.btnSkipGoogleAccount)
        btnSignInGoogleAccount = activity.findViewById(R.id.btnSignInGoogleAccount)
        googleAccountStatus = activity.findViewById(R.id.googleAccountStatus)
        approvalStatusText = activity.findViewById(R.id.approvalStatusText)
    }

    private fun restoreState(savedInstanceState: Bundle?) {
        if (savedInstanceState == null) {
            return
        }
        pendingMode = savedInstanceState.getString(STATE_PENDING_MODE)
        awaitingAccountResult = savedInstanceState.getBoolean(STATE_AWAITING_ACCOUNT, false)
        showingGoogleAccountStep = savedInstanceState.getBoolean(STATE_SHOWING_GOOGLE, false)
        showingApprovalWait = savedInstanceState.getBoolean(STATE_SHOWING_APPROVAL, false)
        enrollmentStarted = savedInstanceState.getBoolean(STATE_ENROLLMENT_STARTED, false)
    }

    private fun setupModeSelection() {
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
            onModeConfirmed(selectedMode())
        }
    }

    private fun setupGoogleAccountStep() {
        btnSkipGoogleAccount.setOnClickListener { transitionToApprovalWait() }
        btnSignInGoogleAccount.setOnClickListener { launchGoogleAccountAdd() }
    }

    private fun selectedMode(): String {
        return if (cardExclusiveDo.isChecked) {
            AgentConfigStore.MANAGEMENT_MODE_EXCLUSIVE_DO
        } else {
            AgentConfigStore.MANAGEMENT_MODE_SECONDARY_USERS
        }
    }

    private fun onModeConfirmed(mode: String) {
        pendingMode = mode
        if (shouldOfferGoogleAccountStep(mode)) {
            showingGoogleAccountStep = true
            googleAccountStatus.visibility = View.GONE
            updateVisiblePanel()
            return
        }
        transitionToApprovalWait()
    }

    private fun shouldOfferGoogleAccountStep(mode: String): Boolean {
        return mode == AgentConfigStore.MANAGEMENT_MODE_EXCLUSIVE_DO &&
            GoogleAccountSetupHelper.canAddGoogleAccount(activity) &&
            !GoogleAccountSetupHelper.hasGoogleAccount(activity)
    }

    private fun launchGoogleAccountAdd() {
        awaitingAccountResult = true
        activity.startActivity(GoogleAccountSetupHelper.buildAddAccountIntent())
    }

    private fun transitionToApprovalWait() {
        val mode = pendingMode ?: selectedMode()
        pendingMode = null
        awaitingAccountResult = false
        showingGoogleAccountStep = false
        showingApprovalWait = true
        updateVisiblePanel()
        updateApprovalStatus(AgentConnectionState.status.value)

        if (!enrollmentStarted) {
            ProvisioningBootstrap.completeManagementModeSetup(activity, mode)
            enrollmentStarted = true
        }

        val config = GuardianApplication.from(activity).configStore.load()
        if (config.serverUrl.isBlank()) {
            completeAndExit()
            return
        }
        if (!config.agentToken.isNullOrBlank()) {
            completeAndExit()
            return
        }

        AgentSessionCoordinator.startMobileAgent(activity)
        startApprovalWait()
    }

    private fun resumeApprovalWait() {
        updateApprovalStatus(AgentConnectionState.status.value)
        val config = GuardianApplication.from(activity).configStore.load()
        if (!config.agentToken.isNullOrBlank()) {
            completeAndExit()
            return
        }
        startApprovalWait()
    }

    private fun startApprovalWait() {
        approvalPollJob?.cancel()
        approvalPollJob = activity.lifecycleScope.launch {
            launch {
                AgentConnectionState.status.collectLatest { status ->
                    updateApprovalStatus(status)
                }
            }
            while (isActive && showingApprovalWait) {
                if (isApproved()) {
                    completeAndExit()
                    break
                }
                val result = AgentSessionCoordinator.runSyncSession(
                    activity.applicationContext,
                    AgentWebSocketClient.SessionMode.PAIRING_ONLY,
                )
                if (isApproved()) {
                    completeAndExit()
                    break
                }
                delay(
                    if (result.reason == "session_busy") {
                        SESSION_BUSY_RETRY_MS
                    } else {
                        APPROVAL_POLL_INTERVAL_MS
                    },
                )
            }
        }
    }

    private fun isApproved(): Boolean {
        return !GuardianApplication.from(activity).configStore.load().agentToken.isNullOrBlank()
    }

    private fun updateApprovalStatus(status: AgentConnectionStatus) {
        if (!showingApprovalWait) {
            return
        }
        approvalStatusText.setText(
            when (status) {
                AgentConnectionStatus.PENDING_APPROVAL -> R.string.provisioning_approval_waiting
                AgentConnectionStatus.ERROR -> R.string.provisioning_approval_retrying
                else -> R.string.provisioning_approval_connecting
            },
        )
    }

    private fun completeAndExit() {
        if (exitRequested || activity.isFinishing) {
            return
        }
        exitRequested = true
        showingApprovalWait = false
        approvalPollJob?.cancel()

        if (isApproved()) {
            AgentSessionCoordinator.scheduleSync(activity, reason = "pairing_approved")
        }

        if (complianceFlow) {
            activity.setResult(AppCompatActivity.RESULT_OK)
            activity.finish()
            return
        }

        activity.startActivity(
            Intent(activity, MainActivity::class.java).apply {
                addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_NEW_TASK)
            },
        )
        activity.finish()
    }

    private fun updateVisiblePanel() {
        panelModeSelection.visibility = when {
            showingGoogleAccountStep || showingApprovalWait -> View.GONE
            else -> View.VISIBLE
        }
        panelGoogleAccount.visibility = if (showingGoogleAccountStep) View.VISIBLE else View.GONE
        panelApprovalWait.visibility = if (showingApprovalWait) View.VISIBLE else View.GONE
    }

    companion object {
        private const val STATE_PENDING_MODE = "pending_management_mode"
        private const val STATE_AWAITING_ACCOUNT = "awaiting_google_account"
        private const val STATE_SHOWING_GOOGLE = "showing_google_account_step"
        private const val STATE_SHOWING_APPROVAL = "showing_approval_wait"
        private const val STATE_ENROLLMENT_STARTED = "enrollment_started"
        private const val APPROVAL_POLL_INTERVAL_MS = 5_000L
        private const val SESSION_BUSY_RETRY_MS = 1_000L
    }
}
