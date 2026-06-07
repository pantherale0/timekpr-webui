package com.timekpr.agent.policy

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import com.timekpr.agent.util.PrefXmlReader
import java.io.File
import java.time.DayOfWeek
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneId
import java.util.concurrent.ConcurrentHashMap

/**
 * Local TimeKpr-style screen time state for Android profiles.
 * Mirrors debug-agent / TimeKpr D-Bus semantics using in-app storage.
 */
class TimeLimitStore(context: Context) {
    private val appContext = context.applicationContext
    private val prefs = appContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    private val users = ConcurrentHashMap<String, UserTimeState>()
    private val screentimeExemptByUser = ConcurrentHashMap<String, Set<String>>()

    init {
        restoreScreentimeExemptPackages()
    }

    fun getUsernameForUid(uid: Int): String? {
        val found = users.entries.find { it.value.linuxUid == uid }?.key
        if (found != null) return found

        prefs.all.forEach { (key, value) ->
            if (key.startsWith("user_") && value is String) {
                try {
                    val json = JSONObject(value)
                    if (json.optInt("linux_uid") == uid) {
                        return key.substring(5)
                    }
                } catch (_: Exception) {}
            }
        }
        return null
    }

    fun reloadFromPrefs() {
        users.clear()
    }

    fun allUsernames(): Set<String> {
        val names = mutableSetOf<String>()
        names.addAll(users.keys)
        readUsernamesFromPrefsStore(names)
        readUsernamesFromPrefsFile(names)
        return names
    }

    private fun readUsernamesFromPrefsStore(names: MutableSet<String>) {
        prefs.all.keys.forEach { key ->
            if (key.startsWith("user_")) {
                names.add(key.substring(5))
            }
        }
    }

    private fun readUsernamesFromPrefsFile(names: MutableSet<String>) {
        val file = File(appContext.applicationInfo.dataDir, "shared_prefs/$PREFS_NAME.xml")
        PrefXmlReader.stringValues(file).keys.forEach { key ->
            if (key.startsWith("user_")) {
                names.add(key.substring(5))
            }
        }
    }

    data class UserTimeState(
        var linuxUid: Int,
        var timeSpentDay: Int,
        var timeLeftDay: Int,
        var limit: Int,
        var enabled: Boolean,
        var allowedDays: MutableSet<Int>,
        var weeklySchedule: MutableMap<String, Double>,
        var allowedHours: MutableMap<String, MutableMap<String, HourSlot>>,
    )

    data class HourSlot(
        var startMin: Int = 0,
        var endMin: Int = 60,
        var uacc: Int = 0,
    )

    fun ensureUser(username: String, defaultUid: Int, defaultSeconds: Int = 2 * 3600): UserTimeState {
        val loaded = loadPersisted(username)
        val state = users.getOrPut(username) {
            loaded ?: UserTimeState(
                linuxUid = defaultUid,
                timeSpentDay = 0,
                timeLeftDay = defaultSeconds,
                limit = defaultSeconds,
                enabled = true,
                allowedDays = (1..7).toMutableSet(),
                weeklySchedule = mutableMapOf(),
                allowedHours = defaultAllowedHours(),
            )
        }
        if (loaded != null && loaded.linuxUid > 0) {
            state.linuxUid = loaded.linuxUid
        } else if (defaultUid > 0 && state.linuxUid <= 0) {
            state.linuxUid = defaultUid
            persist(username, state)
        }
        return state
    }

    fun modifyTimeLeft(username: String, operation: String, seconds: Int): Boolean {
        val state = users[username] ?: return false
        when (operation) {
            "+" -> state.timeLeftDay += seconds
            "-" -> state.timeLeftDay = maxOf(0, state.timeLeftDay - seconds)
            else -> return false
        }
        persist(username, state)
        return true
    }

    fun setWeeklySchedule(username: String, schedule: Map<String, Double>): Boolean {
        val state = users[username] ?: return false
        state.weeklySchedule.clear()
        state.weeklySchedule.putAll(schedule)
        val today = LocalDate.now().dayOfWeek.name.lowercase()
        schedule[today]?.let { hours ->
            val limitSeconds = (hours * 3600).toInt().coerceAtLeast(0)
            state.limit = limitSeconds
            state.timeLeftDay = minOf(state.timeLeftDay, state.limit)
        }
        persist(username, state)
        return true
    }

    fun setAllowedHours(username: String, intervals: Map<String, Map<String, Map<String, Any>>>): Boolean {
        val state = users[username] ?: return false
        val normalized = defaultAllowedHours()
        intervals.forEach { (day, hours) ->
            val dayMap = mutableMapOf<String, HourSlot>()
            hours.forEach { (hour, slot) ->
                dayMap[hour] = HourSlot(
                    startMin = (slot["STARTMIN"] as? Number)?.toInt() ?: 0,
                    endMin = (slot["ENDMIN"] as? Number)?.toInt() ?: 60,
                    uacc = (slot["UACC"] as? Number)?.toInt() ?: 0,
                )
            }
            normalized[day] = dayMap
        }
        state.allowedHours = normalized
        persist(username, state)
        return true
    }

    fun recordUsage(username: String, seconds: Int) {
        val state = users[username] ?: return
        state.timeSpentDay += seconds
        state.timeLeftDay = maxOf(0, state.timeLeftDay - seconds)
        persist(username, state)
    }

    fun screentimeExemptPackages(username: String): Set<String> {
        return screentimeExemptByUser[username] ?: emptySet()
    }

