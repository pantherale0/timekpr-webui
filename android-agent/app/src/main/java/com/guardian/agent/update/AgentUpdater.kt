package com.guardian.agent.update

import android.app.PendingIntent
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInfo
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.os.Build
import android.os.UserManager
import android.util.Log
import com.guardian.agent.admin.DeviceOwnerProvisioner
import com.guardian.agent.admin.GuardianDeviceAdminReceiver
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.util.concurrent.TimeUnit

class AgentUpdater(private val context: Context) {
    private val packageManager = context.packageManager
    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .build()

    fun performUpdate(request: AgentUpdateRequest): UpdateResult {
        val targetVersion = request.targetVersion.trim()
        if (targetVersion.isEmpty()) {
            return UpdateResult.Failure("Missing target version")
        }

        val apkUrl = request.resolvedApkUrl()
            ?: return UpdateResult.Failure("No APK download URL available")

        val checksum = resolveChecksum(request)
            ?: return UpdateResult.Failure("No APK signature checksum available")

        if (!canInstallPackages()) {
            return UpdateResult.Failure("Install permission not granted for package updates")
        }

        val updateDir = File(context.cacheDir, UPDATE_DIR).apply { mkdirs() }
        val safeTag = targetVersion.replace(Regex("[^A-Za-z0-9._-]"), "_")
        val apkFile = File(updateDir, "guardian-android-agent-$safeTag.apk")

        try {
            downloadFile(apkUrl, apkFile)
        } catch (error: Exception) {
            Log.e(TAG, "Failed to download agent update", error)
            return UpdateResult.Failure("Download failed: ${error.message ?: "unknown error"}")
        }

        if (!ApkSignatureVerifier.verifyApkChecksum(apkFile, checksum, packageManager)) {
            apkFile.delete()
            return UpdateResult.Failure("APK signature verification failed")
        }

        val downloadedVersionCode = readArchiveVersionCode(apkFile)
        val installedVersionCode = installedVersionCode()
        if (downloadedVersionCode != null && installedVersionCode != null &&
            downloadedVersionCode <= installedVersionCode
        ) {
            Log.i(TAG, "Downloaded APK versionCode $downloadedVersionCode is not newer than installed $installedVersionCode")
            return UpdateResult.AlreadyCurrent
        }

        return try {
            installApk(apkFile)
            UpdateResult.InstallStarted
        } catch (error: Exception) {
            Log.e(TAG, "Failed to install agent update", error)
            UpdateResult.Failure("Install failed: ${error.message ?: "unknown error"}")
        }
    }

    private fun resolveChecksum(request: AgentUpdateRequest): String? {
        val direct = request.signatureChecksum?.trim().orEmpty()
        if (direct.isNotEmpty()) {
            return direct
        }
        val checksumUrl = request.resolvedChecksumUrl() ?: return null
        return try {
            val response = httpClient.newCall(Request.Builder().url(checksumUrl).build()).execute()
            response.use {
                if (!it.isSuccessful) {
                    return null
                }
                it.body?.string()?.trim()?.ifBlank { null }
            }
        } catch (error: Exception) {
            Log.w(TAG, "Failed to fetch signature checksum from $checksumUrl", error)
            null
        }
    }

    private fun downloadFile(url: String, destination: File) {
        val response = httpClient.newCall(Request.Builder().url(url).build()).execute()
        response.use {
            if (!it.isSuccessful) {
                throw IllegalStateException("HTTP ${it.code} for $url")
            }
            val body = it.body ?: throw IllegalStateException("Empty response body for $url")
            destination.outputStream().use { output -> body.byteStream().copyTo(output) }
        }
    }

    private fun canInstallPackages(): Boolean {
        if (DeviceOwnerProvisioner.isDeviceOrProfileOwner(context)) {
            return true
        }
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.packageManager.canRequestPackageInstalls()
        } else {
            true
        }
    }

    private fun readArchiveVersionCode(apkFile: File): Int? {
        val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            PackageManager.GET_SIGNING_CERTIFICATES
        } else {
            @Suppress("DEPRECATION")
            PackageManager.GET_SIGNATURES
        }
        val archiveInfo = packageManager.getPackageArchiveInfo(apkFile.absolutePath, flags)
            ?: return null
        archiveInfo.applicationInfo?.sourceDir = apkFile.absolutePath
        archiveInfo.applicationInfo?.publicSourceDir = apkFile.absolutePath
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            archiveInfo.longVersionCode.toInt()
        } else {
            @Suppress("DEPRECATION")
            archiveInfo.versionCode
        }
    }

    private fun installedVersionCode(): Int? {
        return try {
            val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                0
            } else {
                @Suppress("DEPRECATION")
                0
            }
            val info: PackageInfo = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                packageManager.getPackageInfo(context.packageName, PackageManager.PackageInfoFlags.of(flags.toLong()))
            } else {
                @Suppress("DEPRECATION")
                packageManager.getPackageInfo(context.packageName, flags)
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                info.longVersionCode.toInt()
            } else {
                @Suppress("DEPRECATION")
                info.versionCode
            }
        } catch (_: PackageManager.NameNotFoundException) {
            null
        }
    }

    private fun installApk(apkFile: File) {
        temporarilyAllowSelfInstall()

        val packageInstaller = packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                setAppPackageName(context.packageName)
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
                DeviceOwnerProvisioner.isDeviceOrProfileOwner(context)
            ) {
                setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
            }
        }

        val sessionId = packageInstaller.createSession(params)
        val session = packageInstaller.openSession(sessionId)
        try {
            apkFile.inputStream().use { input ->
                session.openWrite("guardian-update", 0, apkFile.length()).use { output ->
                    input.copyTo(output)
                    session.fsync(output)
                }
            }

            val callbackIntent = Intent(context, AgentUpdateReceiver::class.java).apply {
                action = AgentUpdateReceiver.ACTION_INSTALL_COMPLETE
            }
            val pendingFlags = PendingIntent.FLAG_UPDATE_CURRENT or
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                    PendingIntent.FLAG_MUTABLE
                } else {
                    0
                }
            val pendingIntent = PendingIntent.getBroadcast(
                context,
                sessionId,
                callbackIntent,
                pendingFlags,
            )
            session.commit(pendingIntent.intentSender)
        } catch (error: Exception) {
            session.abandon()
            throw error
        } finally {
            session.close()
        }
    }

    private fun temporarilyAllowSelfInstall() {
        if (!DeviceOwnerProvisioner.isDeviceOrProfileOwner(context)) {
            return
        }
        val dpm = context.getSystemService(android.app.admin.DevicePolicyManager::class.java) ?: return
        val admin = ComponentName(context, GuardianDeviceAdminReceiver::class.java)
        if (!dpm.isAdminActive(admin)) {
            return
        }
        try {
            dpm.clearUserRestriction(admin, UserManager.DISALLOW_INSTALL_APPS)
        } catch (error: Exception) {
            Log.w(TAG, "Failed to clear DISALLOW_INSTALL_APPS for self-update", error)
        }
    }

    sealed class UpdateResult {
        data object InstallStarted : UpdateResult()
        data object AlreadyCurrent : UpdateResult()
        data class Failure(val message: String) : UpdateResult()
    }

    companion object {
        private const val TAG = "AgentUpdater"
        private const val UPDATE_DIR = "guardian-update"
    }
}
