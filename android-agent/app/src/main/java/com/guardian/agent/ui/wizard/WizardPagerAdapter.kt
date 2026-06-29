package com.guardian.agent.ui.wizard

import androidx.fragment.app.Fragment
import androidx.fragment.app.FragmentActivity
import androidx.viewpager2.adapter.FragmentStateAdapter

class WizardPagerAdapter(activity: FragmentActivity) : FragmentStateAdapter(activity) {
    override fun getItemCount(): Int = 3

    override fun createFragment(position: Int): Fragment = when (position) {
        0 -> WizardStep1Fragment()
        1 -> WizardStep2Fragment()
        else -> WizardStep3Fragment()
    }
}
