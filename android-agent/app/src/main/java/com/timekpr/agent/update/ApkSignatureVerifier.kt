package com.timekpr.agent.update

import android.content.pm.PackageManager
import android.content.pm.Signature
import android.os.Build
import java.io.File
import java.security.MessageDigest

object ApkSignatureVerifier {
    /**
     * Compute the URL-safe base64 SHA-256 checksum of an APK signing certificate.
     * Matches [scripts/android-signature-checksum.sh] / Android MDM provisioning format.
     */
    fun computeCertificateChecksum(signature: Signature): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(signature.toByteArray())
        return encodeUrlSafeBase64(digest)
    }

    fun encodeUrlSafeBase64(raw: ByteArray): String {
        val alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        val out = StringBuilder()
        var i = 0
        val len = raw.size
        while (i < len) {
            val b0 = raw[i++].toInt() and 0xFF
            if (i < len) {
                val b1 = raw[i++].toInt() and 0xFF
                if (i < len) {
                    val b2 = raw[i++].toInt() and 0xFF
                    out.append(alphabet[b0 ushr 2])
                    out.append(alphabet[((b0 and 3) shl 4) or (b1 ushr 4)])
                    out.append(alphabet[((b1 and 15) shl 2) or (b2 ushr 6)])
                    out.append(alphabet[b2 and 63])
                } else {
                    out.append(alphabet[b0 ushr 2])
                    out.append(alphabet[((b0 and 3) shl 4) or (b1 ushr 4)])
                    out.append(alphabet[(b1 and 15) shl 2])
                }
            } else {
                out.append(alphabet[b0 ushr 2])
                out.append(alphabet[(b0 and 3) shl 4])
            }
        }
        return out.toString()
    }

    fun hexDigestToChecksum(hexDigest: String): String {
        val normalized = hexDigest.lowercase().replace(":", "").trim()
        require(normalized.length == 64) { "Invalid certificate SHA-256 digest length" }
        val raw = ByteArray(32) { index ->
            normalized.substring(index * 2, index * 2 + 2).toInt(16).toByte()
        }
        return encodeUrlSafeBase64(raw)
    }

    fun verifyApkChecksum(apkFile: File, expectedChecksum: String, packageManager: PackageManager): Boolean {
        val expected = expectedChecksum.trim()
        if (expected.isEmpty()) {
            return false
        }
        val actual = readApkCertificateChecksum(apkFile, packageManager) ?: return false
        return actual == expected
    }

    fun readApkCertificateChecksum(apkFile: File, packageManager: PackageManager): String? {
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

        val signature = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            val signingInfo = archiveInfo.signingInfo ?: return null
            if (signingInfo.hasMultipleSigners()) {
                signingInfo.apkContentsSigners?.firstOrNull()
            } else {
                signingInfo.signingCertificateHistory?.firstOrNull()
            }
        } else {
            @Suppress("DEPRECATION")
            archiveInfo.signatures?.firstOrNull()
        } ?: return null

        return computeCertificateChecksum(signature)
    }
}
