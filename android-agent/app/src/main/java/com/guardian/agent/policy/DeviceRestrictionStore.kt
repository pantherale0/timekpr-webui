package com.guardian.agent.policy

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

data class ProfileToProvision(
    val username: String,
    val profileType: String
)

data class ForceInstalledApp(
    val packageName: String,
    val apkUrl: String,
    val sha256Checksum: String?
)

data class DeviceRestrictionPolicy(
    val screenCaptureDisabled: Boolean = false,
    val cameraAccess: String = CAMERA_ACCESS_UNSPECIFIED,
    val microphoneAccess: String = MICROPHONE_ACCESS_UNSPECIFIED,
    val installAppsDisabled: Boolean = false,
    val uninstallAppsDisabled: Boolean = false,
    val factoryResetDisabled: Boolean = false,
    val adjustVolumeDisabled: Boolean = false,
    val modifyAccountsDisabled: Boolean = false,
    val mountPhysicalMediaDisabled: Boolean = false,
    val bluetoothDisabled: Boolean = false,
    val outgoingCallsDisabled: Boolean = false,
    val smsDisabled: Boolean = false,
    val blockWifiTethering: Boolean = false,
    val blockNfc: Boolean = false,
    val usbDataAccess: String = USB_DATA_ACCESS_UNSPECIFIED,
    val developerSettings: String = DEVELOPER_SETTINGS_UNSPECIFIED,
    val shortSupportMessage: String = DEFAULT_SHORT_SUPPORT_MESSAGE,
    val longSupportMessage: String = DEFAULT_LONG_SUPPORT_MESSAGE,
    val profiles: List<ProfileToProvision> = emptyList(),
    val lockOwnerProfile: Boolean = false,
    val managedProfileUids: List<Int> = emptyList(),
    val forceInstalledApps: List<ForceInstalledApp> = emptyList(),
) {
    val cameraDisabled: Boolean
        get() = when (cameraAccess) {
            CAMERA_ACCESS_DISABLED -> true
            CAMERA_ACCESS_UNSPECIFIED -> false
            else -> false
        }

    val enforceCameraToggle: Boolean
        get() = cameraAccess == CAMERA_ACCESS_ENFORCED

    val microphoneDisabled: Boolean
        get() = microphoneAccess == MICROPHONE_ACCESS_DISABLED

    val enforceMicrophoneToggle: Boolean
        get() = microphoneAccess == MICROPHONE_ACCESS_ENFORCED

    val developerSettingsDisabled: Boolean
        get() = developerSettings == DEVELOPER_SETTINGS_DISABLED

    val developerSettingsAllowed: Boolean
        get() = developerSettings == DEVELOPER_SETTINGS_ALLOWED

    val blockUsbFileTransfer: Boolean
        get() = usbDataAccess == USB_DATA_ACCESS_DISALLOW_FILE ||
            usbDataAccess == USB_DATA_ACCESS_DISALLOW_ALL

    val blockAllUsbData: Boolean
        get() = usbDataAccess == USB_DATA_ACCESS_DISALLOW_ALL

    companion object {
        const val CAMERA_ACCESS_UNSPECIFIED = "CAMERA_ACCESS_UNSPECIFIED"
        const val CAMERA_ACCESS_DISABLED = "CAMERA_ACCESS_DISABLED"
        const val CAMERA_ACCESS_USER_CHOICE = "CAMERA_ACCESS_USER_CHOICE"
        const val CAMERA_ACCESS_ENFORCED = "CAMERA_ACCESS_ENFORCED"

        const val MICROPHONE_ACCESS_UNSPECIFIED = "MICROPHONE_ACCESS_UNSPECIFIED"
        const val MICROPHONE_ACCESS_DISABLED = "MICROPHONE_ACCESS_DISABLED"
        const val MICROPHONE_ACCESS_USER_CHOICE = "MICROPHONE_ACCESS_USER_CHOICE"
        const val MICROPHONE_ACCESS_ENFORCED = "MICROPHONE_ACCESS_ENFORCED"

        const val USB_DATA_ACCESS_UNSPECIFIED = "USB_DATA_ACCESS_UNSPECIFIED"
        const val USB_DATA_ACCESS_ALLOW = "ALLOW_USB_DATA_TRANSFER"
        const val USB_DATA_ACCESS_DISALLOW_FILE = "DISALLOW_USB_FILE_TRANSFER"
        const val USB_DATA_ACCESS_DISALLOW_ALL = "DISALLOW_USB_DATA_TRANSFER"

        const val DEVELOPER_SETTINGS_UNSPECIFIED = "DEVELOPER_SETTINGS_UNSPECIFIED"
        const val DEVELOPER_SETTINGS_DISABLED = "DEVELOPER_SETTINGS_DISABLED"
        const val DEVELOPER_SETTINGS_ALLOWED = "DEVELOPER_SETTINGS_ALLOWED"

        const val DEFAULT_SHORT_SUPPORT_MESSAGE =
            "This setting is managed by your parent through Guardian."
        const val DEFAULT_LONG_SUPPORT_MESSAGE =
            "This device is protected by Guardian parental controls. Your parent manages " +
                "screen time, apps, and websites. Ask them if you need something changed."

        private fun parseUserFacingMessage(json: JSONObject?, fallback: String): String {
            return json?.optString("defaultMessage")?.trim()?.takeIf { it.isNotEmpty() } ?: fallback
        }

        fun parse(json: JSONObject?): DeviceRestrictionPolicy {
            if (json == null) return DeviceRestrictionPolicy()
            val advanced = json.optJSONObject("advancedSecurityOverrides")
            val developerSettings = advanced?.optString(
                "developerSettings",
                DEVELOPER_SETTINGS_UNSPECIFIED,
            ) ?: DEVELOPER_SETTINGS_UNSPECIFIED
            val connectivity = json.optJSONObject("deviceConnectivityManagement")
            val usbDataAccess = connectivity?.optString(
                "usbDataAccess",
                USB_DATA_ACCESS_UNSPECIFIED,
            ) ?: USB_DATA_ACCESS_UNSPECIFIED

            val profilesList = mutableListOf<ProfileToProvision>()
            val profilesArray = json.optJSONArray("profiles")
            if (profilesArray != null) {
                for (i in 0 until profilesArray.length()) {
                    val p = profilesArray.optJSONObject(i)
                    if (p != null) {
                        val name = p.optString("username", "").trim()
                        val type = p.optString("profile_type", "").trim()
                        if (name.isNotEmpty() && type.isNotEmpty()) {
                            profilesList.add(ProfileToProvision(name, type))
                        }
                    }
                }
            }

            val managedUids = mutableListOf<Int>()
            val managedArray = json.optJSONArray("managedProfileUids")
            if (managedArray != null) {
                for (i in 0 until managedArray.length()) {
                    managedArray.optInt(i).takeIf { it >= 0 }?.let { managedUids.add(it) }
                }
            }

            val forceInstalledList = mutableListOf<ForceInstalledApp>()
            val forceInstalledArray = json.optJSONArray("forceInstalledApps")
            if (forceInstalledArray != null) {
                for (i in 0 until forceInstalledArray.length()) {
                    val appJson = forceInstalledArray.optJSONObject(i)
                    if (appJson != null) {
                        val packageName = appJson.optString("packageName", "").trim()
                        val apkUrl = appJson.optString("apkUrl", "").trim()
                        val sha256Checksum = appJson.optString("sha256Checksum", "").trim().takeIf { it.isNotEmpty() }
                        if (packageName.isNotEmpty() && apkUrl.isNotEmpty()) {
                            forceInstalledList.add(ForceInstalledApp(packageName, apkUrl, sha256Checksum))
                        }
                    }
                }
            }

            return DeviceRestrictionPolicy(
                screenCaptureDisabled = json.optBoolean("screenCaptureDisabled", false),
                cameraAccess = json.optString("cameraAccess", CAMERA_ACCESS_UNSPECIFIED),
                microphoneAccess = json.optString("microphoneAccess", MICROPHONE_ACCESS_UNSPECIFIED),
                installAppsDisabled = json.optBoolean("installAppsDisabled", false),
                uninstallAppsDisabled = json.optBoolean("uninstallAppsDisabled", false),
                factoryResetDisabled = json.optBoolean("factoryResetDisabled", false),
                adjustVolumeDisabled = json.optBoolean("adjustVolumeDisabled", false),
                modifyAccountsDisabled = json.optBoolean("modifyAccountsDisabled", false),
                mountPhysicalMediaDisabled = json.optBoolean("mountPhysicalMediaDisabled", false),
                bluetoothDisabled = json.optBoolean("bluetoothDisabled", false),
                outgoingCallsDisabled = json.optBoolean("outgoingCallsDisabled", false),
                smsDisabled = json.optBoolean("smsDisabled", false),
                blockWifiTethering = json.optBoolean("blockWifiTethering", false),
                blockNfc = json.optBoolean("blockNfc", false),
                usbDataAccess = usbDataAccess,
                developerSettings = developerSettings,
                shortSupportMessage = parseUserFacingMessage(
                    json.optJSONObject("shortSupportMessage"),
                    DEFAULT_SHORT_SUPPORT_MESSAGE,
                ),
                longSupportMessage = parseUserFacingMessage(
                    json.optJSONObject("longSupportMessage"),
                    DEFAULT_LONG_SUPPORT_MESSAGE,
                ),
                profiles = profilesList,
                forceInstalledApps = forceInstalledList,
            )
        }
    }

    fun toJson(): JSONObject {
        val profilesArray = JSONArray()
        profiles.forEach { p ->
            profilesArray.put(
                JSONObject()
                    .put("username", p.username)
                    .put("profile_type", p.profileType)
            )
        }

        val forceInstalledArray = JSONArray()
        forceInstalledApps.forEach { app ->
            forceInstalledArray.put(
                JSONObject()
                    .put("packageName", app.packageName)
                    .put("apkUrl", app.apkUrl)
                    .put("sha256Checksum", app.sha256Checksum ?: "")
            )
        }

        return JSONObject()
            .put("screenCaptureDisabled", screenCaptureDisabled)
            .put("cameraAccess", cameraAccess)
            .put("microphoneAccess", microphoneAccess)
            .put("installAppsDisabled", installAppsDisabled)
            .put("uninstallAppsDisabled", uninstallAppsDisabled)
            .put("factoryResetDisabled", factoryResetDisabled)
            .put("adjustVolumeDisabled", adjustVolumeDisabled)
            .put("modifyAccountsDisabled", modifyAccountsDisabled)
            .put("mountPhysicalMediaDisabled", mountPhysicalMediaDisabled)
            .put("bluetoothDisabled", bluetoothDisabled)
            .put("outgoingCallsDisabled", outgoingCallsDisabled)
            .put("smsDisabled", smsDisabled)
            .put("blockWifiTethering", blockWifiTethering)
            .put("blockNfc", blockNfc)
            .put("profiles", profilesArray)
            .put("forceInstalledApps", forceInstalledArray)
            .put("lockOwnerProfile", lockOwnerProfile)
            .put(
                "managedProfileUids",
                JSONArray().apply { managedProfileUids.forEach { put(it) } },
            )
            .put(
                "advancedSecurityOverrides",
                JSONObject().put("developerSettings", developerSettings),
            )
            .put(
                "deviceConnectivityManagement",
                JSONObject().put("usbDataAccess", usbDataAccess),
            )
            .put(
                "shortSupportMessage",
                JSONObject().put("defaultMessage", shortSupportMessage),
            )
            .put(
                "longSupportMessage",
                JSONObject().put("defaultMessage", longSupportMessage),
            )
    }
}

class DeviceRestrictionStore(context: Context) {
    private val appContext = context.applicationContext
    private val prefs = appContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    private var globalPolicy = DeviceRestrictionPolicy()

    fun policyForUser(username: String): DeviceRestrictionPolicy {
        return globalPolicy
    }

    fun syncPolicy(username: String, policyJson: JSONObject?) {
        globalPolicy = DeviceRestrictionPolicy.parse(policyJson)
        persist()
    }

    fun restore() {
        val raw = prefs.getString(KEY_POLICY, null)
        globalPolicy = if (raw != null) {
            try {
                DeviceRestrictionPolicy.parse(JSONObject(raw))
            } catch (_: Exception) {
                DeviceRestrictionPolicy()
            }
        } else {
            DeviceRestrictionPolicy()
        }
    }

    private fun persist() {
        prefs.edit().putString(KEY_POLICY, globalPolicy.toJson().toString()).apply()
    }

    companion object {
        private const val PREFS_NAME = "guardian_device_restrictions"
        private const val KEY_POLICY = "global_device_policy"
    }
}
