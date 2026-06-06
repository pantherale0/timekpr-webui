package com.timekpr.agent.update

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class ApkSignatureVerifierTest {
    @Test
    fun encodeUrlSafeBase64_matchesProvisioningFormat() {
        val raw = ByteArray(32) { index -> index.toByte() }
        val encoded = ApkSignatureVerifier.encodeUrlSafeBase64(raw)
        assertFalse(encoded.contains('='))
        assertFalse(encoded.contains('+'))
        assertFalse(encoded.contains('/'))
    }

    @Test
    fun hexDigestToChecksum_convertsApksignerHexDigest() {
        val hex = "ab" + "cd".repeat(31)
        val expected = ApkSignatureVerifier.encodeUrlSafeBase64(
            ByteArray(32) { index ->
                if (index == 0) 0xAB.toByte() else 0xCD.toByte()
            },
        )
        assertEquals(expected, ApkSignatureVerifier.hexDigestToChecksum(hex))
    }

    @Test
    fun hexDigestToChecksum_stripsColonSeparators() {
        val hexNoColon = "ab" + "cd".repeat(31)
        val hexWithColon = hexNoColon.chunked(2).joinToString(":")
        assertEquals(
            ApkSignatureVerifier.hexDigestToChecksum(hexNoColon),
            ApkSignatureVerifier.hexDigestToChecksum(hexWithColon),
        )
    }
}

class AgentUpdateRequestTest {
    @Test
    fun resolvedApkUrl_fallsBackToGitHubReleaseAsset() {
        val request = AgentUpdateRequest(
            targetVersion = "v1.2.3",
            apkUrl = null,
            signatureChecksum = "abc",
            updateAvailable = false,
        )

        assertEquals(
            "https://github.com/pantherale0/timekpr-webui/releases/download/v1.2.3/timekpr-android-agent-v1.2.3.apk",
            request.resolvedApkUrl(),
        )
    }

    @Test
    fun resolvedApkUrl_prefersServerProvidedUrl() {
        val request = AgentUpdateRequest(
            targetVersion = "v1.2.3",
            apkUrl = "https://example.com/agent.apk",
            signatureChecksum = "abc",
            updateAvailable = true,
        )

        assertEquals("https://example.com/agent.apk", request.resolvedApkUrl())
    }

    @Test
    fun resolvedChecksumUrl_fallsBackToGitHubReleaseAsset() {
        val request = AgentUpdateRequest(
            targetVersion = "1.2.3",
            apkUrl = "https://example.com/agent.apk",
            signatureChecksum = null,
            updateAvailable = false,
        )

        assertEquals(
            "https://github.com/pantherale0/timekpr-webui/releases/download/v1.2.3/timekpr-android-agent-v1.2.3.signature-checksum",
            request.resolvedChecksumUrl(),
        )
    }
}
