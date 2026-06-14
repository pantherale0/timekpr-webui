package com.guardian.agent.update

import android.app.PendingIntent
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build
import android.os.UserManager
import android.util.Log
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import com.guardian.agent.admin.DeviceOwnerProvisioner
import com.guardian.agent.admin.GuardianDeviceAdminReceiver
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

class AppInstallWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {

    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .build()

    override suspend fun doWork(): Result {
        val packageName = inputData.getString(KEY_PACKAGE_NAME)?.trim().orEmpty()
        val apkUrl = inputData.getString(KEY_APK_URL)?.trim().orEmpty()
        val sha256Checksum = inputData.getString(KEY_CHECKSUM)?.trim().orEmpty()

        if (packageName.isEmpty() || apkUrl.isEmpty()) {
            Log.e(TAG, "Invalid work arguments: package=$packageName, url=$apkUrl")
            return Result.failure()
        }

        Log.i(TAG, "Starting force-install task: package=$packageName, url=$apkUrl")

        val cacheDir = File(applicationContext.cacheDir, "app_installs").apply { mkdirs() }
        val apkFile = File(cacheDir, "$packageName.apk")

        var isRestrictionCleared = false
        try {
            downloadFile(apkUrl, apkFile)

            if (sha256Checksum.isNotEmpty()) {
                val fileHash = computeSha256(apkFile)
                if (!fileHash.equals(sha256Checksum, ignoreCase = true)) {
                    Log.e(TAG, "SHA-256 validation failed for $packageName. Configured: $sha256Checksum, Actual: $fileHash")
                    apkFile.delete()
                    return Result.failure()
                }
            }

            isRestrictionCleared = temporarilyAllowInstall()

            installApk(apkFile, packageName)

            Log.i(TAG, "Silent installation session created successfully for $packageName")
            return Result.success()

        } catch (e: Exception) {
            Log.e(TAG, "Failed to force install package $packageName", e)
            apkFile.delete()
            return Result.retry()
        } finally {
            if (isRestrictionCleared) {
                restoreInstallRestriction()
            }
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

    private fun computeSha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(8192)
            var bytesRead = input.read(buffer)
            while (bytesRead != -1) {
                digest.update(buffer, 0, bytesRead)
                bytesRead = input.read(buffer)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    private fun temporarilyAllowInstall(): Boolean {
        if (!DeviceOwnerProvisioner.isDeviceOrProfileOwner(applicationContext)) return false
        val dpm = applicationContext.getSystemService(android.app.admin.DevicePolicyManager::class.java) ?: return false
        val admin = ComponentName(applicationContext, GuardianDeviceAdminReceiver::class.java)
        if (!dpm.isAdminActive(admin)) return false

        try {
            val userManager = applicationContext.getSystemService(UserManager::class.java)
            if (userManager != null && userManager.hasUserRestriction(UserManager.DISALLOW_INSTALL_APPS)) {
                dpm.clearUserRestriction(admin, UserManager.DISALLOW_INSTALL_APPS)
                return true
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to temporarily clear DISALLOW_INSTALL_APPS", e)
        }
        return false
    }

    private fun restoreInstallRestriction() {
        if (!DeviceOwnerProvisioner.isDeviceOrProfileOwner(applicationContext)) return
        val dpm = applicationContext.getSystemService(android.app.admin.DevicePolicyManager::class.java) ?: return
        val admin = ComponentName(applicationContext, GuardianDeviceAdminReceiver::class.java)
        if (!dpm.isAdminActive(admin)) return

        try {
            dpm.addUserRestriction(admin, UserManager.DISALLOW_INSTALL_APPS)
        } catch (e: Exception) {
            Log.w(TAG, "Failed to restore DISALLOW_INSTALL_APPS", e)
        }
    }

    private fun installApk(apkFile: File, packageName: String) {
        val packageManager = applicationContext.packageManager
        val packageInstaller = packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                setAppPackageName(packageName)
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
                DeviceOwnerProvisioner.isDeviceOrProfileOwner(applicationContext)
            ) {
                setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
            }
        }

        val sessionId = packageInstaller.createSession(params)
        val session = packageInstaller.openSession(sessionId)
        try {
            apkFile.inputStream().use { input ->
                session.openWrite("app-force-install", 0, apkFile.length()).use { output ->
                    input.copyTo(output)
                    session.fsync(output)
                }
            }

            val callbackIntent = Intent(applicationContext, AgentUpdateReceiver::class.java).apply {
                action = AgentUpdateReceiver.ACTION_INSTALL_COMPLETE
                putExtra(PackageInstaller.EXTRA_PACKAGE_NAME, packageName)
            }
            val pendingFlags = PendingIntent.FLAG_UPDATE_CURRENT or
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                    PendingIntent.FLAG_MUTABLE
                } else {
                    0
                }
            val pendingIntent = PendingIntent.getBroadcast(
                applicationContext,
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

    companion object {
        private const val TAG = "AppInstallWorker"
        private const val KEY_PACKAGE_NAME = "package_name"
        private const val KEY_APK_URL = "apk_url"
        private const val KEY_CHECKSUM = "checksum"

        fun enqueue(context: Context, packageName: String, apkUrl: String, checksum: String?) {
            val constraints = androidx.work.Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .build()
            val workRequest = OneTimeWorkRequestBuilder<AppInstallWorker>()
                .setConstraints(constraints)
                .setInputData(
                    workDataOf(
                        KEY_PACKAGE_NAME to packageName,
                        KEY_APK_URL to apkUrl,
                        KEY_CHECKSUM to (checksum ?: ""),
                    )
                )
                .build()

            val uniqueName = "force_install_$packageName"
            WorkManager.getInstance(context.applicationContext).enqueueUniqueWork(
                uniqueName,
                ExistingWorkPolicy.KEEP,
                workRequest,
            )
        }
    }
}