    fun setScreentimeExemptPackages(username: String, packages: Set<String>): Boolean {
        if (!users.containsKey(username) && loadPersisted(username) == null) {
            return false
        }
        screentimeExemptByUser[username] = packages.toSet()
        persistScreentimeExemptPackages()
        return true
    }

    fun isAccessAllowed(username: String, zoneId: ZoneId = ZoneId.systemDefault()): Boolean {
        val state = users[username] ?: return true
        if (!state.enabled) return false
        if (state.timeLeftDay <= 0) return false

        val now = LocalTime.now(zoneId)
        val dayIndex = dayOfWeekIndex(LocalDate.now(zoneId).dayOfWeek)
        if (dayIndex !in state.allowedDays) return false

        val hourKey = now.hour.toString()
        val dayHours = state.allowedHours[dayIndex.toString()] ?: return true
        val slot = dayHours[hourKey] ?: return true
        val minute = now.minute
        return minute >= slot.startMin && minute < slot.endMin && slot.uacc == 0
    }

    fun configPayload(username: String, state: UserTimeState): JSONObject {
        val allowedHoursJson = JSONObject()
        state.allowedHours.forEach { (day, hours) ->
            val dayJson = JSONObject()
            hours.forEach { (hour, slot) ->
                dayJson.put(
                    hour,
                    JSONObject()
                        .put("STARTMIN", slot.startMin)
                        .put("ENDMIN", slot.endMin)
                        .put("UACC", slot.uacc),
                )
            }
            allowedHoursJson.put(day, dayJson)
        }
        val weeklyJson = JSONObject()
        state.weeklySchedule.forEach { (day, hours) -> weeklyJson.put(day, hours) }

        return JSONObject()
            .put("USERNAME", username)
            .put("LINUX_UID", state.linuxUid)
            .put("TIME_SPENT_DAY", state.timeSpentDay)
            .put("TIME_LEFT_DAY", state.timeLeftDay)
            .put("LIMIT", state.limit)
            .put("ENABLED", state.enabled)
            .put("ALLOWED_DAYS", state.allowedDays.sorted().map { it.toString() })
            .put("WEEKLY_SCHEDULE", weeklyJson)
            .put("ALLOWED_HOURS", allowedHoursJson)
    }

    private fun dayOfWeekIndex(day: DayOfWeek): Int {
        return when (day) {
            DayOfWeek.MONDAY -> 1
            DayOfWeek.TUESDAY -> 2
            DayOfWeek.WEDNESDAY -> 3
            DayOfWeek.THURSDAY -> 4
            DayOfWeek.FRIDAY -> 5
            DayOfWeek.SATURDAY -> 6
            DayOfWeek.SUNDAY -> 7
        }
    }

    private fun defaultAllowedHours(): MutableMap<String, MutableMap<String, HourSlot>> {
        val days = mutableMapOf<String, MutableMap<String, HourSlot>>()
        for (day in 1..7) {
            val hours = mutableMapOf<String, HourSlot>()
            for (hour in 0..23) {
                hours[hour.toString()] = HourSlot()
            }
            days[day.toString()] = hours
        }
        return days
    }

    private fun persist(username: String, state: UserTimeState) {
        val json = JSONObject()
            .put("linux_uid", state.linuxUid)
            .put("time_spent_day", state.timeSpentDay)
            .put("time_left_day", state.timeLeftDay)
            .put("limit", state.limit)
            .put("enabled", state.enabled)
        prefs.edit().putString("user_$username", json.toString()).apply()
    }

    private fun loadPersisted(username: String): UserTimeState? {
        val raw = readPersistedRaw(username) ?: return null
        return parsePersistedState(raw)
    }

    private fun readPersistedRaw(username: String): String? {
        val key = "user_$username"
        PrefXmlReader.stringValues(
            File(appContext.applicationInfo.dataDir, "shared_prefs/$PREFS_NAME.xml"),
        )[key]?.let { return it }
        return prefs.getString(key, null)
    }

    private fun parsePersistedState(raw: String): UserTimeState? {
        return try {
            val json = JSONObject(raw)
            UserTimeState(
                linuxUid = json.optInt("linux_uid"),
                timeSpentDay = json.optInt("time_spent_day"),
                timeLeftDay = json.optInt("time_left_day"),
                limit = json.optInt("limit"),
                enabled = json.optBoolean("enabled", true),
                allowedDays = (1..7).toMutableSet(),
                weeklySchedule = mutableMapOf(),
                allowedHours = defaultAllowedHours(),
            )
        } catch (_: Exception) {
            null
        }
    }

    private fun persistScreentimeExemptPackages() {
        val root = JSONObject()
        screentimeExemptByUser.forEach { (username, packages) ->
            root.put(username, JSONArray(packages.toList()))
        }
        prefs.edit().putString(KEY_SCREENTIME_EXEMPT, root.toString()).apply()
    }

    private fun restoreScreentimeExemptPackages() {
        val raw = prefs.getString(KEY_SCREENTIME_EXEMPT, null) ?: return
        try {
            val root = JSONObject(raw)
            val keys = root.keys()
            while (keys.hasNext()) {
                val username = keys.next()
                val array = root.optJSONArray(username) ?: continue
                val packages = mutableSetOf<String>()
                for (index in 0 until array.length()) {
                    array.optString(index).takeIf { it.isNotBlank() }?.let { packages += it }
                }
                screentimeExemptByUser[username] = packages
            }
        } catch (_: Exception) {
            screentimeExemptByUser.clear()
        }
    }

    companion object {
        private const val PREFS_NAME = "timekpr_time_limits"
        private const val KEY_SCREENTIME_EXEMPT = "screentime_exempt_packages"
    }
}
