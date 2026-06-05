package com.timekpr.agent.discovery

import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.drawable.BitmapDrawable
import android.graphics.drawable.Drawable
import android.util.Base64
import java.io.ByteArrayOutputStream
import java.security.MessageDigest

data class DiscoveredApp(
    val applicationName: String,
    val identifier: String,
    val matchType: String,
    val versionName: String?,
    val iconHash: String?,
    val iconPng: ByteArray?,
)

object InstalledAppsDiscovery {
    const val MATCH_TYPE_PACKAGE = "package"
    const val ANDROID_PACKAGE_PREFIX = "/android/package/"
    const val CHUNK_SIZE = 100
    private const val ICON_SIZE = 64

    /** When true, only apps with a launcher activity are reported. */
    var launcherAppsOnly: Boolean = true

    fun discover(context: Context): List<DiscoveredApp> {
        val packageManager = context.packageManager
        val launcherPackages = if (launcherAppsOnly) {
            packageManager.queryIntentActivities(
                Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER),
                PackageManager.MATCH_DEFAULT_ONLY,
            ).map { it.activityInfo.packageName }.toSet()
        } else {
            emptySet()
        }

        val applications = packageManager.getInstalledApplications(PackageManager.GET_META_DATA)
        val results = mutableListOf<DiscoveredApp>()
        for (appInfo in applications) {
            if ((appInfo.flags and ApplicationInfo.FLAG_SYSTEM) != 0 &&
                (appInfo.flags and ApplicationInfo.FLAG_UPDATED_SYSTEM_APP) == 0 &&
                launcherAppsOnly
            ) {
                // Keep updated system apps; skip untouched system internals when filtering launchers.
            }
            if ((appInfo.flags and ApplicationInfo.FLAG_INSTALLED) == 0) {
                continue
            }
            if (launcherAppsOnly && appInfo.packageName !in launcherPackages) {
                continue
            }

            val label = packageManager.getApplicationLabel(appInfo).toString().trim()
            if (label.isEmpty()) {
                continue
            }

            val versionName = try {
                packageManager.getPackageInfo(appInfo.packageName, 0).versionName
            } catch (_: PackageManager.NameNotFoundException) {
                null
            }

            val iconResult = buildIconPayload(packageManager.getApplicationIcon(appInfo))
            results.add(
                DiscoveredApp(
                    applicationName = label,
                    identifier = "$ANDROID_PACKAGE_PREFIX${appInfo.packageName}",
                    matchType = MATCH_TYPE_PACKAGE,
                    versionName = versionName,
                    iconHash = iconResult?.first,
                    iconPng = iconResult?.second,
                ),
            )
        }

        return results.sortedBy { it.applicationName.lowercase() }
    }

    private fun buildIconPayload(drawable: Drawable): Pair<String, ByteArray>? {
        val bitmap = drawableToBitmap(drawable) ?: return null
        val scaled = Bitmap.createScaledBitmap(bitmap, ICON_SIZE, ICON_SIZE, true)
        if (scaled !== bitmap) {
            bitmap.recycle()
        }
        val output = ByteArrayOutputStream()
        if (!scaled.compress(Bitmap.CompressFormat.PNG, 100, output)) {
            scaled.recycle()
            return null
        }
        scaled.recycle()
        val bytes = output.toByteArray()
        val hash = sha256Hex(bytes)
        return hash to bytes
    }

    private fun drawableToBitmap(drawable: Drawable): Bitmap? {
        if (drawable is BitmapDrawable && drawable.bitmap != null) {
            return drawable.bitmap
        }
        val width = if (drawable.intrinsicWidth > 0) drawable.intrinsicWidth else ICON_SIZE
        val height = if (drawable.intrinsicHeight > 0) drawable.intrinsicHeight else ICON_SIZE
        val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bitmap)
        drawable.setBounds(0, 0, canvas.width, canvas.height)
        drawable.draw(canvas)
        return bitmap
    }

    fun sha256Hex(bytes: ByteArray): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(bytes)
        return digest.joinToString("") { "%02x".format(it) }
    }

    fun iconBase64(bytes: ByteArray): String {
        return Base64.encodeToString(bytes, Base64.NO_WRAP)
    }
}
