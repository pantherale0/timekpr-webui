package com.timekpr.agent.policy

import android.content.Context
import org.json.JSONObject

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
            "This setting is managed by your parent through TimeKpr."
        const val DEFAULT_LONG_SUPPORT_MESSAGE =
            "This device is protected by TimeKpr parental controls. Your parent manages " +
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
            )
        }
    }

    fun toJson(): JSONObject {
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
    private val policyByUser = mutableMapOf<String, DeviceRestrictionPolicy>()

    fun policyForUser(username: String): DeviceRestrictionPolicy {
        return policyByUser[username] ?: DeviceRestrictionPolicy()
    }

    fun syncPolicy(username: String, policyJson: JSONObject?) {
        val policy = DeviceRestrictionPolicy.parse(policyJson)
        policyByUser[username] = policy
        persist()
    }

    fun restore() {
        policyByUser.clear()
        val raw = prefs.getString(KEY_POLICIES, null) ?: return
        try {
            val root = JSONObject(raw)
            root.keys().forEach { username ->
                val entry = root.optJSONObject(username) ?: return@forEach
                policyByUser[username] = DeviceRestrictionPolicy.parse(entry)
            }
        } catch (_: Exception) {
            policyByUser.clear()
        }
    }

    private fun persist() {
        val root = JSONObject()
        policyByUser.forEach { (username, policy) ->
            root.put(username, policy.toJson())
        }
        prefs.edit().putString(KEY_POLICIES, root.toString()).apply()
    }

    companion object {
        private const val PREFS_NAME = "timekpr_device_restrictions"
        private const val KEY_POLICIES = "policies_by_user"
    }
}
