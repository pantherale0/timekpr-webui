package com.timekpr.agent.protocol

import android.content.Context
import com.timekpr.agent.TimeKprApplication
import com.timekpr.agent.policy.AppPolicyStore
import com.timekpr.agent.policy.DomainPolicyStore
import com.timekpr.agent.policy.TimeLimitStore
import com.timekpr.agent.policy.UidPolicy
import com.timekpr.agent.discovery.DiscoveredApp
import com.timekpr.agent.discovery.InstalledAppsDiscovery
import com.timekpr.agent.util.AndroidUsers
import org.json.JSONArray
import org.json.JSONObject

class CommandDispatcher(
    private val context: Context,
    private val appPolicyStore: AppPolicyStore,
    private val onDomainPolicyChanged: () -> Unit,
    private val onAppPolicyChanged: (String) -> Unit,
    private val onTimePolicyChanged: (String) -> Unit,
) {
    private val app: TimeKprApplication = TimeKprApplication.from(context)
    private val timeLimitStore: TimeLimitStore = app.timeLimitStore
    private val domainPolicyStore: DomainPolicyStore = app.domainPolicyStore

    fun handle(action: String, username: String, args: JSONObject): DispatchResult {
        return when (action) {
            "validate_user" -> handleValidateUser(username)
            "modify_time_left" -> handleModifyTimeLeft(username, args)
            "set_weekly_time_limits" -> handleWeeklyLimits(username, args)
            "set_allowed_hours" -> handleAllowedHours(username, args)
            "sync_domain_policy" -> handleSyncDomainPolicy(args)
            "get_domain_policy_state" -> handleGetDomainPolicyState()
            "begin_domain_policy_sync" -> handleBeginDomainSync(args)
            "delete_domain_policy_sources" -> handleDeleteDomainSources(args)
            "sync_domain_policy_chunk" -> handleDomainChunk(args)
            "update_domain_policy_manifest" -> handleDomainManifest(args)
            "finalize_domain_policy_sync" -> handleFinalizeDomainSync(args)
            "abort_domain_policy_sync" -> handleAbortDomainSync(args)
            "sync_apparmor_policy" -> handleAppPolicy(username, args)
            "refresh_installed_apps" -> handleRefreshInstalledApps(username)
            else -> DispatchResult(false, "Unsupported action '$action'", JSONObject())
        }
    }

    private fun handleValidateUser(username: String): DispatchResult {
        val state = timeLimitStore.ensureUser(
            username,
            AndroidUsers.currentLinuxUid(context),
        )
        val config = timeLimitStore.configPayload(username, state)
        config.put("DOMAIN_POLICY_SOURCE_IDS", JSONArray())
        config.put("APPARMOR_POLICY_COUNT", appPolicyStore.rulesForUser(username).size)
        return DispatchResult(
            success = true,
            message = "User validated successfully",
            data = JSONObject().put("config", config),
        )
    }

    private fun handleModifyTimeLeft(username: String, args: JSONObject): DispatchResult {
        val operation = args.optString("operation", "+")
        val seconds = args.optInt("seconds", 0).coerceAtLeast(0)
        val ok = timeLimitStore.modifyTimeLeft(username, operation, seconds)
        if (ok) onTimePolicyChanged(username)
        return DispatchResult(ok, if (ok) "Time updated" else "Unknown user", JSONObject())
    }

    private fun handleWeeklyLimits(username: String, args: JSONObject): DispatchResult {
        val scheduleObj = args.optJSONObject("schedule") ?: return DispatchResult(false, "Missing schedule", JSONObject())
        val schedule = mutableMapOf<String, Double>()
        scheduleObj.keys().forEach { day ->
            schedule[day] = scheduleObj.optDouble(day, 0.0)
        }
        val ok = timeLimitStore.setWeeklySchedule(username, schedule)
        if (ok) onTimePolicyChanged(username)
        return DispatchResult(ok, if (ok) "Weekly limits configured" else "Unknown user", JSONObject())
    }

    private fun handleAllowedHours(username: String, args: JSONObject): DispatchResult {
        val intervalsObj = args.optJSONObject("intervals")
            ?: return DispatchResult(false, "Missing intervals", JSONObject())
        val intervals = mutableMapOf<String, Map<String, Map<String, Any>>>()
        intervalsObj.keys().forEach { day ->
            val hoursObj = intervalsObj.optJSONObject(day) ?: return@forEach
            val hours = mutableMapOf<String, Map<String, Any>>()
            hoursObj.keys().forEach { hour ->
                val slot = hoursObj.optJSONObject(hour) ?: return@forEach
                hours[hour] = mapOf(
                    "STARTMIN" to slot.optInt("STARTMIN", 0),
                    "ENDMIN" to slot.optInt("ENDMIN", 60),
                    "UACC" to slot.optInt("UACC", 0),
                )
            }
            intervals[day] = hours
        }
        val ok = timeLimitStore.setAllowedHours(username, intervals)
        if (ok) onTimePolicyChanged(username)
        return DispatchResult(ok, if (ok) "Allowed hours updated" else "Unknown user", JSONObject())
    }

    private fun handleSyncDomainPolicy(args: JSONObject): DispatchResult {
        domainPolicyStore.applyFullSync(args)
        onDomainPolicyChanged()
        return DispatchResult(true, "Domain policy synchronized", JSONObject())
    }

    private fun handleGetDomainPolicyState(): DispatchResult {
        return DispatchResult(true, "Fetched domain policy state", domainPolicyStore.getStatePayload())
    }

    private fun handleBeginDomainSync(args: JSONObject): DispatchResult {
        val syncId = args.optString("sync_id").trim()
        if (syncId.isEmpty()) return DispatchResult(false, "Missing sync_id", JSONObject())
        domainPolicyStore.beginSync(syncId)
        return DispatchResult(true, "Started domain policy sync", JSONObject())
    }

    private fun handleDeleteDomainSources(args: JSONObject): DispatchResult {
        val syncId = args.optString("sync_id").trim()
        val session = domainPolicyStore.syncSessions[syncId]
            ?: return DispatchResult(false, "Unknown sync_id", JSONObject())
        val sourceIds = args.optJSONArray("source_ids") ?: JSONArray()
        for (index in 0 until sourceIds.length()) {
            session.sources.remove(sourceIds.optString(index))
        }
        return DispatchResult(true, "Deleted domain policy sources", JSONObject())
    }

    private fun handleDomainChunk(args: JSONObject): DispatchResult {
        val syncId = args.optString("sync_id").trim()
        val session = domainPolicyStore.syncSessions[syncId]
            ?: return DispatchResult(false, "Unknown sync_id", JSONObject())
        val sourceId = args.optString("source_id").trim()
        if (sourceId.isEmpty()) return DispatchResult(false, "Missing source_id", JSONObject())
        val revision = args.optString("revision")
        val entry = session.sources.getOrPut(sourceId) {
            DomainPolicyStore.SourceEntry(revision, mutableSetOf())
        }
        entry.revision = revision.ifBlank { entry.revision }
        val domains = args.optJSONArray("domains") ?: JSONArray()
        for (index in 0 until domains.length()) {
            val domain = domains.optString(index).trim().lowercase().trimEnd('.')
            if (domain.isNotEmpty()) entry.domains.add(domain)
        }
        return DispatchResult(true, "Accepted domain policy chunk", JSONObject())
    }

    private fun handleDomainManifest(args: JSONObject): DispatchResult {
        val syncId = args.optString("sync_id").trim()
        val session = domainPolicyStore.syncSessions[syncId]
            ?: return DispatchResult(false, "Unknown sync_id", JSONObject())
        val policiesObj = args.optJSONObject("policies")
            ?: return DispatchResult(false, "Missing policies", JSONObject())
        session.policies.clear()
        policiesObj.keys().forEach { uid ->
            val entry = policiesObj.optJSONObject(uid) ?: return@forEach
            val sourceIds = entry.optJSONArray("source_ids")?.let { array ->
                (0 until array.length()).map { array.optString(it) }
            } ?: emptyList()
            session.policies[uid] = UidPolicy(
                linuxUsername = entry.optString("linux_username"),
                sourceIds = sourceIds,
            )
        }
        return DispatchResult(true, "Updated domain policy manifest", JSONObject())
    }

    private fun handleFinalizeDomainSync(args: JSONObject): DispatchResult {
        val syncId = args.optString("sync_id").trim()
        val ok = domainPolicyStore.finalizeSync(syncId)
        if (ok) onDomainPolicyChanged()
        return DispatchResult(ok, if (ok) "Finalized domain policy sync" else "Unknown sync_id", JSONObject())
    }

    private fun handleAbortDomainSync(args: JSONObject): DispatchResult {
        val syncId = args.optString("sync_id").trim()
        domainPolicyStore.syncSessions.remove(syncId)
        return DispatchResult(true, "Aborted domain policy sync", JSONObject())
    }

    private fun handleAppPolicy(username: String, args: JSONObject): DispatchResult {
        val policies = args.optJSONArray("policies") ?: JSONArray()
        appPolicyStore.syncPolicies(username, policies)
        onAppPolicyChanged(username)
        return DispatchResult(true, "App policies synchronized", JSONObject())
    }

    private fun handleRefreshInstalledApps(username: String): DispatchResult {
        timeLimitStore.ensureUser(username, AndroidUsers.currentLinuxUid(context))
        val apps = InstalledAppsDiscovery.discover(context)
        return DispatchResult(
            success = true,
            message = "Installed apps refresh queued",
            data = JSONObject().put("queued", true),
            followUpApps = apps,
            followUpUsername = username,
        )
    }

    data class DispatchResult(
        val success: Boolean,
        val message: String,
        val data: JSONObject,
        val followUpApps: List<DiscoveredApp>? = null,
        val followUpUsername: String? = null,
    )
}
