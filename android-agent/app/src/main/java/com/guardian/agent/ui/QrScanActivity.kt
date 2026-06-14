package com.guardian.agent.ui

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import com.guardian.agent.protocol.PairingQrPayload

class QrScanActivity : AppCompatActivity() {
    private val scanner = registerForActivityResult(ScanContract()) { result ->
        val payload = result.contents?.let { PairingQrPayload.parse(it) }
        if (payload == null) {
            setResult(Activity.RESULT_CANCELED)
        } else {
            val data = Intent()
                .putExtra(EXTRA_SERVER_URL, payload.serverUrl)
            if (!payload.registrationToken.isNullOrBlank()) {
                data.putExtra(EXTRA_REGISTRATION_TOKEN, payload.registrationToken)
            }
            setResult(Activity.RESULT_OK, data)
        }
        finish()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val options = ScanOptions().apply {
            setDesiredBarcodeFormats(ScanOptions.QR_CODE)
            setPrompt("Scan the Guardian server pairing QR code")
            setBeepEnabled(false)
            setOrientationLocked(true)
        }
        scanner.launch(options)
    }

    companion object {
        const val EXTRA_SERVER_URL = "server_url"
        const val EXTRA_REGISTRATION_TOKEN = "registration_token"
    }
}
