package com.guardian.agent.ui.wizard

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.fragment.app.Fragment
import com.guardian.agent.R
import com.guardian.agent.databinding.FragmentWizardStep2Binding
import com.guardian.agent.utils.permissions.PermissionState

class WizardStep2Fragment : Fragment() {

    private var _binding: FragmentWizardStep2Binding? = null
    private val binding get() = _binding!!

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View {
        _binding = FragmentWizardStep2Binding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        bindCard(
            card = binding.cardAdmin,
            iconRes = R.drawable.ic_shield_admin,
            titleRes = R.string.wizard_perm_admin_title,
            descRes = R.string.wizard_perm_admin_desc,
        )
        bindCard(
            card = binding.cardVpn,
            iconRes = R.drawable.ic_vpn,
            titleRes = R.string.wizard_perm_vpn_title,
            descRes = R.string.wizard_perm_vpn_desc,
        )
        bindCard(
            card = binding.cardUsage,
            iconRes = R.drawable.ic_usage,
            titleRes = R.string.wizard_perm_usage_title,
            descRes = R.string.wizard_perm_usage_desc,
        )
    }

    override fun onDestroyView() {
        _binding = null
        super.onDestroyView()
    }

    fun updatePermissionStates(state: PermissionState) {
        if (_binding == null) return
        setCardGranted(binding.cardAdmin, state.deviceAdmin)
        setCardGranted(binding.cardVpn, state.vpn)
        setCardGranted(binding.cardUsage, state.usageAccess)
    }

    private fun bindCard(
        card: com.guardian.agent.databinding.ItemWizardPermissionCardBinding,
        iconRes: Int,
        titleRes: Int,
        descRes: Int,
    ) {
        card.permissionIcon.setImageResource(iconRes)
        card.permissionTitle.setText(titleRes)
        card.permissionDesc.setText(descRes)
    }

    private fun setCardGranted(
        card: com.guardian.agent.databinding.ItemWizardPermissionCardBinding,
        granted: Boolean,
    ) {
        card.permissionStatus.setImageResource(
            if (granted) R.drawable.ic_check_circle else R.drawable.status_pending_ring,
        )
    }
}
