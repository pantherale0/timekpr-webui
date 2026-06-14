package com.guardian.agent.vpn

/**
 * Simple registrable-domain heuristic (last two labels). No Public Suffix List in v1.
 */
internal object DomainGrouping {
    fun registrableDomain(domain: String): String {
        val normalized = domain.trim().lowercase().trimEnd('.')
        if (normalized.isEmpty()) return normalized
        val labels = normalized.split('.').filter { it.isNotEmpty() }
        return when {
            labels.size >= 2 -> labels.takeLast(2).joinToString(".")
            else -> normalized
        }
    }
}
