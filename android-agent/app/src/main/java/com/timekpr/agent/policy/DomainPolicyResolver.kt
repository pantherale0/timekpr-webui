package com.timekpr.agent.policy

import android.content.Context
import com.timekpr.agent.TimeKprApplication
import com.timekpr.agent.util.AndroidUsers
import android.os.Process

/**
 * Maps an Android multi-user ID to the domain-policy key stored by the server
 * (typically [ManagedUserDeviceMap.linux_uid] as a string).
 */
object DomainPolicyResolver {
    fun resolvePolicyUid(context: Context, androidUserId: Int): String {
        val app = TimeKprApplication.from(context)
        val domainStore = app.domainPolicyStore
        val timeStore = app.timeLimitStore

        val candidates = linkedSetOf<String>()
        candidates.add(androidUserId.toString())
        if (androidUserId == 0) {
            candidates.add("0")
        }

        ProfileProvisioningStore(context).displayNameForUserId(androidUserId)?.let { provisionedName ->
            domainStore.policies.entries
                .filter { it.value.linuxUsername.equals(provisionedName, ignoreCase = true) }
                .forEach { candidates.add(it.key) }
        }

        AndroidUsers.displayNameForUser(context, androidUserId)?.let { deviceName ->
            domainStore.policies.entries
                .filter { it.value.linuxUsername.equals(deviceName, ignoreCase = true) }
                .forEach { candidates.add(it.key) }
        }

        timeStore.getUsernameForUid(androidUserId)?.let { username ->
            domainStore.policies.entries
                .filter { it.value.linuxUsername.equals(username, ignoreCase = true) }
                .forEach { candidates.add(it.key) }
        }

        val callingUserId = Process.myUid() / 100_000
        val provisionedName = AndroidUsers.displayNameForUser(context, androidUserId)
            ?: ProfileProvisioningStore(context).displayNameForUserId(androidUserId)
        val reportedName = when {
            !provisionedName.isNullOrBlank() -> provisionedName
            androidUserId == callingUserId -> AndroidUsers.currentLinuxUsername(context)
            androidUserId == AndroidUsers.activeUserUid(context) -> AndroidUsers.currentLinuxUsername(context)
            else -> AndroidUsers.linuxUsersPayload(context)
                .firstOrNull { (it["uid"] as? Number)?.toInt() == androidUserId }
                ?.get("username") as? String
        }
        if (!reportedName.isNullOrBlank()) {
            domainStore.policies.entries
                .filter { it.value.linuxUsername.equals(reportedName, ignoreCase = true) }
                .forEach { candidates.add(it.key) }
            ProfileProvisioningStore(context).displayNameForUserId(androidUserId)?.let { provisioned ->
                domainStore.policies.entries
                    .filter { it.value.linuxUsername.equals(provisioned, ignoreCase = true) }
                    .forEach { candidates.add(it.key) }
            }
        }

        if (androidUserId != 0 && androidUserId == callingUserId) {
            domainStore.policies.keys.forEach { candidates.add(it) }
        }

        for (key in candidates) {
            if (domainStore.policyForUid(key) != null) {
                return key
            }
        }
        for (key in candidates) {
            if (domainStore.blockedDomainsForUid(key).isNotEmpty()) {
                return key
            }
        }
        return androidUserId.toString()
    }

    fun loadVpnPolicyForUser(context: Context, androidUserId: Int): VpnDomainPolicy {
        val domainStore = TimeKprApplication.from(context).domainPolicyStore
        val policyUid = resolvePolicyUid(context, androidUserId)
        val blocked = domainStore.blockedDomainsForUid(policyUid)
        val allowed = domainStore.policyForUid(policyUid)?.allowedDomains ?: emptySet()
        return VpnDomainPolicy(
            policyUid = policyUid,
            blockedDomains = blocked,
            allowedDomains = allowed,
        )
    }

    data class VpnDomainPolicy(
        val policyUid: String,
        val blockedDomains: Set<String>,
        val allowedDomains: Set<String>,
    )
}
